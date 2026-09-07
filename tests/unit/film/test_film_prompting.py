"""Editable prompt contracts shared by film graph, UI and providers."""

from __future__ import annotations

from novel_forge.film.node_catalog import default_film_graph, film_node_catalog
from novel_forge.film.prompting import optimize_node_prompt, render_node_prompt
from novel_forge.film.workflow_graph import input_signature, validate_graph


def test_every_registered_node_has_a_versioned_editable_prompt() -> None:
    catalog = film_node_catalog()
    graph = default_film_graph("prompt-demo")

    assert catalog
    assert all(item.prompt_template.template_id == f"film.{item.type_id}" for item in catalog)
    assert all(item.prompt_template.template_version == "2.0.0" for item in catalog)
    assert all(item.prompt_template.sections for item in catalog)
    assert all(section.editable for item in catalog for section in item.prompt_template.sections)
    assert all(node.prompt.sections for node in graph.nodes)
    assert validate_graph(graph) == []


def test_h3_prompt_combines_novel_voice_and_official_guide_sections() -> None:
    graph = default_film_graph("prompt-demo")
    node = next(item for item in graph.nodes if item.node_id == "minimax-h3")

    rendered = render_node_prompt(node.prompt)
    source_kinds = {
        source.value for section in node.prompt.sections for source in section.sources
    }

    assert [section.section_id for section in node.prompt.sections] == [
        "reference_materials",
        "core_creative",
        "visual_timeline",
        "dialogue_performance",
        "overall_soundscape",
        "non_diegetic_music",
        "negative_constraints",
    ]
    assert {"novel", "voice", "h3_guide", "workflow"} <= source_kinds
    assert "【参考素材说明】" in rendered
    assert "【核心创意】" in rendered
    assert "【画面过程描述】" in rendered
    assert "【整体声景】" in rendered


def test_prompt_optimization_restores_structure_without_overwriting_user_content() -> None:
    graph = default_film_graph("prompt-demo")
    node = next(item for item in graph.nodes if item.node_id == "minimax-h3")
    edited = node.prompt.sections[1].model_copy(
        update={"content": "雨夜电台  \n\n\n  主持人发现失落录音。"}
    )
    sparse = node.model_copy(
        update={
            "prompt": node.prompt.model_copy(
                update={"sections": [edited], "updated_at": "2020-01-01T00:00:00Z"}
            )
        }
    )

    optimized = optimize_node_prompt(sparse)

    assert optimized.prompt.sections[1].content == "雨夜电台\n\n主持人发现失落录音。"
    assert len(optimized.prompt.sections) == 7
    assert any(change.startswith("恢复缺失段落") for change in optimized.changes)
    assert optimized.character_count == len(optimized.rendered_prompt)
    assert optimized.prompt.updated_at != "2020-01-01T00:00:00Z"


def test_prompt_edits_invalidate_node_and_downstream_cache_signatures() -> None:
    graph = default_film_graph("prompt-demo")
    original_node = next(item for item in graph.nodes if item.node_id == "h3-context-ir")
    original_h3_signature = input_signature(graph, "h3-context-ir")
    original_video_signature = input_signature(graph, "minimax-h3")
    sections = [
        section.model_copy(update={"content": f"{section.content}\n用户确认：保持雨夜冷色调。"})
        if section.section_id == "core_creative"
        else section
        for section in original_node.prompt.sections
    ]
    changed_graph = graph.model_copy(
        update={
            "nodes": [
                node.model_copy(
                    update={"prompt": node.prompt.model_copy(update={"sections": sections})}
                )
                if node.node_id == original_node.node_id
                else node
                for node in graph.nodes
            ]
        }
    )

    assert input_signature(changed_graph, "h3-context-ir") != original_h3_signature
    assert input_signature(changed_graph, "minimax-h3") != original_video_signature
