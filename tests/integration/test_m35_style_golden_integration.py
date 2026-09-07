"""Integration test: LongProjectBundle → PromptBuilder → DRAFT_CHAPTER template
end-to-end. Verifies that M3.5 wiring is sound.

Created to guard against regressions where the runtime stops passing the
retriever into PromptBuilder.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from novel_forge.common.constants import TaskType
from novel_forge.memory.style_golden_retriever import (
    GoldenPassage,
    StyleGoldenRetriever,
)
from novel_forge.pipeline.long.stages.draft import _primary_scene_intent
from novel_forge.prompts.builder import PromptBuilder

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeRetriever:
    """A stand-in that mimics StyleGoldenRetriever.retrieve_for_scene()."""

    def __init__(self, passages: list[GoldenPassage]) -> None:
        self._passages = passages
        self.calls: list[dict[str, Any]] = []

    def retrieve_for_scene(
        self,
        scene_intent: dict[str, Any],
        *,
        max_results: int = 3,
    ) -> list[GoldenPassage]:
        self.calls.append(dict(scene_intent))
        return self._passages[:max_results]


def _make_plan_with_scene(scene_intent_fields: dict[str, Any]) -> dict[str, Any]:
    """Build a fake plan-like object exposing scene_intents[0] with given fields."""
    return {
        "scene_intents": [scene_intent_fields],
    }


# ---------------------------------------------------------------------------
# _primary_scene_intent — flattens SceneIntent into retriever keys
# ---------------------------------------------------------------------------


class TestPrimarySceneIntent:
    def test_flattens_object_scene(self) -> None:
        # Build a dict-shaped scene; many SceneIntent fields end up as object attrs
        # so we use a simple namespace.
        class _S:
            emotional_beat = "克制"
            purpose = "沈鹿溪在门槛边放下行李"
            location = "云南风眼小镇老宅"
            pov_character = "沈鹿溪"
            summary = "沈鹿溪到达小镇的第一天"
            required_characters = ["沈鹿溪"]

        out = _primary_scene_intent(type("P", (), {"scene_intents": [_S()]})())
        assert out["emotional_tone"] == "克制"
        assert out["scene_action"] == "沈鹿溪在门槛边放下行李"
        assert out["setting"] == "云南风眼小镇老宅"
        assert out["pov_keywords"] == "沈鹿溪"
        assert out["characters_involved"] == ["沈鹿溪"]

    def test_handles_empty_plan(self) -> None:
        assert _primary_scene_intent(None) == {}

    def test_handles_dict_plan(self) -> None:
        plan = {"scene_intents": [{"emotional_beat": "warm", "location": "home"}]}
        out = _primary_scene_intent(plan)
        assert out["emotional_tone"] == "warm"
        assert out["setting"] == "home"

    def test_handles_empty_scenes(self) -> None:
        assert _primary_scene_intent({"scene_intents": []}) == {}


# ---------------------------------------------------------------------------
# End-to-end: scene_intent + retriever → rendered prompt contains examples
# ---------------------------------------------------------------------------


class TestEndToEndRender:
    def test_rendered_prompt_contains_golden_passages(self) -> None:
        """The full chain: a fake retriever + a scene_intent dict →
        PromptBuilder.render(DRAFT_CHAPTER) → prompt text contains the
        retriever's golden passages."""
        passages = [
            GoldenPassage(
                chapter_number=1, paragraph_index=0,
                text="窗帘缝隙里渗入第一缕灰蓝色的光，是天色将明未明时特有的暧昧色调。",
                eval_score=9.64,
            ),
            GoldenPassage(
                chapter_number=22, paragraph_index=7,
                text="她握着摄像机的手不再那么用力，肩膀的高度刚好和旁边那人同一个水平线。",
                eval_score=9.93,
            ),
        ]
        retriever = _FakeRetriever(passages)
        scene_intent = {
            "emotional_tone": "克制",
            "setting": "小镇",
            "pov_keywords": "沈鹿溪",
        }

        builder = PromptBuilder()
        context = {
            "style_golden_retriever": retriever,
            "scene_intent": scene_intent,
            "stage_cards": {
                "plan": {"scene_intents": []},
                "bridge": {},
                "opening_contract": {},
                "closing_contract": {},
                "source_slice": {},
            },
            "expected_total_words": 5000,
            "target_word_count": 5000,
            "chapter": {"chapter_number": 1, "title": "测试"},
            "chapter_number": 1,
            "chapter_title": "测试",
            "style": {"banned_phrases": []},
        }
        # _resolve_style_golden_examples runs in render()
        resolved = builder._resolve_style_golden_examples(context)
        assert len(resolved) == 2
        # Verify retriever was called with scene_intent
        assert retriever.calls
        assert retriever.calls[0]["emotional_tone"] == "克制"

        # Now full render
        rendered = builder.render(TaskType.DRAFT_CHAPTER, context)
        # Both passages should appear
        assert "灰蓝色" in rendered
        assert "摄像机" in rendered
        assert "项目级高分段落参考" in rendered

    def test_no_retriever_means_no_section(self) -> None:
        """Without a retriever in context, the section is silently omitted."""
        builder = PromptBuilder()
        context = {
            "scene_intent": {"emotional_tone": "克制"},
            "stage_cards": {
                "plan": {"scene_intents": []},
                "bridge": {},
                "opening_contract": {},
                "closing_contract": {},
                "source_slice": {},
            },
            "expected_total_words": 5000,
            "target_word_count": 5000,
            "chapter": {"chapter_number": 1, "title": "测试"},
            "chapter_number": 1,
            "chapter_title": "测试",
            "style": {"banned_phrases": []},
        }
        rendered = builder.render(TaskType.DRAFT_CHAPTER, context)
        # Section header should NOT appear when there's no retriever
        assert "项目级高分段落参考" not in rendered

    def test_real_retriever_round_trip(self, tmp_path: Path) -> None:
        """Real StyleGoldenRetriever wired into PromptBuilder must work
        end-to-end without errors."""
        project = tmp_path / "p"
        chapters_dir = project / "chapters"
        chapters_dir.mkdir(parents=True)
        (chapters_dir / "chapter_001.md").write_text(
            "窗帘缝隙里渗入第一缕灰蓝色的光，是天色将明未明时特有的暧昧色调。\n\n"
            "沈鹿溪站在老宅民宿的木门槛外，没有跨过去。"
            + "这是一段足够长的填充文本以达到六十字符的最小索引下限。"
            + "应当能被 BM25 检索到的关键词段落，确保段落长度合乎要求。"
            * 2,
            encoding="utf-8",
        )
        (project / "_chapter_meta_cache.json").write_text(
            '{"1": {"overall_score": 9.5}}', encoding="utf-8"
        )
        retriever = StyleGoldenRetriever(
            project, project / "_chapter_meta_cache.json"
        )

        builder = PromptBuilder()
        context = {
            "style_golden_retriever": retriever,
            "scene_intent": {
                "emotional_tone": "克制",
                "setting": "小镇",
            },
            "stage_cards": {
                "plan": {"scene_intents": []},
                "bridge": {},
                "opening_contract": {},
                "closing_contract": {},
                "source_slice": {},
            },
            "expected_total_words": 5000,
            "target_word_count": 5000,
            "chapter": {"chapter_number": 1, "title": "测试"},
            "chapter_number": 1,
            "chapter_title": "测试",
            "style": {"banned_phrases": []},
        }
        # Must not error; rendering may or may not include examples.
        rendered = builder.render(TaskType.DRAFT_CHAPTER, context)
        assert isinstance(rendered, str)
        assert len(rendered) > 100  # something was rendered

    def test_wave_prompt_contains_golden_passages(self) -> None:
        passages = [
            GoldenPassage(
                chapter_number=8,
                paragraph_index=4,
                text="风从堂屋穿过去，旧木门在很轻的响声里稳住了。",
                eval_score=9.6,
            )
        ]
        retriever = _FakeRetriever(passages)
        builder = PromptBuilder()

        rendered = builder.render(
            TaskType.WAVE_CHAPTER,
            {
                "style_golden_retriever": retriever,
                "scene_intent": {"emotional_tone": "克制", "setting": "老宅"},
                "draft_text": "第一场正文\n\n第二场正文",
                "chapter_number": 1,
                "chapter_title": "测试",
                "target_word_count": 5000,
                "stage_cards": {
                    "chapter": {
                        "chapter_number": 1,
                        "title": "测试",
                        "target_word_count": 5000,
                    },
                    "plan": {
                        "scene_intents": [
                            {
                                "scene_id": "scene_01",
                                "summary": "第一场",
                                "target_words": 2500,
                            }
                        ],
                        "cross_scene_intent": {
                            "cross_scene_references": [],
                            "pacing_curve": [],
                        },
                    },
                },
            },
        )

        assert "项目级高分段落参考" in rendered
        assert "旧木门" in rendered


# ---------------------------------------------------------------------------
# Bundle integration: LongProjectBundle.style_golden_retriever field
# ---------------------------------------------------------------------------


class TestBundleStyleGoldenRetrieverField:
    def test_bundle_has_style_golden_retriever_field(self) -> None:
        """LongProjectBundle must accept style_golden_retriever without error."""
        # We just need to confirm the field exists; constructing a full bundle
        # would require many other deps. Use dataclass introspection.
        from dataclasses import fields

        from novel_forge.pipeline.long.preflight import LongProjectBundle
        field_names = {f.name for f in fields(LongProjectBundle)}
        assert "style_golden_retriever" in field_names

    async def test_prepare_long_project_instantiates_style_golden_retriever(
        self,
        tmp_path: Path,
        monkeypatch: Any,
    ) -> None:
        from novel_forge.pipeline.long import preflight

        project_dir = tmp_path / "project"
        project_dir.mkdir()

        class _Storage:
            def existing_project_dir(self, project_id: str) -> Path:
                assert project_id == "project"
                return project_dir

            def exists(self, path: Path) -> bool:
                name = Path(path).name
                return name in {"spec.json", "style_profile.json", "editorial_contract.json"}

            def load_json(self, path: Path) -> dict[str, Any]:
                name = Path(path).name
                if name == "spec.json":
                    payload = {
                        "world_hint": "小镇",
                        "pov_hint": "第三人称限知",
                        "character_silence": True,
                        "backstory_reveals": [
                            {"topic": "主角前史", "required_first_appearance": 3}
                        ],
                    }
                else:
                    payload = {}
                # ArtifactLoader fingerprints request-scoped artifacts after
                # loading. Keep this fake honest by materializing what it says
                # exists instead of bypassing the production cache contract.
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                return payload

        class _KernelStore:
            def __init__(self, path: Path) -> None:
                self.path = path

            async def init_db(self) -> None:
                return None

            async def load_kernel(self, project_id: str) -> Any:
                return SimpleNamespace(project_id=project_id, current_chapter=0)

            async def create_kernel(self, project_id: str) -> Any:
                return SimpleNamespace(project_id=project_id, current_chapter=0)

        class _Retriever:
            def __init__(self, project_root: Path, cache_path: Path) -> None:
                self.project_root = project_root
                self.cache_path = cache_path

        monkeypatch.setattr(preflight, "_assert_supported_schema_file", lambda path: None)
        monkeypatch.setattr(preflight, "_assert_init_readiness", lambda storage, layout: None)
        monkeypatch.setattr(preflight, "scoped_stale_chapters", lambda storage, layout: [])
        monkeypatch.setattr(preflight, "StoryKernelStore", _KernelStore)
        monkeypatch.setattr(preflight, "load_chapter_source_slice", lambda *a, **k: None)
        monkeypatch.setattr(preflight, "build_upstream_revision_fingerprint", lambda *a, **k: {})
        monkeypatch.setattr(preflight, "StyleGoldenRetriever", _Retriever)
        monkeypatch.setattr(
            preflight.StoryOutline,
            "model_validate",
            staticmethod(
                lambda payload: SimpleNamespace(
                    chapters=[SimpleNamespace(chapter_number=1)],
                    volumes=[],
                )
            ),
        )
        monkeypatch.setattr(
            preflight.StoryBible,
            "model_validate",
            staticmethod(lambda payload: SimpleNamespace()),
        )
        monkeypatch.setattr(
            preflight.CharacterBible,
            "model_validate",
            staticmethod(lambda payload: SimpleNamespace(characters=[])),
        )
        monkeypatch.setattr(
            preflight.EditorialContract,
            "model_validate",
            staticmethod(lambda payload: SimpleNamespace(model_dump=lambda mode="json": {})),
        )

        bundle = await preflight.prepare_long_project(
            storage=_Storage(),
            project_id="project",
            chapter_number=1,
        )

        assert isinstance(bundle.style_golden_retriever, _Retriever)
        assert bundle.style_golden_retriever.project_root == project_dir
        assert bundle.style_golden_retriever.cache_path == (
            project_dir / "_chapter_meta_cache.json"
        )
        assert bundle.character_silence is True
        assert bundle.backstory_reveals[0]["topic"] == "主角前史"
