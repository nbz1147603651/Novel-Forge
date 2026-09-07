"""Stable Audio 3 local CLI adapter.

The adapter intentionally invokes the documented CLI without a shell.  Model
weights and optional hardware acceleration stay outside this repository, while
the project retains only generated files and reproducible request metadata.
"""

from __future__ import annotations

import asyncio
import os
import shlex
import shutil
import tempfile
from pathlib import Path

from novel_forge.core.local_model_resources import (
    LocalMemoryClass,
    LocalResourcePriority,
    LocalResourceRequest,
    get_local_model_resource_broker,
)
from novel_forge.tts.sound_generation.providers.base import (
    SoundGenerationError,
    SoundGenerationProvider,
)
from novel_forge.tts.sound_generation.schemas import (
    GeneratedSoundAsset,
    SoundGenerationRequest,
)


class StableAudioCliProvider(SoundGenerationProvider):
    """Run ``stable-audio`` for local ambience, SFX, or instrumental BGM."""

    def __init__(
        self,
        *,
        command: str,
        models_dir: str = "",
        huggingface_token: str = "",
        timeout_s: float = 300.0,
    ) -> None:
        self._command = command.strip()
        self._models_dir = models_dir.strip()
        self._huggingface_token = huggingface_token.strip()
        self._timeout_s = timeout_s

    @property
    def provider_id(self) -> str:
        return "stable_audio"

    async def generate(self, request: SoundGenerationRequest) -> GeneratedSoundAsset:
        async with get_local_model_resource_broker().lease(
            LocalResourceRequest(
                workload="sound_generation",
                label=f"Stable Audio · {request.model_id or request.kind.value}",
                memory_class=(
                    LocalMemoryClass.MEDIUM
                    if str(request.model_id).startswith("small-")
                    else LocalMemoryClass.HIGH
                ),
                accelerator=True,
                cpu_heavy=True,
                priority=LocalResourcePriority.BACKGROUND,
                timeout_s=self._timeout_s,
            )
        ):
            return await self._generate_with_lease(request)

    async def _generate_with_lease(
        self,
        request: SoundGenerationRequest,
    ) -> GeneratedSoundAsset:
        try:
            command_parts = shlex.split(self._command)
        except ValueError as exc:
            raise SoundGenerationError("Stable Audio command format is invalid") from exc
        if not command_parts:
            raise SoundGenerationError("Stable Audio command is not configured")
        executable = command_parts[0]
        if not Path(executable).is_file() and shutil.which(executable) is None:
            raise SoundGenerationError(
                f"Stable Audio CLI not found: {executable}. Install the runtime or configure "
                "NOVEL_FORGE_SOUND_GENERATION_STABLE_AUDIO_COMMAND."
            )
        if not request.model_id:
            raise SoundGenerationError("Stable Audio requests require a model ID")

        prompt = request.prompt
        duration_s = max(1, round(request.duration_ms / 1000))
        with tempfile.TemporaryDirectory(prefix="novel-forge-stable-audio-") as temp_dir:
            output_path = Path(temp_dir) / f"asset.{request.output_format}"
            command = [
                *command_parts,
                "--model",
                request.model_id,
                "-p",
                prompt,
                "--duration",
                str(duration_s),
                "-o",
                str(output_path),
            ]
            if request.seed is not None:
                command.extend(["--seed", str(request.seed)])
            if request.negative_prompt:
                command.extend(["--negative-prompt", request.negative_prompt])
            environment = os.environ.copy()
            if self._models_dir:
                environment["HF_HOME"] = str(Path(self._models_dir).expanduser())
            if self._huggingface_token:
                environment["HF_TOKEN"] = self._huggingface_token
            try:
                process = await asyncio.create_subprocess_exec(
                    *command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=environment,
                )
                _, stderr = await asyncio.wait_for(process.communicate(), timeout=self._timeout_s)
            except TimeoutError as exc:
                raise SoundGenerationError(
                    f"Stable Audio timed out after {self._timeout_s:.0f} seconds"
                ) from exc

            if process.returncode != 0:
                detail = stderr.decode("utf-8", errors="replace")[-1000:]
                raise SoundGenerationError(f"Stable Audio CLI failed: {detail}")
            if not output_path.is_file() or output_path.stat().st_size == 0:
                raise SoundGenerationError("Stable Audio CLI finished without producing audio")
            audio_data = output_path.read_bytes()

        return GeneratedSoundAsset(
            provider=self.provider_id,
            model_id=request.model_id,
            audio_format=request.output_format,
            duration_ms=duration_s * 1000,
            audio_data=audio_data,
        )
