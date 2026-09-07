"""Small legacy-desktop approval window; deliberately not a second chat UI."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from novel_forge.app_service.authoring_commands import AuthoringCommands, require_authoring_rollout
from novel_forge.app_service.contracts import JobCommand
from novel_forge.core.authoring import (
    AuthoringPolicy,
    AuthoringProposalDecision,
    AuthoringProposalRequest,
)
from novel_forge.desktop.pages._page_utils import safe_disconnect
from novel_forge.desktop.workers.base import BaseJobWorker, BaseJobWorkerSignals
from novel_forge.persistence.authoring_proposals import ProposalStore
from novel_forge.persistence.filesystem import FileSystemStorage


class _Signals(BaseJobWorkerSignals):
    result = Signal(dict)


class AuthoringControlWorker(BaseJobWorker):
    signals_cls = _Signals
    pool = "ui_io"

    def __init__(
        self,
        controls: AuthoringCommands,
        project: str,
        chapter: int,
        operation: str,
        payload: dict[str, Any],
    ) -> None:
        super().__init__()
        self.controls, self.project, self.chapter = controls, project, chapter
        self.operation, self.payload = operation, payload

    async def _run_async(self) -> None:
        self._check_cancel()
        c, p, data = self.controls, self.project, self.payload
        if self.operation == "policy":
            c.set_policy(
                p, AuthoringPolicy.model_validate(data["policy"]), data["expected_version"]
            )
        elif self.operation == "start":
            c.start(p, data["expected_version"], data["input_version"])
        elif self.operation == "pause":
            c.service.pause_authoring(p)
        elif self.operation == "availability":
            if data["enabled"]:
                require_authoring_rollout()
                c.service.enable_authoring(
                    p,
                    expected_version=data["expected_version"],
                    input_version=data["input_version"],
                )
            else:
                c.service.pause_authoring(p, disable=True)
        elif self.operation == "propose":
            await c.propose(p, AuthoringProposalRequest.model_validate(data))
        elif self.operation == "decide":
            c.decide(p, data["id"], AuthoringProposalDecision.model_validate(data["decision"]))
            if data["decision"]["decision"] == "accept":
                await c.apply(p, data["id"])
        elif self.operation == "apply":
            await c.apply(p, data["id"])
        elif self.operation == "prepare":
            require_authoring_rollout()
            c.service.submit(
                JobCommand(
                    kind="prepare_chapter",
                    project_id=p,
                    payload={"project_id": p, "chapter_number": self.chapter},
                )
            )
        elif self.operation != "refresh":
            raise ValueError("不支持的作者操作")
        self._check_cancel()
        view = c.view(p, self.chapter)
        from novel_forge.app_service.engine_views import engine_capabilities

        self.signals.result.emit(
            {
                "session": view.model_dump(mode="json"),
                "proposals": [
                    item.model_dump(mode="json")
                    for item in ProposalStore(c.storage.existing_project_dir(p)).views()
                ],
                "released": engine_capabilities().features.get("authoring_coauthor", False),
            }
        )


class AuthoringControlDialog(QDialog):
    changed = Signal()

    def __init__(
        self,
        storage: FileSystemStorage,
        service: Any,
        project: str,
        chapter: int = 1,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("作者授权与确认")
        self.resize(850, 720)
        self.controls = AuthoringCommands(storage, service)
        self.project = project
        self.worker: AuthoringControlWorker | None = None
        self.session: dict[str, Any] = {}
        self.proposals: list[dict[str, Any]] = []
        self.released = False
        self._closed = False
        self._buttons: list[QPushButton] = []
        root = QVBoxLayout(self)
        self.status = QLabel("正在读取后端授权…")
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        form = QFormLayout()
        self.mode = QComboBox()
        for label, value in (
            ("手动", "manual"),
            ("AI 共创（推荐）", "coauthor"),
            ("授权自动", "authorized_auto"),
        ):
            self.mode.addItem(label, value)
        self.first, self.last, self.chapter = QSpinBox(), QSpinBox(), QSpinBox()
        for spin in (self.first, self.last, self.chapter):
            spin.setRange(1, 100000)
            spin.setValue(chapter)
        self.budget = QLineEdit()
        self.budget.setPlaceholderText("留空沿用既有预算；不会增加额度")
        for label, widget in (
            ("模式", self.mode),
            ("授权开始章", self.first),
            ("授权结束章", self.last),
            ("累计预算 USD", self.budget),
            ("当前查看章", self.chapter),
        ):
            form.addRow(label, widget)
        root.addLayout(form)
        row = QHBoxLayout()
        self.save = self._button(row, "保存范围（不启动）", self._save)
        self.start = self._button(row, "启用授权（不启动任务）", self._start)
        self.pause = self._button(row, "暂停", lambda: self._run("pause"))
        self.availability = self._button(row, "停用本作品共创", self._availability)
        root.addLayout(row)
        row = QHBoxLayout()
        self.refresh = self._button(row, "刷新状态", lambda: self._run("refresh"))
        self.prepare = self._button(row, "准备本章方案", lambda: self._run("prepare"))
        self.options = QComboBox()
        row.addWidget(self.options, 1)
        self.propose = self._button(row, "查看检查点提案", self._propose)
        root.addLayout(row)
        self.choice = QComboBox()
        self.choice.currentIndexChanged.connect(self._show_proposal)
        root.addWidget(self.choice)
        self.details = QLabel()
        self.details.setWordWrap(True)
        root.addWidget(self.details)
        diff = QHBoxLayout()
        self.original, self.candidate = QPlainTextEdit(), QPlainTextEdit()
        for label, diff_widget in (
            ("原文 / 原方案", self.original),
            ("候选（未批准不写入）", self.candidate),
        ):
            column = QVBoxLayout()
            column.addWidget(QLabel(label))
            diff_widget.setReadOnly(True)
            column.addWidget(diff_widget)
            diff.addLayout(column)
        root.addLayout(diff, 1)
        self.ack = QCheckBox("已核对候选、影响章段及锁定冲突，只批准所显示版本")
        self.ack.toggled.connect(self._update_buttons)
        root.addWidget(self.ack)
        row = QHBoxLayout()
        self.accept_button = self._button(row, "批准此版本并应用", lambda: self._decide("accept"))
        self.reject_button = self._button(row, "拒绝", lambda: self._decide("reject"))
        self.defer = self._button(row, "暂存", lambda: self._decide("defer"))
        self.apply = self._button(row, "核验 / 完成已批准操作", self._apply)
        root.addLayout(row)
        self.finished.connect(self.shutdown)
        self._run("refresh")

    def _button(self, row: QHBoxLayout, text: str, action: Any) -> QPushButton:
        button = QPushButton(text)
        button.setAutoDefault(False)  # Enter/recommendations must never approve.
        button.clicked.connect(action)
        row.addWidget(button)
        self._buttons.append(button)
        return button

    def _run(self, operation: str, payload: dict[str, Any] | None = None) -> None:
        if self.worker is not None or self._closed:
            return
        worker = AuthoringControlWorker(
            self.controls, self.project, self.chapter.value(), operation, payload or {}
        )
        self.worker = worker
        worker.signals.result.connect(self._loaded)
        worker.signals.worker_failed.connect(self._failed)
        worker.signals.worker_finished.connect(self._finished)
        worker.signals.worker_cancelled.connect(self._finished)
        self._update_buttons()
        worker.submit()

    @Slot(dict)
    def _loaded(self, data: dict[str, Any]) -> None:
        self.session, self.proposals, self.released = (
            data["session"],
            data["proposals"],
            data["released"],
        )
        policy = self.session["policy"]
        self.mode.setCurrentIndex(
            self.mode.findData(policy["mode"] if self.session["configured"] else "coauthor")
        )
        chapter = self.chapter.value()
        self.first.setValue(policy["start_chapter"] if self.session["configured"] else chapter)
        self.last.setValue(policy["end_chapter"] if self.session["configured"] else chapter)
        self.budget.setText("" if policy["budget_usd"] is None else str(policy["budget_usd"]))
        self.status.setText(
            f"{self.session['stop_state']} · {self.session['waiting_reason']}\n任务：{self.session['current_task_id'] or '无'}。保存或启用授权均不自动启动任务。"
        )
        self.options.clear()
        for option in self.session["checkpoint"].get("options", []):
            self.options.addItem(option["label"], option["option_id"])
        selected = self.choice.currentData()
        self.choice.blockSignals(True)
        self.choice.clear()
        for item in self.proposals:
            self.choice.addItem(f"{item['status']} · {item['title']}", item["id"])
        index = self.choice.findData(selected)
        self.choice.setCurrentIndex(index if index >= 0 else self.choice.count() - 1)
        self.choice.blockSignals(False)
        self._show_proposal()
        self.changed.emit()

    def _selected(self) -> dict[str, Any]:
        return next(
            (item for item in self.proposals if item["id"] == self.choice.currentData()), {}
        )

    @Slot()
    def _show_proposal(self) -> None:
        item = self._selected()
        self.ack.setChecked(False)
        self.original.setPlainText(item.get("original", ""))
        self.candidate.setPlainText(item.get("candidate", ""))
        self.details.setText(
            "\n".join(
                [
                    "影响章节：" + "、".join(map(str, item.get("affected_chapters", []))),
                    *item.get("evidence", []),
                    *item.get("risks", []),
                    *item.get("lock_conflicts", []),
                    item.get("cost_hint", ""),
                    str(item.get("application_result", "")),
                ]
            )
        )
        self._update_buttons()

    @Slot()
    def _update_buttons(self) -> None:
        for button in self._buttons:
            button.setEnabled(self.worker is None and bool(self.session))
        if self.worker is not None or not self.session:
            return
        s, item = self.session, self._selected()
        idle = s["stop_state"] not in {"running", "stopping"}
        enabled = self.released and not s["disabled"]
        self.save.setEnabled(enabled and idle)
        self.start.setEnabled(enabled and idle and s["configured"])
        self.pause.setEnabled(s["configured"])
        self.availability.setText("重新启用（保持停止）" if s["disabled"] else "停用本作品共创")
        self.availability.setEnabled(
            s["configured"] and (not s["disabled"] or (idle and self.released))
        )
        permissions = {p["action"]: p for p in s["allowed_actions"]}
        self.prepare.setEnabled(enabled and idle and permissions["prepare"]["allowed"])
        self.prepare.setToolTip(permissions["prepare"]["reason"])
        self.propose.setEnabled(
            enabled and bool(self.options.count()) and not s["policy"]["stopped"]
        )
        pending = item.get("status") in {"pending", "deferred"}
        self.accept_button.setEnabled(
            enabled and pending and not s["policy"]["stopped"] and self.ack.isChecked()
        )
        self.reject_button.setEnabled(
            item.get("status") in {"pending", "deferred", "stale", "approved"}
            and not item.get("application_result", {}).get("task_id")
        )
        self.defer.setEnabled(self.reject_button.isEnabled())
        application = item.get("application_result", {})
        self.apply.setEnabled(
            bool(
                application.get("recoverable_receipt")
                or application.get("task_id")
                or (enabled and item.get("status") == "approved")
            )
        )

    def _save(self) -> None:
        try:
            budget = None if not self.budget.text().strip() else float(self.budget.text())
            policy = AuthoringPolicy(
                mode=self.mode.currentData(),
                start_chapter=self.first.value(),
                end_chapter=self.last.value(),
                budget_usd=budget,
            )
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        self._run(
            "policy",
            {
                "policy": policy.model_dump(mode="json"),
                "expected_version": self.session["policy"]["version"]
                if self.session["configured"]
                else 0,
            },
        )

    def _start(self) -> None:
        self._run(
            "start",
            {
                "expected_version": self.session["policy"]["version"],
                "input_version": self.session["input_version"],
            },
        )

    def _availability(self) -> None:
        self._run(
            "availability",
            {
                "enabled": self.session["disabled"],
                "expected_version": self.session["policy"]["version"],
                "input_version": self.session["input_version"],
            },
        )

    def _propose(self) -> None:
        self._run(
            "propose",
            {
                "command": "checkpoint",
                "chapter_number": self.chapter.value(),
                "option_id": self.options.currentData(),
                "title": self.options.currentText(),
                "expected_input_version": self.session["input_version"],
            },
        )

    def _decide(self, decision: str) -> None:
        item = self._selected()
        if not item or (decision == "accept" and not self.ack.isChecked()):
            return
        self._run(
            "decide",
            {
                "id": item["id"],
                "decision": {
                    "decision": decision,
                    "candidate_version": item["candidate_version"],
                    "input_version": item["input_version"],
                    "policy_version": item["policy_version"],
                },
            },
        )

    def _apply(self) -> None:
        self._run("apply", {"id": self._selected()["id"]})

    @Slot(str, dict)
    def _failed(self, worker_id: str, error: dict[str, Any]) -> None:
        self.status.setText(str(error.get("message") or error))
        self._finished(worker_id)

    @Slot(str)
    def _finished(self, _worker_id: str) -> None:
        self._disconnect_worker()
        self.worker = None
        self._update_buttons()

    def _disconnect_worker(self) -> None:
        if self.worker is not None:
            signals = self.worker.signals
            safe_disconnect(signals.result, self._loaded)
            safe_disconnect(signals.worker_failed, self._failed)
            safe_disconnect(signals.worker_finished, self._finished)
            safe_disconnect(signals.worker_cancelled, self._finished)

    def shutdown(self) -> None:
        self._closed = True
        if self.worker is not None:
            self.worker.request_cancel()
            self._disconnect_worker()
        # Closing this view never resumes or approves a chapter task.
