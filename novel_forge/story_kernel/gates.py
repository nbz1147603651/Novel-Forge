"""Gate validators for StoryKernel — completeness and consistency checks.

Two gates:
  - InitTruthGate: validates kernel completeness before chapter generation.
  - PreArchiveTruthGate: validates chapter text consistency before archival.

Neither gate repairs — they only report violations and warnings.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from novel_forge.story_kernel.schemas import StoryKernel


@dataclass
class GateResult:
    """Result of InitTruthGate validation."""

    passed: bool
    violations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class InitTruthGate:
    """Validates StoryKernel completeness before chapter generation can begin."""

    @classmethod
    def validate(cls, kernel: StoryKernel) -> GateResult:
        """Run all validation rules and return aggregated result."""
        violations: list[str] = []
        warnings: list[str] = []

        violations.extend(cls._check_required_fields(kernel))
        entity_warnings = cls._check_entity_completeness(kernel)
        violations.extend(entity_warnings[0])
        warnings.extend(entity_warnings[1])
        violations.extend(cls._check_relationship_completeness(kernel))
        violations.extend(cls._check_timeline_completeness(kernel))
        violations.extend(cls._check_world_rules_completeness(kernel))
        violations.extend(cls._check_relationship_references(kernel))
        violations.extend(cls._check_business_dependency_cycles(kernel))

        return GateResult(
            passed=len(violations) == 0,
            violations=violations,
            warnings=warnings,
        )

    # ── Individual rules ───────────────────────────────

    @staticmethod
    def _check_required_fields(kernel: StoryKernel) -> list[str]:
        """Rule 1: title and premise must be non-empty strings."""
        violations: list[str] = []
        if not kernel.title or not kernel.title.strip():
            violations.append("title 不能为空：请为故事提供标题。")
        if not kernel.premise or not kernel.premise.strip():
            violations.append(
                "premise 不能为空：请提供至少 2-3 句的核心前提描述。"
            )
        return violations

    @staticmethod
    def _check_entity_completeness(
        kernel: StoryKernel,
    ) -> tuple[list[str], list[str]]:
        """Rule 2: entities must contain protagonist and supporting characters.

        Returns (violations, warnings).
        """
        violations: list[str] = []
        warnings: list[str] = []

        if not kernel.entities:
            violations.append(
                "entities 不能为空：至少需要一个主角（protagonist）实体。"
            )
            return violations, warnings

        has_protagonist = False
        supporting_count = 0
        for entity in kernel.entities:
            role = entity.attributes.get("role", "")
            if role == "protagonist":
                has_protagonist = True
            elif role == "supporting":
                supporting_count += 1

        if not has_protagonist:
            violations.append(
                "entities 缺少主角：至少需要一个 role='protagonist' 的角色实体。"
            )

        if supporting_count == 0:
            warnings.append(
                "entities 仅有主角，建议添加至少一个配角（role='supporting'）以丰富叙事。"
            )

        return violations, warnings

    @staticmethod
    def _check_relationship_completeness(kernel: StoryKernel) -> list[str]:
        """Rule 3: protagonist must have at least one relationship."""
        violations: list[str] = []

        protagonist_ids = {
            e.entity_id
            for e in kernel.entities
            if e.attributes.get("role") == "protagonist"
        }

        if not protagonist_ids:
            # Already reported by entity completeness check
            return violations

        if not kernel.relationships:
            violations.append(
                "relationships 不能为空：主角至少需要一个核心关系。"
            )
            return violations

        protagonist_has_rel = any(
            r.source_entity_id in protagonist_ids
            or r.target_entity_id in protagonist_ids
            for r in kernel.relationships
        )

        if not protagonist_has_rel:
            violations.append(
                "protagonist 缺少关系：主角至少需要一个 relationship 连接。"
            )

        return violations

    @staticmethod
    def _check_timeline_completeness(kernel: StoryKernel) -> list[str]:
        """Rule 4: timeline must contain at least one anchor."""
        violations: list[str] = []
        if not kernel.timeline:
            violations.append(
                "timeline 为空：至少需要一个时间线锚点（TimelineAnchor）来确定故事起点。"
            )
        return violations

    @staticmethod
    def _check_world_rules_completeness(kernel: StoryKernel) -> list[str]:
        """Rule 5: world_rules must contain at least 3 hard rules."""
        violations: list[str] = []
        hard_rules = [
            r for r in kernel.world_rules if r.severity == "hard"
        ]
        if len(hard_rules) < 3:
            violations.append(
                f"world_rules 硬性规则不足：需要至少 3 条 severity='hard' 的规则，"
                f"当前仅有 {len(hard_rules)} 条。"
            )
        return violations

    @staticmethod
    def _check_relationship_references(kernel: StoryKernel) -> list[str]:
        """Rule 6: relationship endpoints must reference known entities."""
        violations: list[str] = []
        entity_ids = {entity.entity_id for entity in kernel.entities}

        for rel in kernel.relationships:
            if rel.source_entity_id not in entity_ids:
                violations.append(
                    f"relationship '{rel.relationship_id}' source_entity_id "
                    f"'{rel.source_entity_id}' 未在 entities 中注册。"
                )
            if rel.target_entity_id not in entity_ids:
                violations.append(
                    f"relationship '{rel.relationship_id}' target_entity_id "
                    f"'{rel.target_entity_id}' 未在 entities 中注册。"
                )

        return violations

    @staticmethod
    def _check_business_dependency_cycles(kernel: StoryKernel) -> list[str]:
        """Rule 7: business dependency graph must be acyclic.

        Uses DFS to detect cycles in the directed dependency graph.
        """
        violations: list[str] = []
        deps = kernel.business_dependencies

        if not deps:
            return violations

        # Build adjacency list
        adj: dict[str, list[str]] = {}
        for dep in deps:
            adj.setdefault(dep.source_id, []).append(dep.target_id)

        # DFS cycle detection with coloring: 0=white, 1=gray, 2=black
        color: dict[str, int] = {}
        for dep in deps:
            color.setdefault(dep.source_id, 0)
            color.setdefault(dep.target_id, 0)

        cycle_found = False

        def dfs(node: str) -> bool:
            nonlocal cycle_found
            if cycle_found:
                return True
            color[node] = 1
            for neighbor in adj.get(node, []):
                if color.get(neighbor, 0) == 1:
                    cycle_found = True
                    return True
                if color.get(neighbor, 0) == 0:
                    if dfs(neighbor):
                        return True
            color[node] = 2
            return False

        for node in list(color.keys()):
            if color[node] == 0:
                if dfs(node):
                    violations.append(
                        "business_dependencies 存在循环依赖：依赖图中检测到环路，"
                        "请移除循环依赖。"
                    )
                    break

        return violations


__all__ = ["GateResult", "InitTruthGate", "PreArchiveTruthGate"]


class PreArchiveTruthGate:
    """Validates StoryKernel structural consistency before chapter archival.

    This gate intentionally does not inspect chapter prose for semantic
    contradictions. Semantic checks belong to LLM-backed continuity,
    adjudication, causal, and repair steps. Local validation stays limited to
    deterministic structure: references, future chapter bounds, dependency
    cycles, and required ledger fields.
    """

    @classmethod
    def validate(
        cls,
        chapter_text: str,
        kernel: StoryKernel,
        chapter_number: int,
    ) -> GateResult:
        """Run all pre-archive validation rules and return aggregated result."""
        violations: list[str] = []
        warnings: list[str] = []

        _ = chapter_text
        violations.extend(cls._check_relationship_references(kernel))
        violations.extend(cls._check_object_references(kernel))
        v, w = cls._check_knowledge(kernel, chapter_number)
        violations.extend(v)
        warnings.extend(w)
        v, w = cls._check_promises(kernel)
        violations.extend(v)
        warnings.extend(w)
        v, w = cls._check_timeline(kernel, chapter_number)
        violations.extend(v)
        warnings.extend(w)

        return GateResult(
            passed=len(violations) == 0,
            violations=violations,
            warnings=warnings,
        )

    # ── Helpers ────────────────────────────────────────

    @staticmethod
    def _check_relationship_references(kernel: StoryKernel) -> list[str]:
        """Rule 1: relationship endpoints must reference known entities."""
        violations: list[str] = []
        entity_ids = {entity.entity_id for entity in kernel.entities}
        for rel in kernel.relationships:
            if rel.source_entity_id not in entity_ids:
                violations.append(
                    f"[relationship] relationship '{rel.relationship_id}' source_entity_id "
                    f"'{rel.source_entity_id}' 未在 entities 中注册。"
                )
            if rel.target_entity_id not in entity_ids:
                violations.append(
                    f"[relationship] relationship '{rel.relationship_id}' target_entity_id "
                    f"'{rel.target_entity_id}' 未在 entities 中注册。"
                )

        return violations

    @staticmethod
    def _check_object_references(kernel: StoryKernel) -> list[str]:
        """Rule 2: object ledger entity references must resolve when present."""
        violations: list[str] = []
        entity_ids = {entity.entity_id for entity in kernel.entities}
        for entry in kernel.object_ledger:
            if entry.item_entity_id and entry.item_entity_id not in entity_ids:
                violations.append(
                    f"[object] object_ledger '{entry.entry_id}' item_entity_id "
                    f"'{entry.item_entity_id}' 未在 entities 中注册。"
                )
            if entry.owner_entity_id and entry.owner_entity_id not in entity_ids:
                violations.append(
                    f"[object] object_ledger '{entry.entry_id}' owner_entity_id "
                    f"'{entry.owner_entity_id}' 未在 entities 中注册。"
                )
        return violations

    # ── Rule 3: Knowledge ──────────────────────────────

    @staticmethod
    def _check_knowledge(
        kernel: StoryKernel,
        chapter_number: int,
    ) -> tuple[list[str], list[str]]:
        """Rule 3: Knowledge source_chapter must not exceed chapter_number."""
        violations: list[str] = []
        warnings: list[str] = []

        for entry in kernel.knowledge_ledger:
            if entry.source_chapter > chapter_number:
                entity = kernel.get_entity_by_id(entry.entity_id)
                entity_name = entity.name if entity else entry.entity_id
                violations.append(
                    f"[knowledge] 角色 '{entity_name}' 的知识 '{entry.fact}' "
                    f"来源章节为 {entry.source_chapter}，但当前归档章节为 {chapter_number}，"
                    f"存在未来知识引用。"
                )
        return violations, warnings

    # ── Rule 4: Promises ───────────────────────────────

    @staticmethod
    def _check_promises(
        kernel: StoryKernel,
    ) -> tuple[list[str], list[str]]:
        """Rule 4: Paid promises must have payoff_chapter set."""
        violations: list[str] = []
        warnings: list[str] = []

        for entry in kernel.promise_ledger:
            if entry.status == "paid" and entry.payoff_chapter == 0:
                violations.append(
                    f"[promise] 伏笔 '{entry.description}' 状态为 'paid'，"
                    f"但 payoff_chapter 未设置（为 0），请补充兑现章节。"
                )
        return violations, warnings

    # ── Rule 5: Timeline ───────────────────────────────

    @staticmethod
    def _check_timeline(
        kernel: StoryKernel,
        chapter_number: int,
    ) -> tuple[list[str], list[str]]:
        """Rule 5: Timeline can't point beyond current kernel progress."""
        violations: list[str] = []
        warnings: list[str] = []

        if not kernel.timeline:
            return violations, warnings

        chapters = sorted({t.chapter for t in kernel.timeline})

        for ch in chapters:
            if ch > kernel.current_chapter:
                violations.append(
                    f"[timeline] 时间线包含未来章节 {ch} 的事件，"
                    f"但当前进度为第 {kernel.current_chapter} 章。"
                )

        if len(chapters) >= 2:
            for i in range(len(chapters) - 1):
                gap = chapters[i + 1] - chapters[i]
                if gap >= 5:
                    warnings.append(
                        f"[timeline] 时间线在第 {chapters[i]} 章和第 "
                        f"{chapters[i + 1]} 章之间存在较大间隔（{gap} 章）。"
                    )

        return violations, warnings
