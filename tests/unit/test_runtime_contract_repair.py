from __future__ import annotations

from novel_forge.pipeline.long.services.runtime_contract_repair import (
    classify_contract_execution_blocker,
)


def test_contract_execution_blocker_defaults_future_leak_to_prose_repair() -> None:
    assert (
        classify_contract_execution_blocker(
            {
                "should_block_archive": True,
                "repair_or_replan_decision": "repair",
                "future_leak_hits": ["提前泄露后续合作"],
            }
        )
        == "prose_source_error"
    )


def test_contract_execution_blocker_uses_explicit_contract_source_marker() -> None:
    assert (
        classify_contract_execution_blocker(
            {
                "should_block_archive": True,
                "repair_or_replan_decision": "repair",
                "blocker_source": "contract_source_error",
                "missing_required_progressions": ["契约要求了不存在的推进"],
            }
        )
        == "contract_source_error"
    )


def test_contract_execution_blocker_replan_missing_progression_is_contract_source() -> None:
    assert (
        classify_contract_execution_blocker(
            {
                "should_block_archive": True,
                "repair_or_replan_decision": "replan",
                "missing_required_progressions": ["契约要求了不存在的推进"],
            }
        )
        == "contract_source_error"
    )
