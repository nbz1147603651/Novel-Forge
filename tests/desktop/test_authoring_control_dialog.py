from __future__ import annotations

from types import SimpleNamespace

import pytest

from novel_forge.app_service.job_service import JobService
from novel_forge.core.authoring import AuthoringPolicy, AuthoringProposalRequest
from novel_forge.desktop.components.authoring_control import AuthoringControlDialog
from novel_forge.persistence.authoring_store import AuthoringStore, story_input_version
from novel_forge.persistence.filesystem import (
    FileSystemStorage,
    atomic_write_json,
    atomic_write_text,
)
from novel_forge.workspace.authoring_proposals import create_proposal_under_lock


@pytest.fixture
def controls(tmp_path, monkeypatch):
    from novel_forge.app_service import engine_views

    monkeypatch.setattr(
        engine_views,
        "engine_capabilities",
        lambda: SimpleNamespace(features={"authoring_coauthor": True}),
    )
    storage = FileSystemStorage(tmp_path)
    root = storage.ensure_project_dir("book")
    atomic_write_json(root / "spec.json", {"theme": "开放结局"})
    atomic_write_text(root / "chapters/chapter_001.md", "作者的旧正文")
    store = AuthoringStore(root)
    policy = store.set_policy(AuthoringPolicy(mode="coauthor", end_chapter=2), expected_version=0)
    store.start(expected_version=policy.version, input_version=story_input_version(root))
    with storage.project_lock("book"):
        view = create_proposal_under_lock(
            SimpleNamespace(storage=storage),
            "book",
            AuthoringProposalRequest(
                command="revise_chapter", chapter_number=1, candidate="作者批准的新正文"
            ),
        )
    service = JobService(storage_root=tmp_path, load_persisted_history=False)
    yield storage, service, view
    service.shutdown(wait_s=1)


def loaded(qtbot, dialog):
    qtbot.waitUntil(lambda: dialog.worker is None, timeout=5000)


def test_exact_approval_native_window_does_not_start_on_open(qtbot, controls):
    storage, service, _ = controls
    dialog = AuthoringControlDialog(storage, service, "book")
    qtbot.addWidget(dialog)
    dialog.show()
    loaded(qtbot, dialog)
    assert not service.list()
    assert not dialog.accept_button.isEnabled()
    assert dialog.candidate.toPlainText() == "作者批准的新正文"
    assert not dialog.accept_button.autoDefault()
    dialog.ack.setChecked(True)
    assert dialog.accept_button.isEnabled()
    dialog.accept_button.click()
    loaded(qtbot, dialog)
    assert (
        storage.existing_project_dir("book") / "chapters/chapter_001.md"
    ).read_text() == "作者批准的新正文"
    assert not service.list()  # Applying a text revision is not a model task.
    assert dialog.proposals[0]["status"] == "applied"
    dialog.close()


def test_native_pause_disable_reenable_requires_new_start(qtbot, controls):
    storage, service, _ = controls
    dialog = AuthoringControlDialog(storage, service, "book")
    qtbot.addWidget(dialog)
    dialog.show()
    loaded(qtbot, dialog)
    dialog.availability.click()
    loaded(qtbot, dialog)
    assert dialog.session["disabled"]
    assert not dialog.accept_button.isEnabled()
    dialog.availability.click()
    loaded(qtbot, dialog)
    assert not dialog.session["disabled"] and dialog.session["policy"]["stopped"]
    assert not service.list()
    assert (
        storage.existing_project_dir("book") / "chapters/chapter_001.md"
    ).read_text() == "作者的旧正文"
    dialog.close()


def test_stale_native_acceptance_cannot_overwrite_manual_edit(qtbot, controls):
    storage, service, _ = controls
    dialog = AuthoringControlDialog(storage, service, "book")
    qtbot.addWidget(dialog)
    dialog.show()
    loaded(qtbot, dialog)
    text = storage.existing_project_dir("book") / "chapters/chapter_001.md"
    atomic_write_text(text, "作者随后另改的正文")
    dialog.ack.setChecked(True)
    dialog.accept_button.click()
    loaded(qtbot, dialog)
    assert text.read_text() == "作者随后另改的正文"
    assert not service.list()
    dialog.close()


def test_unreleased_native_window_keeps_stop_not_approval(qtbot, controls, monkeypatch):
    from novel_forge.app_service import engine_views

    monkeypatch.setattr(
        engine_views,
        "engine_capabilities",
        lambda: SimpleNamespace(features={"authoring_coauthor": False}),
    )
    storage, service, _ = controls
    dialog = AuthoringControlDialog(storage, service, "book")
    qtbot.addWidget(dialog)
    dialog.show()
    loaded(qtbot, dialog)
    dialog.ack.setChecked(True)
    assert not dialog.accept_button.isEnabled() and not dialog.save.isEnabled()
    assert dialog.pause.isEnabled() and dialog.reject_button.isEnabled()
    dialog.reject_button.click()
    loaded(qtbot, dialog)
    assert dialog.proposals[0]["status"] == "rejected"
    assert not service.list()
    dialog.close()


def test_unconfigured_native_defaults_to_current_chapter_without_start(qtbot, tmp_path):
    storage = FileSystemStorage(tmp_path)
    storage.ensure_project_dir("book")
    service = JobService(storage_root=tmp_path, load_persisted_history=False)
    try:
        dialog = AuthoringControlDialog(storage, service, "book", chapter=7)
        qtbot.addWidget(dialog)
        dialog.show()
        loaded(qtbot, dialog)
        assert dialog.first.value() == dialog.last.value() == 7
        assert dialog.mode.currentData() == "coauthor"
        assert not service.list()
        assert not (storage.existing_project_dir("book") / "authoring_policy.json").exists()
        dialog.close()
    finally:
        service.shutdown(wait_s=1)
