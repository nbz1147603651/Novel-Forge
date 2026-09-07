from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest

from novel_forge.app_service.authoring_foundation import create_authoring_proposal
from novel_forge.app_service.authoring_proposals import apply_proposal
from novel_forge.app_service.character_artifacts import (
    remove_relationship_edge,
    save_narrative_character,
    write_character_bible,
    write_relationship_edge,
)
from novel_forge.core.authoring import (
    AuthoringPolicy,
    AuthoringProposalDecision,
    AuthoringProposalRequest,
)
from novel_forge.core.schemas import CharacterBible, StoryBible, StoryOutline, StorySpec
from novel_forge.core.schemas.outline import NarrativeBlueprint
from novel_forge.core.user_intent import build_persisted_user_intent_card
from novel_forge.persistence.authoring_proposals import ProposalStore
from novel_forge.persistence.authoring_store import (
    AuthoringDeniedError,
    AuthoringStore,
    story_input_version,
)
from novel_forge.persistence.filesystem import (
    FileSystemStorage,
    atomic_write_json,
    atomic_write_text,
)
from novel_forge.persistence.foundation_guard import SYNC_PATH
from novel_forge.persistence.models import ProjectLayout
from novel_forge.persistence.planning_revision import PlanningRevision
from novel_forge.workspace.authoring_proposals import decide_proposal
from tests.unit.test_source_artifacts import _source_payloads


@pytest.fixture
def foundation(tmp_path):
    storage = FileSystemStorage(tmp_path)
    root = storage.ensure_project_dir("book")
    layout = ProjectLayout(root)
    payloads = _source_payloads()
    for chapter in payloads["outline"]["chapters"]:
        chapter["goal"] = "发现魂玉旧案的新线索"
    for filename, model, payload in [
        ("spec.json", StorySpec, payloads["spec"]),
        ("story_bible.json", StoryBible, payloads["story_bible"]),
        ("character_bible.json", CharacterBible, payloads["character_bible"]),
        ("outline.json", StoryOutline, payloads["outline"]),
        ("plans/narrative_blueprint.json", NarrativeBlueprint, payloads["blueprint"]),
    ]:
        storage.save_json(root / filename, model.model_validate(payload).model_dump(mode="json"))
    for filename, key in [
        ("plans/narrative_contract.json", "narrative_contract"),
        ("plans/chapter_contracts.json", "chapter_contracts"),
        ("narrative_state/entity_graph.json", "entity_graph"),
        ("states/init_v2/character_system.json", "character_system"),
    ]:
        storage.save_json(root / filename, payloads[key])
    atomic_write_text(layout.chapter_path(1), "作者已经归档的第一章。")
    atomic_write_json(
        layout.states_dir / "init_request_meta.json",
        {
            "request": {
                "init_input": {"ending_style": "开放式结局"},
                "generation_options": {},
            }
        },
    )
    store = AuthoringStore(root)
    policy = store.set_policy(
        AuthoringPolicy(mode="authorized_auto", end_chapter=100), expected_version=0
    )
    store.start(expected_version=policy.version, input_version=story_input_version(root))
    return SimpleNamespace(storage=storage), layout


def decide(view, decision="accept"):
    return AuthoringProposalDecision(
        decision=decision,
        candidate_version=view.candidate_version,
        input_version=view.input_version,
        policy_version=view.policy_version,
    )


def request_for(runtime, layout, artifact):
    name, key, value = {
        "spec": ("spec.json", "ending_style", "保留问题，不揭示唯一答案"),
        "world": ("story_bible.json", "era", "架空古代晚春"),
        "characters": ("character_bible.json", "", ""),
        "blueprint": (
            "plans/narrative_blueprint.json",
            "synopsis",
            "玄昱追查魂玉旧案，保留开放结局。",
        ),
    }[artifact]
    payload = runtime.storage.load_json(layout.root / name)
    if artifact == "characters":
        payload["characters"][0]["voice"] = "克制，但在亲友面前稍带幽默"
    else:
        payload[key] = value
    return name, AuthoringProposalRequest(
        command="revise_foundation",
        chapter_number=1,
        foundation_artifact=artifact,
        candidate=json.dumps(payload, ensure_ascii=False),
    )


@pytest.mark.parametrize("artifact", ["spec", "world", "characters", "blueprint"])
async def test_foundation_is_candidate_until_exact_approval_and_preserves_prose(
    foundation, artifact
):
    runtime, layout = foundation
    name, request = request_for(runtime, layout, artifact)
    before = (layout.root / name).read_bytes()
    text = layout.chapter_path(1).read_bytes()
    view = await create_authoring_proposal(runtime, "book", request)
    assert (layout.root / name).read_bytes() == before
    assert view.action == "revise" and view.lock_conflicts
    assert view.affected_chapters == ([] if artifact == "characters" else [1])
    with pytest.raises(AuthoringDeniedError):
        await apply_proposal(runtime, None, "book", view.id)
    decide_proposal(layout.root, view.id, decide(view))
    result = await apply_proposal(runtime, None, "book", view.id)
    assert result.status == "applied"
    assert (layout.root / name).read_bytes() != before
    assert layout.chapter_path(1).read_bytes() == text
    assert runtime.storage.load_json(layout.root / SYNC_PATH)["status"] == "completed"
    atomic_write_text(layout.chapter_path(2), "之后完成的第二章。")
    from novel_forge.persistence.project_staleness import revision_stale_chapters

    assert revision_stale_chapters(runtime.storage, layout) == (
        set() if artifact == "characters" else {1}
    )
    assert (await apply_proposal(runtime, None, "book", view.id)).status == "applied"
    assert layout.chapter_path(2).read_text() == "之后完成的第二章。"


async def test_character_voice_scope_does_not_force_historical_rework(foundation):
    runtime, layout = foundation
    outline = runtime.storage.load_json(layout.outline_path)
    outline["total_chapters"] = 10
    atomic_write_json(layout.outline_path, outline)
    _, request = request_for(runtime, layout, "characters")
    view = await create_authoring_proposal(runtime, "book", request)
    assert view.affected_chapters == list(range(2, 11))
    assert "仅向后" in view.risks[0]
    assert layout.chapter_path(1).read_text() == "作者已经归档的第一章。"


async def test_character_rename_retains_historical_impact(foundation):
    runtime, layout = foundation
    _, request = request_for(runtime, layout, "characters")
    payload = json.loads(request.candidate)
    payload["characters"][0]["name"] = "新的正式姓名"
    view = await create_authoring_proposal(
        runtime, "book", request.model_copy(update={"candidate": json.dumps(payload)})
    )
    assert view.affected_chapters == [1]
    assert "全书" in view.risks[0]


async def test_only_approved_changed_spec_fields_override_original_intent(foundation):
    runtime, layout = foundation
    _, request = request_for(runtime, layout, "spec")
    view = await create_authoring_proposal(runtime, "book", request)
    meta_path = layout.states_dir / "init_request_meta.json"
    assert "author_input_overrides" not in runtime.storage.load_json(meta_path)
    decide_proposal(layout.root, view.id, decide(view))
    await apply_proposal(runtime, None, "book", view.id)
    meta = runtime.storage.load_json(meta_path)
    assert meta["request"]["init_input"]["ending_style"] == "开放式结局"
    card = build_persisted_user_intent_card(meta, runtime.storage.load_json(layout.spec_path))
    assert {i["field"] for i in card["explicit_intents"]} == {"ending_style"}
    assert card["explicit_intents"][0]["value"] == "保留问题，不揭示唯一答案"
    assert card["explicit_intents"][0]["source"] == "author_approved_proposal"


async def test_input_race_and_rejection_do_not_publish_or_regenerate(foundation):
    runtime, layout = foundation
    _, request = request_for(runtime, layout, "spec")
    view = await create_authoring_proposal(runtime, "book", request)
    decide_proposal(layout.root, view.id, decide(view))
    atomic_write_text(layout.chapter_path(1), "另一个窗口的修改。")
    with pytest.raises(AuthoringDeniedError, match="过期"):
        await apply_proposal(runtime, None, "book", view.id)
    rejected = decide_proposal(layout.root, view.id, decide(view, "reject"))
    assert rejected.status == "rejected"
    assert runtime.storage.load_json(layout.spec_path)["ending_style"] == ""
    assert len(list((layout.root / ".planning_revisions").iterdir())) == 1


async def test_committed_but_failed_mirror_blocks_dispatch_and_recovers_after_pause(
    foundation, monkeypatch
):
    from novel_forge.app_service import character_artifacts
    from novel_forge.app_service.authoring import authoring_session

    runtime, layout = foundation
    _, request = request_for(runtime, layout, "characters")
    view = await create_authoring_proposal(runtime, "book", request)
    decide_proposal(layout.root, view.id, decide(view))
    sync = character_artifacts._sync_character_artifacts_to_story_kernel
    monkeypatch.setattr(
        character_artifacts,
        "_sync_character_artifacts_to_story_kernel",
        lambda **kw: (_ for _ in ()).throw(OSError("mirror failed")),
    )
    with pytest.raises(OSError, match="mirror failed"):
        await apply_proposal(runtime, None, "book", view.id)
    store = AuthoringStore(layout.root)
    with pytest.raises(AuthoringDeniedError, match="投影"):
        store.require("generate", 1)
    assert "投影" in authoring_session(layout.root, "book").waiting_reason
    assert ProposalStore(layout.root).views()[0].application_result["recoverable_receipt"]
    store.stop()
    monkeypatch.setattr(character_artifacts, "_sync_character_artifacts_to_story_kernel", sync)
    result = await apply_proposal(runtime, None, "book", view.id)
    assert result.status == "applied" and store.policy().stopped
    assert runtime.storage.load_json(layout.root / SYNC_PATH)["status"] == "completed"


async def test_recovery_never_replays_over_another_author_edit(foundation, monkeypatch):
    from novel_forge.app_service import character_artifacts

    runtime, layout = foundation
    _, request = request_for(runtime, layout, "characters")
    view = await create_authoring_proposal(runtime, "book", request)
    decide_proposal(layout.root, view.id, decide(view))
    monkeypatch.setattr(
        character_artifacts,
        "_sync_character_artifacts_to_story_kernel",
        lambda **kw: (_ for _ in ()).throw(OSError("offline")),
    )
    with pytest.raises(OSError):
        await apply_proposal(runtime, None, "book", view.id)
    payload = runtime.storage.load_json(layout.characters_path)
    payload["characters"][0]["voice"] = "人工改稿"
    atomic_write_json(layout.characters_path, payload)
    with pytest.raises(ValueError, match="未重放"):
        await apply_proposal(runtime, None, "book", view.id)
    assert runtime.storage.load_json(layout.characters_path)["characters"][0]["voice"] == "人工改稿"


def test_old_character_editor_becomes_proposal_but_direct_writers_cannot_bypass(foundation):
    runtime, layout = foundation
    before = layout.characters_path.read_bytes()
    result = save_narrative_character(
        runtime.storage,
        "book",
        character_id="char_xuanyu",
        profile_patch={"voice": "微带幽默"},
        expected_revision=hashlib.sha256(before).hexdigest(),
    )
    assert result.proposal_id
    assert layout.characters_path.read_bytes() == before
    for call in [
        lambda: write_character_bible(
            layout.root, runtime.storage.load_json(layout.characters_path)
        ),
        lambda: write_relationship_edge(layout.root, "甲", "乙", "alliance", "旧友"),
        lambda: remove_relationship_edge(layout.root, "甲", "乙"),
    ]:
        with pytest.raises(AuthoringDeniedError, match="专项提案"):
            call()
    assert not (layout.states_dir / "init_v2/character_relationship_matrix.json").exists()


async def test_foundation_cannot_extend_target_or_publish_arbitrary_paths(foundation):
    runtime, layout = foundation
    _, request = request_for(runtime, layout, "spec")
    payload = json.loads(request.candidate)
    payload["length_target"] += 1000
    with pytest.raises(ValueError, match="总目标"):
        await create_authoring_proposal(
            runtime, "book", request.model_copy(update={"candidate": json.dumps(payload)})
        )
    revision = PlanningRevision(layout.root, "book", purpose="foundation")
    atomic_write_json(revision.project / "authoring_policy.json", {"mode": "authorized_auto"})
    with pytest.raises(ValueError, match="protected"):
        revision.changes()


async def test_editor_api_returns_candidate_not_saved(foundation):
    from novel_forge.api.routes.engine import (
        _SaveNarrativeCharacterBody,
        _SaveNarrativeSubplotsBody,
        engine_save_narrative_character,
        engine_save_narrative_subplots,
    )

    runtime, layout = foundation
    result = await engine_save_narrative_character(
        _SaveNarrativeCharacterBody(
            project_id="book",
            character_id="char_xuanyu",
            profile={"voice": "自然随和"},
            expected_revision=hashlib.sha256(layout.characters_path.read_bytes()).hexdigest(),
        ),
        runtime.storage,
    )
    assert result.status == "candidate" and result.proposal_id
    old_blueprint = layout.blueprint_path.read_bytes()
    result = await engine_save_narrative_subplots(
        _SaveNarrativeSubplotsBody(
            project_id="book",
            subplots=[{"name": "失物追查", "description": "追查线索"}],
            expected_revision=hashlib.sha256(old_blueprint).hexdigest(),
        ),
        runtime.storage,
    )
    assert result.status == "candidate" and result.proposal_id
    assert layout.blueprint_path.read_bytes() == old_blueprint


async def test_last_commit_marker_failure_is_receipt_only_recovery(foundation, monkeypatch):
    from novel_forge.app_service import authoring_foundation, character_artifacts

    runtime, layout = foundation
    _, request = request_for(runtime, layout, "characters")
    view = await create_authoring_proposal(runtime, "book", request)
    decide_proposal(layout.root, view.id, decide(view))
    write = authoring_foundation.atomic_write_json
    sync = character_artifacts._sync_character_artifacts_to_story_kernel
    calls = []

    def counted(**kw):
        calls.append("sync")
        return sync(**kw)

    def fail_marker(path, data):
        if path == layout.root / SYNC_PATH and data.get("status") == "completed":
            raise OSError("last marker")
        return write(path, data)

    monkeypatch.setattr(character_artifacts, "_sync_character_artifacts_to_story_kernel", counted)
    monkeypatch.setattr(authoring_foundation, "atomic_write_json", fail_marker)
    with pytest.raises(OSError, match="last marker"):
        await apply_proposal(runtime, None, "book", view.id)
    with pytest.raises(AuthoringDeniedError, match="已正式提交"):
        decide_proposal(layout.root, view.id, decide(view, "reject"))
    monkeypatch.setattr(authoring_foundation, "atomic_write_json", write)
    await apply_proposal(runtime, None, "book", view.id)
    assert calls == ["sync"]
    assert runtime.storage.load_json(layout.root / SYNC_PATH)["status"] == "completed"


async def test_publication_fault_rolls_back_foundation_and_preserves_history(
    foundation, monkeypatch
):
    from novel_forge.persistence import planning_revision

    runtime, layout = foundation
    _, request = request_for(runtime, layout, "spec")
    view = await create_authoring_proposal(runtime, "book", request)
    decide_proposal(layout.root, view.id, decide(view))
    old = layout.spec_path.read_bytes()
    write = planning_revision.atomic_write_text
    failed = False

    def crash(path, text):
        nonlocal failed
        if path == layout.root / SYNC_PATH and not failed:
            failed = True
            raise OSError("publication fault")
        return write(path, text)

    monkeypatch.setattr(planning_revision, "atomic_write_text", crash)
    with pytest.raises(OSError, match="publication fault"):
        await apply_proposal(runtime, None, "book", view.id)
    assert layout.spec_path.read_bytes() == old
    assert not (layout.root / SYNC_PATH).exists()
    assert layout.chapter_path(1).read_text() == "作者已经归档的第一章。"
    assert (layout.root / ".planning_revisions").is_dir()


async def test_world_merge_preserves_later_canon_and_extracted_rules(foundation):
    from novel_forge.story_kernel.schemas import Entity, StoryKernel, WorldRule
    from novel_forge.story_kernel.store import StoryKernelStore

    runtime, layout = foundation
    store = StoryKernelStore(layout.story_kernel_db_path)
    try:
        await store.init_db()
        await store.save_kernel(
            StoryKernel(
                project_id="book",
                current_chapter=1,
                entities=[Entity(entity_id="later", name="正文中新发现的物品", entity_type="item")],
                world_rules=[WorldRule(rule_id="wr_later", content="正文确立的补充规则")],
            )
        )
    finally:
        await store.close()
    _, request = request_for(runtime, layout, "world")
    view = await create_authoring_proposal(runtime, "book", request)
    decide_proposal(layout.root, view.id, decide(view))
    await apply_proposal(runtime, None, "book", view.id)
    store = StoryKernelStore(layout.story_kernel_db_path)
    try:
        await store.init_db()
        kernel = await store.load_kernel("book")
        assert kernel.current_chapter == 1
        assert kernel.entities[0].entity_id == "later"
        assert any(rule.rule_id == "wr_later" for rule in kernel.world_rules)
    finally:
        await store.close()


async def test_no_archived_chapters_at_proposal_time_does_not_taint_later_chapters(foundation):
    from novel_forge.persistence.project_staleness import revision_stale_chapters

    runtime, layout = foundation
    layout.chapter_path(1).unlink()
    _, request = request_for(runtime, layout, "spec")
    view = await create_authoring_proposal(runtime, "book", request)
    decide_proposal(layout.root, view.id, decide(view))
    await apply_proposal(runtime, None, "book", view.id)
    atomic_write_text(layout.chapter_path(1), "在新设定之后完成的正文。")
    assert revision_stale_chapters(runtime.storage, layout) == set()
