"""Prompt-boundary tests for initialization Claim extraction."""

from __future__ import annotations

from novel_forge.pipeline.long.services.init.init_coherence_extract import _claim_prompt_profile


def test_claim_prompt_profile_excludes_model_authored_name_bearing_prose() -> None:
    projected = _claim_prompt_profile(
        {
            "genre_tags": ["悬疑"],
            "narrative_modes": ["多线"],
            "project_ontology": {
                "state_axes": ["信任"],
                "payoff_types": ['{"character":"旧草稿名"}'],
            },
            "conflict_lens": ["旧草稿名与错误人物名发生冲突"],
            "extraction_guidance": ["把当前源姓名改成旧草稿名"],
            "entity_catalog": {"allowed_entities": [{"canonical_name": "规范名"}]},
            "summary": "旧草稿名主导故事。",
        }
    )

    assert projected == {
        "genre_tags": ["悬疑"],
        "narrative_modes": ["多线"],
        "project_ontology": {"state_axes": ["信任"]},
    }
