"""ReadingPowerRepairStep — targeted repair for reading power issues."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any, cast

from novel_forge.core.constants import TaskType
from novel_forge.core.schemas.reading_power_repair import ReadingPowerRepairInput
from novel_forge.core.utils.audit_issue import ensure_issue_id
from novel_forge.core.utils.issue_prompt_blocks import build_issues_prompt_block
from novel_forge.obs.logger import get_logger
from novel_forge.pipeline.steps.repair.base import RepairStepBase
from novel_forge.pipeline.steps.repair.forbidden_checker import check_forbidden
from novel_forge.pipeline.steps.repair.text_utils import extract_revised_text, non_space_len

_logger = get_logger("pipeline.reading_power_repair")


@dataclass
class ReadingPowerRepairResult:
    """Repair output: revised text and metadata."""

    revised_text: str
    applied: bool = False
    failure_reason: str | None = None
    issues_addressed: list[Any] = field(default_factory=list)
    forbidden_element_warnings: list[str] = field(default_factory=list)


_PATCHABLE_TYPES = frozenset(
    {
        "hook_too_weak",
        "hook_missing",
        "prev_hook_unfulfilled",
        "payoff_missing",
        "outline_mismatch",
        "information_pacing_slow",
        "information_pacing_stagnant",
        "main_plot_surface",
        "main_plot_stalled",
        "tension_depressed",
        "character_drive_weak",
        "revelation_over_budget",
    }
)

_KNOWN_ISSUE_TYPES = (
    "address_form_mismatch",
    "expression_clarity",
    "forbidden_element_usage",
    "forbidden_element_violation",
    "hook_missing",
    "hook_too_weak",
    "information_pacing_slow",
    "information_pacing_stagnant",
    "main_plot_surface",
    "main_plot_stalled",
    "payoff_missing",
    "prompt_leak",
    "pov_intrusion",
    "prev_hook_unfulfilled",
    "outline_mismatch",
    "revelation_over_budget",
    "tension_depressed",
    "text_repetition",
    "time_marker_invalid",
    "character_drive_weak",
)


def _clean_list(values: list[Any] | tuple[Any, ...] | set[Any] | None) -> list[str]:
    """Return a de-duplicated list of non-empty strings, preserving order."""
    seen: set[str] = set()
    cleaned: list[str] = []
    for value in values or []:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            cleaned.append(text)
    return cleaned


class ReadingPowerRepairStep(RepairStepBase[ReadingPowerRepairInput, ReadingPowerRepairResult]):
    """Repair reading power problems without re-running the whole chapter pipeline."""

    @property
    def step_name(self) -> str:
        return "repair_reading_power"

    def repair_task_type(self) -> TaskType:
        return TaskType.REPAIR_READING_POWER

    def issue_type_fixes(self) -> dict[str, str]:
        return {
            "hook_missing": "补出明确、具体、可感的章尾钩子。",
            "hook_too_weak": "让章尾出现角色必须应对的动作后果、证据发现或选择代价。",
            "payoff_missing": "在章内补充信息、关系、能力或线索兑现。",
            "prev_hook_unfulfilled": "在前中段回应上一章钩子承诺。",
            "outline_mismatch": "按大纲预期重设章尾钩子或微兑现。",
            "information_pacing_slow": "补出一处新线索、关系确认、局势变化或阶段性答案。",
            "information_pacing_stagnant": "补出一处清晰的新信息或局势变化，避免章节原地踏步。",
            "main_plot_surface": "让主线目标、阻力、结果或角色认知至少产生一处可见推进。",
            "main_plot_stalled": "恢复主线行动链，让本章不只铺垫而有明确推进结果。",
            "tension_depressed": "强化关键段落压力、代价、期待或章尾承接压力。",
            "character_drive_weak": "让核心角色做出带动机的选择、试探、拒绝或承担后果的行动。",
            "revelation_over_budget": "将超预算重大揭示后移、拆分或降级为阶段性线索。",
            "prompt_leak": "删除正文中的规划/修复层元语言，改写成角色当下动作、感官、对话或内心活动。",
            "text_repetition": "删除或改写整句级重复，保留一次信息表达并补充新的动作/细节。",
            "pov_intrusion": "将非POV角色的内心活动改为外部行为、表情、动作或语气暗示。",
            "forbidden_element_violation": "将硬禁元素替换为完全不同的意象/句式，不使用近义变体。",
            "forbidden_element_usage": "将近期复用的软禁元素换成新意象；若为有意回环则强化叙事意义。",
            "time_marker_invalid": "把非法时辰刻度改为合法表达，或改成更笼统的时间锚点。",
            "address_form_mismatch": "修正与角色身份不匹配的称呼，保持对话原意。",
            "expression_clarity": "修正明显出戏、说明腔或信息密度低的表达，保持原文风格。",
        }

    @staticmethod
    def _normalise_expected_hook(value: Any) -> dict[str, Any] | None:
        if value is None:
            return None
        if hasattr(value, "model_dump"):
            dumped = value.model_dump(mode="json")
            data = dict(dumped) if isinstance(dumped, dict) else {}
        elif isinstance(value, dict):
            data = dict(value)
        else:
            desc = str(value or "").strip()
            if not desc:
                return None
            data = {
                "hook_type": "",
                "hook_strength": "",
                "hook_description": desc,
            }
        if not any(
            str(data.get(key, "") or "").strip()
            for key in (
                "hook_type",
                "hook_strength",
                "hook_description",
            )
        ):
            return None
        data.setdefault("hook_type", "")
        data.setdefault("hook_strength", "")
        data.setdefault("hook_description", "")
        return data

    @staticmethod
    def _normalise_expected_payoffs(values: list[Any] | None) -> list[dict[str, Any]]:
        payoffs: list[dict[str, Any]] = []
        for value in values or []:
            if value is None:
                continue
            if hasattr(value, "model_dump"):
                data = value.model_dump(mode="json")
            elif isinstance(value, dict):
                data = dict(value)
            else:
                desc = str(value or "").strip()
                if not desc:
                    continue
                data = {
                    "payoff_type": "",
                    "description": desc,
                    "strength": "medium",
                }
            if (
                str(data.get("description", "") or "").strip()
                or str(data.get("payoff_type", "") or "").strip()
            ):
                data.setdefault("payoff_type", "")
                data.setdefault("description", "")
                data.setdefault("strength", "medium")
                payoffs.append(data)
        return payoffs

    @staticmethod
    def _infer_issue_type(summary: str) -> str:
        text = str(summary or "").lower()
        if "hook_missing" in text or "缺少明确钩子" in text or "缺少钩子" in text:
            return "hook_missing"
        if "hook_too_weak" in text or "力度偏弱" in text or "weak" in text:
            return "hook_too_weak"
        if (
            "prev_hook_unfulfilled" in text
            or "上一章钩子" in text
            or "没有被回应" in text
            or "未回应" in text
        ):
            return "prev_hook_unfulfilled"
        if "payoff_missing" in text or "微兑现" in text or "兑现不足" in text:
            return "payoff_missing"
        if "information_pacing_stagnant" in text or "信息释放停滞" in text:
            return "information_pacing_stagnant"
        if "information_pacing_slow" in text or "信息释放偏慢" in text:
            return "information_pacing_slow"
        if "main_plot_stalled" in text or "主线推进停滞" in text:
            return "main_plot_stalled"
        if "main_plot_surface" in text or "主线推进停留在表层" in text:
            return "main_plot_surface"
        if "tension_depressed" in text or "张力低于" in text:
            return "tension_depressed"
        if "character_drive_weak" in text or "角色驱动力偏弱" in text:
            return "character_drive_weak"
        if "revelation_over_budget" in text or "重大揭示超出" in text:
            return "revelation_over_budget"
        if "outline_mismatch" in text or "大纲" in text or "未达成" in text:
            return "outline_mismatch"
        if "prompt_leak" in text or "元语言" in text or "说明腔" in text:
            return "prompt_leak"
        if "text_repetition" in text or "重复" in text:
            return "text_repetition"
        if "pov_intrusion" in text or "非pov" in text or "视角" in text:
            return "pov_intrusion"
        if "forbidden_element_violation" in text or "硬禁" in text:
            return "forbidden_element_violation"
        if "forbidden_element_usage" in text or "软禁" in text:
            return "forbidden_element_usage"
        if "time_marker_invalid" in text or "时辰" in text or "刻度" in text:
            return "time_marker_invalid"
        if "address_form_mismatch" in text or "称呼" in text:
            return "address_form_mismatch"
        if "综合追读力" in text or "overall_score_low" in text:
            return "overall_score_low"
        return "reading_power_issue"

    @staticmethod
    def _infer_issue_location(issue_type: str, summary: str) -> str:
        text = str(summary or "")
        if "第" in text and "段" in text:
            return text
        if issue_type in {"hook_missing", "hook_too_weak", "outline_mismatch"}:
            return "章尾"
        if issue_type == "prev_hook_unfulfilled":
            return "前中段"
        if issue_type == "payoff_missing":
            return "章中"
        if issue_type in {
            "information_pacing_slow",
            "information_pacing_stagnant",
            "main_plot_surface",
            "main_plot_stalled",
            "character_drive_weak",
            "revelation_over_budget",
        }:
            return "章中"
        if issue_type == "tension_depressed":
            return "章尾"
        if issue_type in {
            "address_form_mismatch",
            "expression_clarity",
            "forbidden_element_usage",
            "forbidden_element_violation",
            "prompt_leak",
            "pov_intrusion",
            "text_repetition",
            "time_marker_invalid",
        }:
            return "全文"
        return ""

    @classmethod
    def _issue_to_dict(cls, issue: Any) -> dict[str, Any]:
        if hasattr(issue, "model_dump"):
            dumped_issue = issue.model_dump(mode="json")
            data: dict[str, Any] = dict(dumped_issue) if isinstance(dumped_issue, dict) else {}
        elif is_dataclass(issue) and not isinstance(issue, type):
            data = asdict(cast(Any, issue))
        elif isinstance(issue, dict):
            data = dict(issue)
        else:
            summary = str(issue or "").strip()
            issue_type = cls._infer_issue_type(summary)
            data = {
                "issue_type": issue_type,
                "severity": "medium",
                "summary": summary,
                "evidence": "",
                "fix_suggestion": "",
                "location": cls._infer_issue_location(issue_type, summary),
            }

        summary = str(data.get("summary", "") or "").strip()
        issue_type = str(data.get("issue_type", "") or "").strip().lower()
        if not issue_type:
            issue_type = cls._infer_issue_type(summary)
        data["issue_type"] = issue_type
        data["severity"] = str(data.get("severity", "") or "medium").strip().lower()
        data["summary"] = summary
        data["evidence"] = str(data.get("evidence", "") or "").strip()
        data["fix_suggestion"] = str(data.get("fix_suggestion", "") or "").strip()
        data["location"] = str(
            data.get("location", "") or cls._infer_issue_location(issue_type, summary)
        ).strip()
        data["issue_id"] = str(data.get("issue_id", "") or "").strip()
        data["repair_surface"] = str(data.get("repair_surface", "") or "chapter_text").strip()
        data["status"] = str(data.get("status", "") or "open").strip().lower()
        postconditions = data.get("postconditions")
        data["postconditions"] = postconditions if isinstance(postconditions, list) else []
        return data

    @classmethod
    def _build_issue_context(
        cls,
        issues: list[Any],
        *,
        chapter_number: int = 0,
    ) -> dict[str, Any]:
        issue_dicts = [cls._issue_to_dict(issue) for issue in issues if str(issue or "").strip()]
        typed_issues: dict[str, list[dict[str, Any]]] = {key: [] for key in _KNOWN_ISSUE_TYPES}
        issue_types: list[str] = []
        for issue in issue_dicts:
            if not issue.get("issue_id"):
                issue["issue_id"] = ensure_issue_id(
                    issue,
                    "reading_power",
                    chapter_number=chapter_number,
                    repair_surface="chapter_text",
                )
            issue_type = str(issue.get("issue_type", "") or "reading_power_issue").lower()
            typed_issues.setdefault(issue_type, []).append(issue)
            if issue_type not in issue_types:
                issue_types.append(issue_type)
        return {
            "issues": [issue["summary"] for issue in issue_dicts],
            "issue_types": issue_types,
            "typed_issues": typed_issues,
        }

    @staticmethod
    def _effective_forbidden(
        hard: list[str],
        soft: list[str],
        intentional: list[str],
    ) -> tuple[list[str], list[str], list[str]]:
        intentional_clean = _clean_list(intentional)
        intentional_set = set(intentional_clean)
        hard_clean = [item for item in _clean_list(hard) if item not in intentional_set]
        hard_set = set(hard_clean)
        soft_clean = [
            item
            for item in _clean_list(soft)
            if item not in intentional_set and item not in hard_set
        ]
        return hard_clean, soft_clean, intentional_clean

    def build_repair_context(self, input_data: ReadingPowerRepairInput) -> dict[str, Any]:
        orig_len = non_space_len(input_data.chapter_text)
        issue_context = self._build_issue_context(
            input_data.issues,
            chapter_number=input_data.chapter_number,
        )
        word_count_min, word_count_max, _guard_lo, _guard_hi = self.compute_word_bounds(
            orig_len,
            set(issue_context["issue_types"]),
        )
        if input_data.word_count_min:
            word_count_min = input_data.word_count_min
        if input_data.word_count_max:
            word_count_max = input_data.word_count_max

        hard_forbidden, soft_forbidden, intentional_callbacks = self._effective_forbidden(
            input_data.forbidden_elements or [],
            input_data.forbidden_elements_soft or [],
            input_data.intentional_callbacks or [],
        )
        from novel_forge.pipeline.long.services.constraints.constraint_router import (
            build_repair_cards,
        )

        result: dict[str, Any] = {
            "chapter_number": input_data.chapter_number,
            "chapter_text": input_data.chapter_text,
            **issue_context,
            "expected_hook": self._normalise_expected_hook(input_data.expected_hook),
            "expected_payoffs": self._normalise_expected_payoffs(input_data.expected_payoffs),
            "forbidden_elements": hard_forbidden,
            "forbidden_elements_soft": soft_forbidden,
            "intentional_callbacks": intentional_callbacks,
            "word_count_min": word_count_min,
            "word_count_max": word_count_max,
            "previous_hook_description": input_data.previous_hook_description or "",
            "memory_guidance": input_data.memory_guidance or {},
            "repair_attempt_guidance": input_data.repair_attempt_guidance or {},
            "structured_issues_block": build_issues_prompt_block(
                list(input_data.issues or []),
                header="待修复追读力问题详情：",
                include_evidence=True,
                include_fix_actions=True,
            ),
            "character_whitelist_note": (
                f"已知角色列表：{', '.join(input_data.character_profiles)}，修复时不得引入未出现在列表中的新角色。"
                if input_data.character_profiles
                else ""
            ),
            "stage_cards": build_repair_cards(
                stage="reading_power_repair",
                packet=input_data.chapter_state_packet,
                chapter_outline=input_data.chapter_outline,
                bridge=input_data.chapter_bridge,
                plan=input_data.chapter_plan,
                style_profile=input_data.style_profile,
                editorial_contract=input_data.editorial_contract,
                reading_power_hint=input_data.reading_power_hint,
                kernel_context=input_data.kernel_context,
                chapter_source_slice=input_data.chapter_source_slice,
                settings=self.settings,
            ),
        }

        # Merge StoryKernel field slices (explicit fields take precedence)
        if input_data.kernel_context is not None:
            for key, value in input_data.kernel_context.items():
                if key not in result and value is not None:
                    result[key] = value

        return result

    def compute_word_bounds(
        self,
        orig_len: int,
        issue_types: set[str],
    ) -> tuple[int, int, float, float]:
        if orig_len > 0:
            word_count_min = max(int(orig_len * 0.85), 800)
            word_count_max = int(orig_len * 1.15)
        else:
            word_count_min = 800
            word_count_max = 2000
        guard_lo, guard_hi = 0.70, 1.30
        return word_count_min, word_count_max, guard_lo, guard_hi

    def adapt_issue_for_patch(self, issue: Any) -> Any:
        return issue

    # ── Window repair helpers ──────────────────────────────────────────────────

    @staticmethod
    def _split_paragraphs(text: str) -> list[str]:
        """Split chapter text into paragraphs, preserving blank-line separators."""
        return [p for p in text.split("\n\n") if p.strip()]

    @staticmethod
    def _location_to_para_idx(location: str, paragraphs: list[str]) -> int | None:
        """Parse a location string (e.g. "第5段", "第3-4段") into a 0-based paragraph index.

        Returns None when the location cannot be parsed.
        """
        import re

        if not location or not paragraphs:
            return None

        loc = location.strip()

        # "第N-M段" or "第N—M段" → use first number
        m = re.search(r"第\s*(\d+)", loc)
        if m:
            n = int(m.group(1)) - 1  # 1-based → 0-based
            return max(0, min(n, len(paragraphs) - 1))

        # keywords
        if any(k in loc for k in ("开头", "首段", "起首")):
            return 0
        if any(k in loc for k in ("结尾", "末段", "尾段", "最后", "章尾")):
            return len(paragraphs) - 1
        if any(k in loc for k in ("章中", "中部", "中间")):
            mid = len(paragraphs) // 2
            return mid

        return None

    def _locate_issue_paragraph(
        self,
        issue_summary: str,
        paragraphs: list[str],
    ) -> int | None:
        """Locate the paragraph index for an issue by parsing its summary string.

        Returns 0-based paragraph index or None.
        """
        import re

        if not issue_summary or not paragraphs:
            return None

        summary = issue_summary.strip()

        # Try to extract "第X段" pattern directly from summary
        m = re.search(r"第\s*(\d+)\s*段", summary)
        if m:
            n = int(m.group(1)) - 1  # 1-based → 0-based
            if 0 <= n < len(paragraphs):
                return n

        # Try location keywords
        if any(k in summary for k in ("开头", "首段", "起首")):
            return 0
        if any(k in summary for k in ("结尾", "末段", "尾段", "最后", "章尾")):
            return len(paragraphs) - 1
        if any(k in summary for k in ("章中", "中部", "中间")):
            return len(paragraphs) // 2

        return None

    def _issue_paragraph_index(self, issue: Any, paragraphs: list[str]) -> int | None:
        issue_data = self._issue_to_dict(issue)
        location = str(issue_data.get("location", "") or "").strip()
        idx = self._location_to_para_idx(location, paragraphs)
        if idx is not None:
            return idx
        summary = str(issue_data.get("summary", "") or "").strip()
        return self._locate_issue_paragraph(summary, paragraphs)

    def _should_use_window_repair(self, issues: list[Any], current_text: str) -> bool:
        paragraphs = self._split_paragraphs(current_text)
        if len(paragraphs) < 4:
            return False
        return any(self._issue_paragraph_index(issue, paragraphs) is not None for issue in issues)

    async def _window_repair(
        self,
        input_data: ReadingPowerRepairInput,
        issues: list[str],
        current_text: str,
        window_size: int = 3,
    ) -> str:
        """Enhanced window repair with paragraph-level location inference and clustering.

        Args:
            input_data: Repair input data containing style_profile and other configs.
            issues: List of issue summaries to address.
            current_text: The current chapter text to repair.
            window_size: Window radius for clustering nearby issues.

        Returns:
            Revised text after repair attempt.
        """
        paragraphs = self._split_paragraphs(current_text)
        if not paragraphs:
            return current_text

        # Parse each issue summary to find location hints
        located: list[tuple[int, Any]] = []
        unlocated: list[Any] = []
        for issue in issues:
            idx = self._issue_paragraph_index(issue, paragraphs)
            if idx is not None:
                located.append((idx, issue))
            else:
                unlocated.append(issue)

        if not located:
            _logger.info(
                "reading_power_window_repair: no parseable locations, falling back to fulltext",
            )
            return await self._fulltext_repair(input_data, issues, current_text)

        # Group into non-overlapping window clusters
        located.sort(key=lambda x: x[0])
        clusters: list[list[tuple[int, str]]] = []
        cur_cluster: list[tuple[int, str]] = [located[0]]
        cur_end = located[0][0] + window_size
        for idx, issue in located[1:]:
            if idx - window_size <= cur_end:
                cur_cluster.append((idx, issue))
                cur_end = max(cur_end, idx + window_size)
            else:
                clusters.append(cur_cluster)
                cur_cluster = [(idx, issue)]
                cur_end = idx + window_size
        clusters.append(cur_cluster)

        _logger.info(
            "reading_power_window_repair: %d issues → %d clusters",
            len(located),
            len(clusters),
        )

        # Process clusters in REVERSE order so splicing doesn't shift indices
        result_paragraphs = list(paragraphs)
        failed_issues: list[Any] = list(unlocated)

        for cluster in reversed(clusters):
            cluster_indices = [idx for idx, _ in cluster]
            cluster_issues = [iss for _, iss in cluster]

            raw_start = min(cluster_indices) - window_size
            raw_end = max(cluster_indices) + window_size
            window_start = max(0, raw_start)
            window_end = min(len(result_paragraphs) - 1, raw_end)

            prefix_paras = result_paragraphs[:window_start]
            window_paras = result_paragraphs[window_start : window_end + 1]
            suffix_paras = result_paragraphs[window_end + 1 :]

            if not window_paras:
                failed_issues.extend(cluster_issues)
                continue

            window_text = "\n\n".join(window_paras)
            window_before = "\n\n".join(prefix_paras[-5:]) if prefix_paras else ""
            window_after = "\n\n".join(suffix_paras[:5]) if suffix_paras else ""

            _logger.info(
                "reading_power_window_repair: cluster paragraphs [%d-%d]/%d (%d issues)",
                window_start,
                window_end,
                len(result_paragraphs) - 1,
                len(cluster_issues),
            )

            try:
                revised_window = await self._fulltext_repair(
                    input_data,
                    cluster_issues,
                    window_text,
                    window_before=window_before,
                    window_after=window_after,
                )
            except Exception as exc:
                _logger.warning(
                    "reading_power_window_repair: cluster [%d-%d] failed | error=%s",
                    window_start,
                    window_end,
                    exc,
                )
                failed_issues.extend(cluster_issues)
                continue

            # Sanity-check: revised window must not be suspiciously short
            if not revised_window or len(revised_window) < len(window_text) * 0.4:
                _logger.warning(
                    "reading_power_window_repair: LLM returned suspiciously short window (%d vs %d), "
                    "skipping cluster [%d-%d]",
                    len(revised_window),
                    len(window_text),
                    window_start,
                    window_end,
                )
                failed_issues.extend(cluster_issues)
                continue

            # Splice revised window back
            revised_window_paras = [p for p in revised_window.split("\n\n") if p.strip()]
            result_paragraphs = prefix_paras + revised_window_paras + suffix_paras

        final_text = "\n\n".join(result_paragraphs)

        # Handle failed cluster issues
        if failed_issues:
            if final_text == current_text:
                _logger.info(
                    "reading_power_window_repair: all clusters failed, falling back to fulltext",
                )
                return await self._fulltext_repair(input_data, issues, current_text)
            else:
                _logger.info(
                    "reading_power_window_repair: %d unresolved issue(s) after partial cluster success, "
                    "running targeted fulltext pass",
                    len(failed_issues),
                )
                final_text = await self._fulltext_repair(
                    input_data,
                    failed_issues,
                    final_text,
                )

        return final_text if final_text else current_text

    async def _fulltext_repair(
        self,
        input_data: ReadingPowerRepairInput,
        issues: list[Any],
        current_text: str,
        window_before: str = "",
        window_after: str = "",
    ) -> str:
        """Full-text repair with optional window context."""
        _logger.info("reading_power_window_repair: falling back to full-text repair")
        max_tokens = self._dynamic_max_tokens(
            TaskType.REPAIR_READING_POWER,
            len(current_text),
            prompt_overhead=3000,
            max_cap=getattr(self._settings, "continuity_repair_max_tokens", 16384),
            safety_margin=0.80,
            min_tokens=6144,
        )

        context = self.build_repair_context(input_data)
        context["chapter_text"] = current_text
        context.update(
            self._build_issue_context(issues, chapter_number=input_data.chapter_number)
        )
        _window_len = non_space_len(current_text)
        if window_before or window_after:
            _word_count_min = max(1, int(_window_len * 0.70))
            _word_count_max = max(_word_count_min + 1, int(_window_len * 1.35))
        else:
            _word_count_min, _word_count_max, _, _ = self.compute_word_bounds(
                _window_len,
                set(context["issue_types"]),
            )
        context["word_count_min"] = _word_count_min
        context["word_count_max"] = _word_count_max
        if window_before:
            context["window_before"] = window_before
        if window_after:
            context["window_after"] = window_after

        try:
            response = await self._call_with_retry(
                self.repair_task_type(),
                context,
                max_tokens=max_tokens,
                temperature=getattr(self._settings, "temp_repair_reading_power", 0.35),
            )
        except Exception as exc:
            _logger.warning("reading_power_window_repair: fulltext LLM call failed | error=%s", exc)
            return current_text

        raw_content = response.get("content", "") if isinstance(response, dict) else ""
        revised_text, _ = extract_revised_text(raw_content)

        is_valid, _ = self.validate_revised_text(current_text, revised_text)
        if not is_valid:
            return current_text

        return revised_text if revised_text else current_text

    @staticmethod
    def validate_revised_text(original: str, revised: str) -> tuple[bool, str]:
        if not revised or not revised.strip():
            return False, "LLM 空响应，未生成有效修复文本。"

        original_len = non_space_len(original)
        revised_len = non_space_len(revised)

        if original_len > 0 and revised_len < original_len * 0.5:
            return False, (f"修复结果严重截断（{revised_len} vs 原文 {original_len} 非空白字符）。")

        planning_terms = [
            "第一步",
            "第二步",
            "第三步",
            "第四步",
            "第五步",
            "首先",
            "其次",
            "再次",
            "最后",
            "综上所述",
            "修复方案",
            "修改建议",
            "改进措施",
            "接下来",
            "Step 1",
            "Step 2",
            "Step 3",
        ]
        for term in planning_terms:
            if term in revised:
                return False, f"修复结果包含规划术语（{term}），非有效正文。"

        return True, ""

    def should_skip(self, input_data: ReadingPowerRepairInput) -> bool | None:
        return None

    @classmethod
    def _is_patch_candidate(cls, issue: Any) -> bool:
        return False

    async def _execute(self, input_data: ReadingPowerRepairInput) -> ReadingPowerRepairResult:
        skip = self.should_skip(input_data)
        if skip:
            return ReadingPowerRepairResult(
                revised_text=input_data.chapter_text,
                applied=False,
                failure_reason=str(skip),
            )

        # Check if there are no issues to fix
        if not input_data.issues:
            return ReadingPowerRepairResult(
                revised_text=input_data.chapter_text,
                applied=False,
                failure_reason="无需修复",
            )

        context = self.build_repair_context(input_data)

        if bool(getattr(self._settings, "patch_first_repair", True)):
            _patch_issues = [iss for iss in input_data.issues if self._is_patch_candidate(iss)]
            _fulltext_issues = [
                iss for iss in input_data.issues if not self._is_patch_candidate(iss)
            ]
        else:
            _patch_issues = []
            _fulltext_issues = list(input_data.issues)

        working_text = input_data.chapter_text

        if _patch_issues:
            try:
                from novel_forge.pipeline.long.services.constraints.cognitive_constraints import (
                    cognitive_constraints_from_contract,
                )

                (
                    revised,
                    patches_applied,
                    patches_attempted,
                    fallback,
                ) = await self._run_patch_repair(
                    chapter_number=1,
                    chapter_text=working_text,
                    issues=_patch_issues,
                    style_profile=input_data.style_profile,
                    cognitive_constraints=cognitive_constraints_from_contract(
                        getattr(input_data.chapter_state_packet, "chapter_contract", {})
                    ),
                )
                if not fallback and patches_applied > 0:
                    working_text = revised
                    _logger.info(
                        "reading_power_repair: patch path applied %d/%d patches",
                        patches_applied,
                        patches_attempted,
                    )
            except Exception as exc:
                _logger.warning("reading_power_repair: patch path failed | error=%s", exc)

        if _patch_issues and not _fulltext_issues and working_text != input_data.chapter_text:
            return ReadingPowerRepairResult(
                revised_text=working_text,
                applied=True,
                issues_addressed=input_data.issues,
            )

        # If nothing left for full-text and patch didn't change anything
        if not _fulltext_issues and working_text == input_data.chapter_text:
            return ReadingPowerRepairResult(
                revised_text=input_data.chapter_text,
                applied=False,
                failure_reason="所有问题均为 patch 类型，但 patch 未产生修改。",
                issues_addressed=input_data.issues,
            )

        # ── Full-text path ──
        max_tokens = self._dynamic_max_tokens(
            TaskType.REPAIR_READING_POWER,
            len(working_text),
            prompt_overhead=3000,
            max_cap=getattr(self._settings, "continuity_repair_max_tokens", 16384),
            safety_margin=0.80,
            min_tokens=6144,
        )

        # Update context with working_text (may have been modified by patch)
        context["chapter_text"] = working_text
        context.update(self._build_issue_context(_fulltext_issues))

        if self._should_use_window_repair(_fulltext_issues, working_text):
            try:
                revised_text = await self._window_repair(
                    input_data,
                    _fulltext_issues,
                    working_text,
                )
            except Exception as exc:
                _logger.warning("reading_power_repair window path failed | error=%s", exc)
                revised_text = working_text
        else:
            # Call LLM
            try:
                response = await self._call_with_retry(
                    self.repair_task_type(),
                    context,
                    max_tokens=max_tokens,
                    temperature=getattr(self._settings, "temp_repair_reading_power", 0.35),
                )
            except Exception as exc:
                _logger.warning("reading_power_repair LLM call failed | error=%s", exc)
                return ReadingPowerRepairResult(
                    revised_text=working_text,
                    applied=False,
                    failure_reason=f"LLM 调用失败: {exc}",
                    issues_addressed=input_data.issues,
                )

            # Extract revised text from raw response
            raw_content = response.get("content", "") if isinstance(response, dict) else ""
            revised_text, _ = extract_revised_text(raw_content)

        # Validate revised text
        is_valid, failure_reason = self.validate_revised_text(working_text, revised_text)
        if not is_valid:
            _logger.warning(
                "reading_power_repair validation failed | reason=%s",
                failure_reason,
            )
            return ReadingPowerRepairResult(
                revised_text=working_text,
                applied=False,
                failure_reason=failure_reason,
                issues_addressed=input_data.issues,
            )

        hard_forbidden, _soft_forbidden, _intentional_callbacks = self._effective_forbidden(
            input_data.forbidden_elements or [],
            input_data.forbidden_elements_soft or [],
            input_data.intentional_callbacks or [],
        )
        forbidden_warnings: list[str] = []
        if hard_forbidden and revised_text != working_text:
            orig_len = non_space_len(working_text)
            word_count_min, word_count_max, guard_lo, guard_hi = self.compute_word_bounds(
                orig_len,
                set(context["issue_types"]),
            )
            revised_text, remaining_warnings = await self._check_and_retry_forbidden(
                revised_text,
                hard_forbidden,
                context,
                orig_len=orig_len,
                word_count_min=word_count_min,
                word_count_max=word_count_max,
                guard_lo=guard_lo,
                guard_hi=guard_hi,
                max_tokens=max_tokens,
                temperature=getattr(self._settings, "temp_repair_reading_power", 0.35),
            )
            forbidden_warnings.extend(remaining_warnings)

        # Check for forbidden elements using shared checker
        forbidden_hits = check_forbidden(revised_text, hard_forbidden)
        forbidden_warnings.extend(hit for hit in forbidden_hits if hit not in forbidden_warnings)
        if forbidden_warnings:
            _logger.warning(
                "reading_power_repair: forbidden elements detected: %s",
                forbidden_warnings,
            )
            return ReadingPowerRepairResult(
                revised_text=working_text,
                applied=False,
                failure_reason="修复结果仍包含硬禁元素，已回滚至修复前文本。",
                issues_addressed=input_data.issues,
                forbidden_element_warnings=forbidden_warnings,
            )

        applied = revised_text != working_text
        final_failure_reason: str | None = None if applied else "修复结果与原文相同。"

        return ReadingPowerRepairResult(
            revised_text=revised_text,
            applied=applied,
            failure_reason=final_failure_reason,
            issues_addressed=input_data.issues,
            forbidden_element_warnings=forbidden_warnings,
        )
