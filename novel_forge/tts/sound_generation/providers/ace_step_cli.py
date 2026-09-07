"""ACE-Step 1.5 local CLI adapter for instrumental BGM generation.

ACE-Step 1.5 is an MIT-licensed music generation model capable of producing
high-quality instrumental tracks up to ~285 seconds with only 4GB VRAM.
This adapter invokes the documented CLI without a shell, following the same
pattern as StableAudioCliProvider.

Model weights and optional hardware acceleration stay outside this repository;
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


class ACEStepCliProvider(SoundGenerationProvider):
    """Run ``ace-step`` CLI for local instrumental BGM generation."""

    def __init__(
        self,
        *,
        command: str,
        models_dir: str = "",
        timeout_s: float = 300.0,
    ) -> None:
        self._command = command.strip()
        self._models_dir = models_dir.strip()
        self._timeout_s = timeout_s

    @property
    def provider_id(self) -> str:
        return "ace_step"

    async def generate(self, request: SoundGenerationRequest) -> GeneratedSoundAsset:
        async with get_local_model_resource_broker().lease(
            LocalResourceRequest(
                workload="sound_generation",
                label=f"ACE-Step · {request.model_id or 'bgm'}",
                memory_class=LocalMemoryClass.MEDIUM,
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
            raise SoundGenerationError("ACE-Step command format is invalid") from exc
        if not command_parts:
            raise SoundGenerationError(
                "ACE-Step command is not configured. Set "
                "NOVEL_FORGE_SOUND_GENERATION_ACE_STEP_COMMAND."
            )
        executable = command_parts[0]
        if not Path(executable).is_file() and shutil.which(executable) is None:
            raise SoundGenerationError(
                f"ACE-Step CLI not found: {executable}. Install the runtime or configure "
                "NOVEL_FORGE_SOUND_GENERATION_ACE_STEP_COMMAND."
            )

        prompt = request.prompt
        duration_s = max(1, round(request.duration_ms / 1000))
        with tempfile.TemporaryDirectory(prefix="novel-forge-ace-step-") as temp_dir:
            output_path = Path(temp_dir) / f"asset.{request.output_format}"
            command = [
                *command_parts,
                "--prompt",
                prompt,
                "--duration",
                str(duration_s),
                "--output",
                str(output_path),
                # ACE-Step generates instrumental by default for BGM use.
                "--instrumental",
            ]
            if request.seed is not None:
                command.extend(["--seed", str(request.seed)])
            if request.negative_prompt:
                command.extend(["--negative-prompt", request.negative_prompt])
            environment = os.environ.copy()
            if self._models_dir:
                environment["HF_HOME"] = str(Path(self._models_dir).expanduser())
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
                    f"ACE-Step timed out after {self._timeout_s:.0f} seconds"
                ) from exc

            if process.returncode != 0:
                detail = stderr.decode("utf-8", errors="replace")[-1000:]
                raise SoundGenerationError(f"ACE-Step CLI failed: {detail}")
            if not output_path.is_file() or output_path.stat().st_size == 0:
                raise SoundGenerationError("ACE-Step CLI finished without producing audio")
            audio_data = output_path.read_bytes()

        return GeneratedSoundAsset(
            provider=self.provider_id,
            model_id=request.model_id or "ace-step-1.5",
            audio_format=request.output_format,
            duration_ms=duration_s * 1000,
            audio_data=audio_data,
        )
