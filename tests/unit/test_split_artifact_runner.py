from __future__ import annotations

import json
from typing import Any

from novel_forge.common.constants import TaskType
from novel_forge.pipeline.steps.split_artifact_runner import (
    SplitArtifactRunner,
    SplitJsonFragment,
)


async def test_split_artifact_runner_normalizes_checkpoint_payload(tmp_path) -> None:
    async def fail_call_json(
        task_type: TaskType,
        context: dict[str, Any],
        max_tokens: int,
        temperature: float,
        required_keys: tuple[str, ...],
        max_retries: int,
    ) -> dict[str, Any]:
        raise AssertionError("checkpoint payload should be reused")

    checkpoint_path = tmp_path / "character_profiles.checkpoint.json"
    runner = SplitArtifactRunner(
        call_json=fail_call_json,
        checkpoint_path=checkpoint_path,
        artifact_name="character_profiles",
    )
    fragment = SplitJsonFragment(
        name="profiles",
        task_type=TaskType.INIT_CHARACTER_PROFILE_BATCH,
        context={},
        required_keys=("character_profiles",),
        max_tokens=1024,
    )
    signature = runner._signature([fragment])
    checkpoint_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "artifact_name": "character_profiles",
                "signature": signature,
                "fragments": {
                    "profiles": {
                        "character_profiles": [
                            {
                                "name": "民国线陆云峥",
                                "role": "minor",
                                "abilities": {"professional": "商业经营", "ideology": "实业救国"},
                                "appearance": {"general": "民国西装", "detail": "袖口染血"},
                                "personality": {"core_traits": "理想主义", "fears": "失约"},
                                "arc": {"start": "实业青年", "end": "以身守诺"},
                            }
                        ]
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = await runner.run([fragment])

    assert result.from_checkpoint == {"profiles"}
    profile = result.fragments["profiles"]["character_profiles"][0]
    assert profile["abilities"] == "professional: 商业经营；ideology: 实业救国"
    assert profile["appearance"] == "general: 民国西装；detail: 袖口染血"
    assert profile["personality"] == "core_traits: 理想主义；fears: 失约"
    assert profile["arc"] == "start: 实业青年；end: 以身守诺"

    saved = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    saved_profile = saved["fragments"]["profiles"]["character_profiles"][0]
    assert saved_profile["arc"] == "start: 实业青年；end: 以身守诺"


async def test_split_artifact_runner_cache_version_invalidates_checkpoint(tmp_path) -> None:
    calls = 0

    async def call_json(
        task_type: TaskType,
        context: dict[str, Any],
        max_tokens: int,
        temperature: float,
        required_keys: tuple[str, ...],
        max_retries: int,
    ) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {"character_arcs": []}

    checkpoint_path = tmp_path / "arcs.checkpoint.json"
    runner = SplitArtifactRunner(
        call_json=call_json,
        checkpoint_path=checkpoint_path,
        artifact_name="character_arcs",
    )

    def fragment(version: str) -> SplitJsonFragment:
        return SplitJsonFragment(
            name="arcs",
            task_type=TaskType.INIT_CHARACTER_ARC_PLAN,
            context={},
            required_keys=("character_arcs",),
            max_tokens=1024,
            cache_version=version,
        )

    await runner.run([fragment("v1")])
    await runner.run([fragment("v1")])
    await runner.run([fragment("v2")])

    assert calls == 2
