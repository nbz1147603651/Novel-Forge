"""Validation, impact calculation and cost estimation for film graphs."""

from __future__ import annotations

import hashlib
import json

from .node_catalog import node_definition_map
from .prompting import prompt_template_for, prompt_warnings
from .providers.catalog import FILM_PROVIDER_CATALOG
from .schemas import FILM_STAGE_ORDER
from .workflow_models import (
    FilmGraphDefinition,
    FilmGraphIssueSeverity,
    FilmGraphNode,
    FilmGraphRun,
    FilmGraphRunScope,
    FilmGraphRunStatus,
    FilmGraphValidationIssue,
    FilmNodeAttempt,
    FilmNodePort,
    FilmRunEstimate,
)


def validate_graph(graph: FilmGraphDefinition) -> list[FilmGraphValidationIssue]:
    issues: list[FilmGraphValidationIssue] = []
    catalog = node_definition_map()
    by_id: dict[str, FilmGraphNode] = {}
    group_ids = {group.group_id for group in graph.groups}
    for node in graph.nodes:
        if node.node_id in by_id:
            issues.append(
                _issue("duplicate_node", f"节点 ID 重复：{node.node_id}", node_id=node.node_id)
            )
        by_id[node.node_id] = node
        definition = catalog.get(node.type_id)
        if definition is None:
            issues.append(
                _issue("unknown_node_type", f"节点类型未注册：{node.type_id}", node_id=node.node_id)
            )
            continue
        if node.type_version != definition.version:
            issues.append(
                _issue(
                    "node_version_mismatch",
                    f"节点版本不受支持：{node.type_id}@{node.type_version}",
                    node_id=node.node_id,
                )
            )
        prompt_template = prompt_template_for(node.type_id)
        if node.prompt.template_id != prompt_template.template_id:
            issues.append(
                _issue(
                    "prompt_template_mismatch",
                    f"提示词模板与节点类型不一致：{node.label}",
                    node_id=node.node_id,
                )
            )
        elif node.prompt.template_version != prompt_template.template_version:
            issues.append(
                _issue(
                    "prompt_template_outdated",
                    f"提示词模板已有新版本：{node.label}",
                    node_id=node.node_id,
                    severity=FilmGraphIssueSeverity.WARNING,
                )
            )
        for section in node.prompt.sections:
            if section.required and not section.content.strip():
                issues.append(
                    _issue(
                        "empty_prompt_section",
                        f"必填提示词段落为空：{node.label} / {section.label}",
                        node_id=node.node_id,
                    )
                )
        for message in prompt_warnings(node):
            if message.startswith("必填提示词段落为空"):
                continue
            issues.append(
                _issue(
                    "prompt_guidance",
                    f"{node.label}：{message}",
                    node_id=node.node_id,
                    severity=FilmGraphIssueSeverity.WARNING,
                )
            )
        if _port_signatures(node.input_ports) != _port_signatures(definition.input_ports) or (
            _port_signatures(node.output_ports) != _port_signatures(definition.output_ports)
        ):
            issues.append(
                _issue(
                    "node_port_schema_mismatch",
                    f"节点端口与注册目录不一致：{node.label}",
                    node_id=node.node_id,
                )
            )
        if node.group_id and node.group_id not in group_ids:
            issues.append(
                _issue("unknown_group", f"节点分组不存在：{node.group_id}", node_id=node.node_id)
            )
        if node.bypassed and not definition.allows_bypass:
            issues.append(
                _issue("bypass_not_allowed", f"节点不允许旁路：{node.label}", node_id=node.node_id)
            )
        declared_providers = {
            capability.split(":", 1)[0]
            for capability in definition.provider_capabilities
            if ":" in capability
            and capability.split(":", 1)[0] in FILM_PROVIDER_CATALOG
        }
        if node.provider_id and declared_providers and node.provider_id not in declared_providers:
            issues.append(
                _issue(
                    "provider_not_supported",
                    f"节点 {node.label} 不支持平台：{node.provider_id}",
                    node_id=node.node_id,
                )
            )
        if len(declared_providers) > 1 and not node.provider_id:
            issues.append(
                _issue(
                    "provider_required",
                    f"多平台节点必须选择生成平台：{node.label}",
                    node_id=node.node_id,
                )
            )
        routable_video = any(port.artifact_type == "VideoClip" for port in node.output_ports) and not any(
            port.artifact_type == "VideoClip" for port in node.input_ports
        )
        if len(declared_providers) > 1 and node.provider_id in declared_providers and routable_video:
            model_ids = {
                str(item.get("id") or "")
                for item in FILM_PROVIDER_CATALOG[node.provider_id].get("video_models", [])
                if isinstance(item, dict)
            }
            if node.model_id not in model_ids:
                issues.append(
                    _issue(
                        "model_not_supported",
                        f"平台 {node.provider_id} 不支持视频模型：{node.model_id or '未选择'}",
                        node_id=node.node_id,
                    )
                )
        required_config = definition.config_schema.get("required", [])
        for field_name in required_config if isinstance(required_config, list) else []:
            if field_name not in node.config:
                issues.append(
                    _issue(
                        "missing_config",
                        f"缺少节点配置：{node.label} / {field_name}",
                        node_id=node.node_id,
                    )
                )

    incoming: dict[tuple[str, str], int] = {}
    adjacency: dict[str, set[str]] = {node.node_id: set() for node in graph.nodes}
    edge_ids: set[str] = set()
    edge_signatures: set[tuple[str, str, str, str]] = set()
    stage_rank = {stage: index for index, stage in enumerate(FILM_STAGE_ORDER)}
    for edge in graph.edges:
        if edge.edge_id in edge_ids:
            issues.append(
                _issue("duplicate_edge", f"连线 ID 重复：{edge.edge_id}", edge_id=edge.edge_id)
            )
        edge_ids.add(edge.edge_id)
        source = next((node for node in graph.nodes if node.node_id == edge.source_node_id), None)
        target = next((node for node in graph.nodes if node.node_id == edge.target_node_id), None)
        if source is None or target is None:
            issues.append(_issue("dangling_edge", "连线引用了不存在的节点。", edge_id=edge.edge_id))
            continue
        if source.node_id == target.node_id:
            issues.append(
                _issue(
                    "self_edge",
                    "节点不能连接到自身。",
                    node_id=source.node_id,
                    edge_id=edge.edge_id,
                )
            )
            continue
        signature = (
            edge.source_node_id,
            edge.source_port_id,
            edge.target_node_id,
            edge.target_port_id,
        )
        if signature in edge_signatures:
            issues.append(
                _issue(
                    "duplicate_connection",
                    "同一组端口不能重复连接。",
                    node_id=target.node_id,
                    edge_id=edge.edge_id,
                )
            )
            continue
        edge_signatures.add(signature)
        source_port = next(
            (port for port in source.output_ports if port.port_id == edge.source_port_id), None
        )
        target_port = next(
            (port for port in target.input_ports if port.port_id == edge.target_port_id), None
        )
        if source_port is None or target_port is None:
            issues.append(
                _issue("unknown_port", "连线端口不存在或方向错误。", edge_id=edge.edge_id)
            )
            continue
        if source_port.artifact_type != target_port.artifact_type:
            issues.append(
                _issue(
                    "incompatible_ports",
                    f"产物类型不兼容：{source_port.artifact_type} → {target_port.artifact_type}",
                    node_id=target.node_id,
                    edge_id=edge.edge_id,
                )
            )
            continue
        if stage_rank[source.stage] > stage_rank[target.stage]:
            issues.append(
                _issue(
                    "stage_regression",
                    f"制作流程不能从 {source.stage.value} 回连到 {target.stage.value}。",
                    node_id=target.node_id,
                    edge_id=edge.edge_id,
                )
            )
        key = (target.node_id, target_port.port_id)
        incoming[key] = incoming.get(key, 0) + 1
        if incoming[key] > 1 and not target_port.multiple:
            issues.append(
                _issue(
                    "multiple_input",
                    f"输入端口只能连接一次：{target_port.label}",
                    node_id=target.node_id,
                    edge_id=edge.edge_id,
                )
            )
        adjacency[source.node_id].add(target.node_id)

    for node in graph.nodes:
        if node.disabled:
            continue
        for port in node.input_ports:
            if port.required and incoming.get((node.node_id, port.port_id), 0) == 0:
                issues.append(
                    _issue(
                        "missing_input",
                        f"缺少必需输入：{node.label} / {port.label}",
                        node_id=node.node_id,
                    )
                )

    color: dict[str, int] = {node_id: 0 for node_id in adjacency}

    def visit(node_id: str) -> bool:
        color[node_id] = 1
        for target_id in adjacency[node_id]:
            if color[target_id] == 1:
                return True
            if color[target_id] == 0 and visit(target_id):
                return True
        color[node_id] = 2
        return False

    if any(color[node_id] == 0 and visit(node_id) for node_id in adjacency):
        issues.append(_issue("cycle", "工作流包含循环依赖，保存后仍不可执行。"))
    return issues


def _port_signatures(ports: list[FilmNodePort]) -> list[tuple[object, ...]]:
    return [
        (
            port.port_id,
            port.artifact_type,
            port.required,
            port.multiple,
        )
        for port in ports
    ]


def _issue(
    code: str,
    message: str,
    *,
    node_id: str = "",
    edge_id: str = "",
    severity: FilmGraphIssueSeverity = FilmGraphIssueSeverity.ERROR,
) -> FilmGraphValidationIssue:
    return FilmGraphValidationIssue(
        code=code,
        message=message,
        severity=severity,
        node_id=node_id,
        edge_id=edge_id,
    )


def execution_nodes(
    graph: FilmGraphDefinition,
    scope: FilmGraphRunScope,
    target_node_ids: list[str],
) -> list[str]:
    by_id = {node.node_id: node for node in graph.nodes if not node.disabled}
    parents: dict[str, set[str]] = {node_id: set() for node_id in by_id}
    children: dict[str, set[str]] = {node_id: set() for node_id in by_id}
    for edge in graph.edges:
        if edge.source_node_id in by_id and edge.target_node_id in by_id:
            parents[edge.target_node_id].add(edge.source_node_id)
            children[edge.source_node_id].add(edge.target_node_id)

    selected = {node_id for node_id in target_node_ids if node_id in by_id}
    if scope == FilmGraphRunScope.ALL or not selected:
        wanted = set(by_id)
    elif scope == FilmGraphRunScope.DOWNSTREAM:
        wanted = _closure(selected, children)
        wanted |= _closure(selected, parents)
    else:
        wanted = _closure(selected, parents)

    indegree = {node_id: 0 for node_id in wanted}
    for node_id in wanted:
        indegree[node_id] = sum(1 for parent in parents[node_id] if parent in wanted)
    queue = sorted(node_id for node_id, count in indegree.items() if count == 0)
    ordered: list[str] = []
    while queue:
        node_id = queue.pop(0)
        ordered.append(node_id)
        for child in sorted(children[node_id]):
            if child not in indegree:
                continue
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)
    return ordered


def _closure(seed: set[str], relation: dict[str, set[str]]) -> set[str]:
    found = set(seed)
    pending = list(seed)
    while pending:
        node_id = pending.pop()
        for linked in relation.get(node_id, set()):
            if linked not in found:
                found.add(linked)
                pending.append(linked)
    return found


_DIRECT_SOURCE_PREFIXES: dict[str, tuple[str, ...]] = {
    "creative_brief": (
        "story_spec",
        "story_bible",
        "narrative_blueprint",
    ),
    "style_lock": ("style_profile", "audio_creative_bible"),
    "character_assets": ("character_bible", "voice_team"),
    "scene_assets": ("story_bible", "outline", "novel_chapter:"),
    "prop_assets": ("story_bible", "outline", "novel_chapter:"),
    "shot_list": ("outline", "novel_chapter:"),
    "storyboard": ("outline", "novel_chapter:"),
    "h3_context_ir": ("voice_team", "audio_creative_bible", "novel_chapter:"),
}


def input_signature(graph: FilmGraphDefinition, node_id: str) -> str:
    """Hash node configuration plus exact direct and transitive source versions."""

    return _input_signature(graph, node_id, stack=set())


def _input_signature(
    graph: FilmGraphDefinition,
    node_id: str,
    *,
    stack: set[str],
) -> str:
    node = next(node for node in graph.nodes if node.node_id == node_id)
    incoming_edges = sorted(
        (
            edge.source_node_id,
            edge.source_port_id,
            edge.target_port_id,
        )
        for edge in graph.edges
        if edge.target_node_id == node_id
    )
    if node_id in stack:
        parent_signatures: dict[str, str] = {"cycle": "invalid"}
    else:
        parent_signatures = {
            source_id: _input_signature(graph, source_id, stack={*stack, node_id})
            for source_id in sorted({item[0] for item in incoming_edges})
        }
    prefixes = _DIRECT_SOURCE_PREFIXES.get(node.type_id, ())
    direct_sources = {
        key: value
        for key, value in sorted(graph.source_signatures.items())
        if any(key == prefix or key.startswith(prefix) for prefix in prefixes)
    }
    payload = {
        "type": [node.type_id, node.type_version],
        "config": node.config,
        "prompt": node.prompt.model_dump(mode="json", exclude={"updated_at"}),
        "incoming": incoming_edges,
        "parent_signatures": parent_signatures,
        "direct_sources": direct_sources,
        "provider": [node.provider_id, node.model_id],
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def estimate_run(
    graph: FilmGraphDefinition,
    *,
    scope: FilmGraphRunScope,
    target_node_ids: list[str],
    prior_runs: list[FilmGraphRun] | None = None,
    budget_usd: float | None = None,
) -> FilmRunEstimate:
    issues = validate_graph(graph)
    ordered = execution_nodes(graph, scope, target_node_ids)
    successful = {
        (attempt.node_id, attempt.input_signature)
        for run in prior_runs or []
        for attempt in run.attempts
        if attempt.status == FilmGraphRunStatus.SUCCEEDED and attempt.artifact_uris
    }
    cached: list[str] = []
    cost = 0.0
    duration = 0
    definitions = node_definition_map()
    by_id = {node.node_id: node for node in graph.nodes}
    for node_id in ordered:
        node = by_id[node_id]
        signature = input_signature(graph, node_id)
        definition = definitions.get(node.type_id)
        if node.bypassed or (
            definition is not None and definition.cacheable and (node_id, signature) in successful
        ):
            cached.append(node_id)
            continue
        cost += node.estimated_cost_usd or (definition.estimated_cost_usd if definition else 0)
        duration += definition.estimated_duration_s if definition else 0
    missing = [issue.message for issue in issues if issue.code == "missing_input"]
    if budget_usd is not None and cost > budget_usd:
        issues.append(
            _issue(
                "budget_exceeded",
                f"预计费用 ${cost:.2f} 超过项目预算 ${budget_usd:.2f}",
            )
        )
    return FilmRunEstimate(
        graph_revision=graph.revision,
        scope=scope,
        target_node_ids=target_node_ids,
        execution_node_ids=ordered,
        cached_node_ids=cached,
        estimated_cost_usd=round(cost, 4),
        estimated_duration_s=duration,
        missing_inputs=missing,
        validation_issues=issues,
        requires_confirmation=cost > 0,
    )


def build_run(
    graph: FilmGraphDefinition,
    estimate: FilmRunEstimate,
    *,
    confirmed_cost: bool,
    high_priority: bool = False,
) -> FilmGraphRun:
    has_errors = any(
        issue.severity == FilmGraphIssueSeverity.ERROR for issue in estimate.validation_issues
    )
    if has_errors:
        status = FilmGraphRunStatus.BLOCKED
    elif estimate.requires_confirmation and not confirmed_cost:
        status = FilmGraphRunStatus.WAITING_CONFIRMATION
    else:
        status = FilmGraphRunStatus.QUEUED
    run = FilmGraphRun(
        project_id=graph.project_id,
        graph_id=graph.graph_id,
        graph_revision=graph.revision,
        scope=estimate.scope,
        target_node_ids=estimate.target_node_ids,
        high_priority=high_priority,
        status=status,
        confirmed_cost=confirmed_cost,
        estimate=estimate,
    )
    attempts = [
        FilmNodeAttempt(
            run_id=run.run_id,
            node_id=node_id,
            status=FilmGraphRunStatus.SKIPPED
            if node_id in estimate.cached_node_ids
            else FilmGraphRunStatus.QUEUED,
            input_signature=input_signature(graph, node_id),
        )
        for node_id in estimate.execution_node_ids
    ]
    return run.model_copy(update={"attempts": attempts})
