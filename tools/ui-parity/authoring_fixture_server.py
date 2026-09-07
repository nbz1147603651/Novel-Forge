"""Offline authoring UI fixture: real approvals, disposable data, no model gateway.

Run explicitly with .venv/bin/python tools/ui-parity/authoring_fixture_server.py.
Only binds loopback. Cannot be pointed at a user's storage directory.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import uvicorn  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

from novel_forge.api import deps  # noqa: E402
from novel_forge.api.routes.authoring import router  # noqa: E402
from novel_forge.app_service.job_service import JobService  # noqa: E402
from novel_forge.app_service.workspace_commands import PreparedCommand  # noqa: E402
from novel_forge.core.authoring import AuthoringProposalRequest, AuthoringReply  # noqa: E402
from novel_forge.persistence.authoring_store import story_input_version  # noqa: E402
from novel_forge.persistence.filesystem import (  # noqa: E402
    FileSystemStorage,
    atomic_write_json,
    atomic_write_text,
)
from novel_forge.workspace import authoring_conversation  # noqa: E402
from novel_forge.workspace.contracts import AuthoringMessageJobRequest  # noqa: E402


class OfflineReply:
    async def run(self, _request):
        await asyncio.sleep(0.1)
        return AuthoringReply(
            reply="离线示例：保留开放结局与单视角，仅建议调整末句。候选需作者单独批准。",
            proposals=[
                AuthoringProposalRequest(
                    command="revise_chapter",
                    chapter_number=1,
                    title="保留开放结局的末句修订",
                    candidate="晨钟响过，沈昭放下药盏。门外的人没有回答。她仍不知道来者是谁。",
                    evidence=["离线固定响应，仅测试提案审批，不代表模型质量"],
                )
            ],
        )


class OfflineExecutor:
    def prepare(self, command):
        return PreparedCommand(
            kind=command.kind,
            request=command.payload,
            label="离线共创讨论",
            project_id=command.project_id,
            command_name="fixture",
            metadata={},
        )

    async def run(self, prepared, runtime, on_step):
        if prepared.kind != "authoring_chat":
            raise ValueError("此界面夹具仅提供离线讨论；章节生成由管线回归单独验证")
        result = await authoring_conversation.execute_authoring_message(
            runtime, AuthoringMessageJobRequest(**prepared.request)
        )
        return result.result


class OfflineRuntime(SimpleNamespace):
    async def shutdown(self):
        pass


def main():
    with TemporaryDirectory(prefix="nimo-authoring-ui-") as directory:
        storage = FileSystemStorage(Path(directory))
        root = storage.ensure_project_dir("book")
        atomic_write_json(root / "spec.json", {"theme": "开放结局；单视角", "total_chapters": 100})
        atomic_write_text(
            root / "chapters/chapter_001.md", "晨钟响过，沈昭放下药盏。门外的人没有回答。"
        )
        atomic_write_text(
            root / "chapters/chapter_002.md", "后来新增的第二章，不允许恢复第一章时覆盖。"
        )
        runtime = OfflineRuntime(storage=storage, router=None, builder=None, settings=None)
        authoring_conversation.AuthoringChatStep = lambda **_: OfflineReply()
        service = JobService(
            storage_root=Path(directory),
            load_persisted_history=False,
            executor=OfflineExecutor(),
            runtime_factory=lambda _: runtime,
        )
        # The production query path peeks the shared service for safe-stop status.
        deps._get_job_service_cached = lambda: service
        deps._peek_cached_job_service = lambda: service
        import novel_forge.api.routes.authoring as routes

        routes._peek_cached_job_service = lambda: service
        app = FastAPI()
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["http://127.0.0.1:1420"],
            allow_methods=["GET", "POST"],
            allow_headers=["Content-Type"],
        )
        app.include_router(router, prefix="/api/v1/engine")
        app.dependency_overrides[deps.get_storage] = lambda: storage
        app.dependency_overrides[deps.get_runtime_services] = lambda: runtime
        app.dependency_overrides[deps.get_job_service] = lambda: service

        @app.get("/fixture")
        def inspect_fixture():
            return {
                "text": (root / "chapters/chapter_001.md").read_text(),
                "laterChapter": (root / "chapters/chapter_002.md").read_text(),
                "inputVersion": story_input_version(root),
                "jobs": len(service.list()),
                "modelCalls": 0,
            }

        @app.post("/fixture/concurrent-edit")
        def concurrent_edit():
            atomic_write_text(
                root / "chapters/chapter_001.md", "作者在另一个窗口刚刚改过的正文。开放结局不变。"
            )
            return inspect_fixture()

        print(f"Offline fixture only: {directory}", flush=True)
        try:
            uvicorn.run(app, host="127.0.0.1", port=18791, log_level="warning")
        finally:
            service.shutdown(wait_s=3)


if __name__ == "__main__":
    main()
