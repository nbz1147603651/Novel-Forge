"""Engine-owned token analytics aggregation and preference persistence."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping

from novel_forge.persistence.filesystem import atomic_write_text

_DEFAULT_PREFS: dict[str, Any] = {
    "currency": "CNY",
    "price_per_million": 8.0,
    "price_unit": "million",
    "model_price_per_million": {},
    "model_price_unit": {},
    "step_waterfall_filter": "all",
    "exchange_rates": {
        "CNY": 1.0,
        "USD": 0.14,
        "EUR": 0.13,
        "GBP": 0.11,
        "JPY": 21.0,
    },
}

_CURRENCY_SYMBOLS: dict[str, str] = {
    "CNY": "¥",
    "USD": "$",
    "EUR": "€",
    "GBP": "£",
    "JPY": "¥",
}

_DEFAULT_MODEL_PRICE_KEY = "__default__"
_STEP_FILTER_KEYS: frozenset[str] = frozenset({"all", "init", "chapter", "repair"})

_I18N: dict[str, str] = {
    "title": "Token 成本追踪",
    "model": "模型",
    "price": "每百万 Token 价格（CNY）",
    "model_default": "默认（未单独设置）",
    "step_filter": "瀑布筛选",
    "filter_all": "全部",
    "filter_init": "立项",
    "filter_chapter": "正文",
    "filter_repair": "修复",
    "provider_unknown": "未知供应商",
    "currency": "货币",
    "exchange": "汇率（1 CNY =）",
    "save": "保存设置",
    "save_dirty": "保存设置 *",
    "refresh": "刷新",
    "tab_overview": "总览",
    "tab_steps": "步骤明细",
    "tab_models": "模型明细",
    "tab_pricing": "模型价格设置",
    "pricing_hint": "在此统一管理默认单价与各模型单价；改动会立即影响总览、模型明细与成本环图。",
    "pricing_all_models": "参与模型价格（批量设置）",
    "pricing_none": "暂无模型调用记录，完成一次任务后可在此批量设置。",
    "from_start": "统计范围：自项目首个运行起",
    "no_data": "暂无可统计的运行日志。完成一次任务后此处将显示 Token 明细。",
    "metric_tokens": "累计 Token",
    "metric_prompt": "输入 Token",
    "metric_completion": "输出 Token",
    "metric_calls": "模型调用",
    "metric_runs": "运行次数",
    "metric_steps": "步骤记录",
    "metric_cost": "估算成本",
    "metric_logged_cost": "日志成本(USD)",
    "metric_recovered": "失败回填 Token",
    "metric_unattributed": "未拆分 Token",
    "run_history": "运行历史",
    "status_mix": "状态消耗占比",
    "recovered_hint": "失败轮次中的 Token 已由 model_calls 回填",
    "source_trace": "来源：trace_summary",
    "source_fallback": "来源：model_calls 回填",
    "source_verified": "来源：model_calls 校验",
    "source_reconciled": "来源：model_calls 对账校正",
    "reconciled_hint": "trace_summary 与逐次调用记录不一致，已按逐次调用对账",
    "split_coverage": "Token 拆分覆盖 {coverage:.1%}",
    "step_dist": "步骤分布",
    "model_dist": "模型分布",
    "model_cost_ring": "模型成本环图",
    "model_cost_ring_hint": "按当前“每模型单价”估算，金额单位 CNY。",
    "hover_hint": "悬停扇区或图例可查看明细（模型、单价、成本、占比）。",
    "step_waterfall": "步骤瀑布图",
    "step_waterfall_hint": "左至右为累计消耗进度，右侧显示累计占比。",
    "step_waterfall_filtered_by": "当前筛选：{label}",
    "step_waterfall_empty": "当前筛选下暂无可展示步骤。",
    "other_bucket": "其他",
    "outline_scope_title": "大纲章节覆盖",
    "outline_scope_desc": "区间显示为“某次分批/续写/修复任务”的处理范围，并非总章数起止。",
    "outline_scope_value": "覆盖章节：{start}-{end}章（共 {segments} 个区间）",
    "col_started": "开始时间",
    "col_kind": "任务",
    "col_chapter": "章节",
    "col_tokens": "Tokens",
    "col_prompt": "输入",
    "col_completion": "输出",
    "col_status": "状态",
    "col_steps": "步骤数",
    "col_step": "步骤",
    "col_calls": "调用数",
    "col_share": "占比",
    "col_price": "单价(CNY/百万)",
    "col_cost": "估算成本(CNY)",
    "col_model_raw": "模型ID",
    "status_success": "成功",
    "status_error": "失败",
    "status_running": "运行中",
    "status_other": "未知",
    "unit_million": "每百万",
    "unit_thousand": "每千",
}


def _coerce_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(value, 0)
    if isinstance(value, float):
        return max(int(value), 0)
    try:
        return max(int(str(value).strip()), 0)
    except Exception:
        return 0


def _coerce_float(value: Any) -> float:
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return max(float(value), 0.0)
    try:
        return max(float(str(value).strip()), 0.0)
    except Exception:
        return 0.0


def _normalize_step_filter(value: Any) -> str:
    key = str(value or "").strip().lower()
    return key if key in _STEP_FILTER_KEYS else "all"


def _step_filter_group_for_kind(kind: str) -> str | None:
    kind_key = str(kind or "").strip()
    if kind_key == "init_long":
        return "init"
    _chapter_kinds = {
        "run_chapter",
        "prepare_chapter",
        "resolve_chapter_checkpoint",
        "resolve_chapter_checkpoint_finalize",
    }
    if kind_key in _chapter_kinds:
        return "chapter"
    if kind_key in {
        "repair_continuity",
        "repair_causal",
        "repair_issues",
        "reevaluate_chapter",
        "polish_chapter",
        "book_consistency",
        "book_editorial_audit",
        "global_repair_queue",
    }:
        return "repair"
    return None


def _step_tokens_for_filter(step_item: Mapping[str, Any], filter_key: str) -> int:
    normalized = _normalize_step_filter(filter_key)
    if normalized == "all":
        return _coerce_int(step_item.get("tokens"))
    kind_tokens = step_item.get("kind_tokens")
    if isinstance(kind_tokens, dict):
        return _coerce_int(kind_tokens.get(normalized))
    return 0


def _model_key(provider: Any, model: Any) -> str:
    provider_clean = str(provider or "").strip().lower()
    model_clean = str(model or "").strip()
    key = f"{provider_clean}/{model_clean}".strip("/")
    return key or "unknown/unknown"


def _extract_outline_range(step_key: str) -> tuple[int, int] | None:
    for prefix in ("plan_outline_batch_", "plan_outline_continue_", "plan_outline_repair_"):
        if not step_key.startswith(prefix):
            continue
        suffix = step_key[len(prefix) :]
        parts = suffix.split("_", 1)
        if len(parts) != 2:
            return None
        try:
            start = int(parts[0])
            end = int(parts[1])
        except ValueError:
            return None
        if start <= 0 or end <= 0:
            return None
        if end < start:
            start, end = end, start
        return start, end
    return None


def _discover_profiles_path(project_dir: Path) -> Path | None:
    for parent in [project_dir, *project_dir.parents]:
        candidate = parent / "model_profiles.json"
        if candidate.is_file():
            return candidate
    return None


def _load_model_display_names(project_dir: Path) -> dict[str, str]:
    path = _discover_profiles_path(project_dir)
    if path is None:
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}

    mapping: dict[str, str] = {}
    profiles = payload.get("profiles")
    if not isinstance(profiles, list):
        return mapping
    for item in profiles:
        if not isinstance(item, dict):
            continue
        key = _model_key(item.get("provider"), item.get("model_id"))
        display_name = str(item.get("display_name", "") or "").strip()
        if not display_name:
            continue
        mapping.setdefault(key, display_name)
    return mapping


def _prefs_path(project_dir: Path) -> Path:
    return project_dir / "states" / "token_dashboard_preferences.json"


def load_token_dashboard_prefs(project_dir: Path) -> dict[str, Any]:
    prefs: dict[str, Any] = json.loads(json.dumps(_DEFAULT_PREFS, ensure_ascii=False))
    path = _prefs_path(project_dir)
    if not path.exists():
        return prefs
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return prefs
    if not isinstance(raw, dict):
        return prefs

    currency = str(raw.get("currency", prefs["currency"]) or "").strip().upper()
    if currency in _CURRENCY_SYMBOLS:
        prefs["currency"] = currency

    # price_per_million: accept both the old {zh/en} dict and the new flat float
    ppm_raw = raw.get("price_per_million")
    if isinstance(ppm_raw, dict):
        # migrate from old per-language format — prefer "zh" value
        legacy = _coerce_float(ppm_raw.get("zh") or ppm_raw.get("en"))
        if legacy > 0:
            prefs["price_per_million"] = legacy
    elif ppm_raw is not None:
        value = _coerce_float(ppm_raw)
        if value > 0:
            prefs["price_per_million"] = value

    # model_price_per_million: accept both old {zh:{}/en:{}} and new flat dict
    model_ppm = raw.get("model_price_per_million")
    if isinstance(model_ppm, dict):
        # detect old format: keys are "zh" or "en" with dict values
        if any(k in model_ppm and isinstance(model_ppm[k], dict) for k in ("zh", "en")):
            source = model_ppm.get("zh") or model_ppm.get("en") or {}
        else:
            source = model_ppm
        if isinstance(source, dict):
            for model_key, price in source.items():
                model_key_clean = str(model_key or "").strip()
                price_value = _coerce_float(price)
                if model_key_clean and price_value > 0:
                    prefs["model_price_per_million"][model_key_clean] = price_value

    unit = str(raw.get("price_unit", "million") or "million").strip().lower()
    prefs["price_unit"] = "thousand" if unit == "thousand" else "million"
    model_units = raw.get("model_price_unit")
    if isinstance(model_units, dict):
        for _mk, _u in model_units.items():
            _mk_clean = str(_mk or "").strip()
            _u_clean = str(_u or "million").strip().lower()
            if _mk_clean:
                prefs["model_price_unit"][_mk_clean] = (
                    "thousand" if _u_clean == "thousand" else "million"
                )
    prefs["step_waterfall_filter"] = _normalize_step_filter(raw.get("step_waterfall_filter", "all"))

    rates = raw.get("exchange_rates")
    if isinstance(rates, dict):
        for key in _CURRENCY_SYMBOLS:
            value = _coerce_float(rates.get(key))
            if value > 0:
                prefs["exchange_rates"][key] = value

    prefs["exchange_rates"]["CNY"] = 1.0
    return prefs


def save_token_dashboard_prefs(project_dir: Path, prefs: dict[str, Any]) -> None:
    path = _prefs_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        atomic_write_text(path, json.dumps(prefs, ensure_ascii=False, indent=2))
    except OSError:
        return


def _step_label(step_key: str, language: str) -> str:
    if language == "zh":
        return step_key.replace("_", " ").strip() or step_key
    return step_key.replace("_", " ").strip().title() or step_key


def _kind_label(kind: str, language: str) -> str:
    kind_clean = kind.strip()
    if language != "zh":
        return kind_clean.replace("_", " ").replace("desktop-", "").title() or kind_clean
    zh_map = {
        "run_short": "短篇生成",
        "init_long": "长篇立项",
        "run_chapter": "章节正文",
        "prepare_chapter": "章节方案",
        "resolve_chapter_checkpoint": "章节决策",
        "repair_continuity": "连贯性修复",
        "repair_causal": "因果链修复",
        "repair_issues": "问题修复",
        "reevaluate_chapter": "章节重评估",
        "polish_chapter": "章节润色",
        "book_consistency": "全书一致性",
        "book_editorial_audit": "出版编辑审查",
        "global_repair_queue": "全书审计修复",
        "export_book": "导出",
    }
    return zh_map.get(kind_clean, kind_clean)


def _trace_summary_score(summary: Mapping[str, Any]) -> int:
    score = 0
    steps = summary.get("steps")
    if isinstance(steps, list) and steps:
        score += 100 + len(steps)
    score += _coerce_int(summary.get("total_tokens")) // 1000
    score += _coerce_int(summary.get("total_prompt_tokens")) // 1000
    score += _coerce_int(summary.get("total_completion_tokens")) // 1000
    score += _coerce_int(summary.get("completed_step_count"))
    completed_steps = summary.get("completed_steps")
    if isinstance(completed_steps, list):
        score += len(completed_steps)
    return score


def _best_trace_summary(summary_payload: dict[str, Any]) -> dict[str, Any]:
    direct = summary_payload.get("trace_summary")
    candidates: list[dict[str, Any]] = []
    if isinstance(direct, dict):
        candidates.append(direct)

    def _visit(node: Any, *, depth: int) -> None:
        if depth > 8:
            return
        if isinstance(node, dict):
            trace = node.get("trace_summary")
            if isinstance(trace, dict):
                candidates.append(trace)
            for value in node.values():
                _visit(value, depth=depth + 1)
            return
        if isinstance(node, list):
            for item in node[:120]:
                _visit(item, depth=depth + 1)

    _visit(summary_payload.get("result"), depth=0)

    best: dict[str, Any] = {}
    best_score = -1
    for candidate in candidates:
        score = _trace_summary_score(candidate)
        if score > best_score:
            best = candidate
            best_score = score
    return best


def _fallback_step_name_from_call(call_payload: dict[str, Any], call_path: Path) -> str:
    task = str(call_payload.get("task", "") or "").strip()
    if task:
        return task
    stem = call_path.stem
    if "_" in stem:
        stem = stem.split("_", 1)[1]
    for suffix in ("_error", "_timeout", "_done"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
    return stem.strip() or "unknown_step"


def _collect_model_calls_fallback(run_dir: Path) -> dict[str, Any]:
    model_calls_dir = run_dir / "model_calls"
    if not model_calls_dir.is_dir():
        return {
            "total_tokens": 0,
            "total_prompt_tokens": 0,
            "total_completion_tokens": 0,
            "total_cost_usd": 0.0,
            "step_count": 0,
            "steps": [],
            "models": [],
            "call_count": 0,
        }

    total_tokens = 0
    total_prompt_tokens = 0
    total_completion_tokens = 0
    total_cost_usd = 0.0
    call_count = 0

    step_acc: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "step": "",
            "tokens": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "cost_usd": 0.0,
            "calls": 0,
        }
    )
    model_acc: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "key": "",
            "label": "",
            "provider": "",
            "model": "",
            "tokens": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "cost_usd": 0.0,
            "calls": 0,
        }
    )

    call_records: dict[str, tuple[Path, dict[str, Any]]] = {}
    for call_path in sorted(model_calls_dir.rglob("*.json")):
        try:
            raw = json.loads(call_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        if not isinstance(raw, dict):
            continue

        call_id = str(raw.get("call_id") or raw.get("request_id") or "").strip()
        record_key = call_id or str(call_path.resolve())
        call_records[record_key] = (call_path, raw)

    for call_path, raw in call_records.values():

        prompt_tokens = _coerce_int(raw.get("prompt_tokens"))
        completion_tokens = _coerce_int(raw.get("completion_tokens"))
        call_tokens = _coerce_int(raw.get("total_tokens"))
        if call_tokens <= 0:
            call_tokens = prompt_tokens + completion_tokens
        if call_tokens <= 0 and prompt_tokens <= 0 and completion_tokens <= 0:
            continue

        total_tokens += call_tokens
        total_prompt_tokens += prompt_tokens
        total_completion_tokens += completion_tokens
        total_cost_usd += _coerce_float(raw.get("cost_usd"))
        call_count += 1

        step_name = _fallback_step_name_from_call(raw, call_path)
        step_entry = step_acc[step_name]
        step_entry["step"] = step_name
        step_entry["tokens"] += call_tokens
        step_entry["prompt_tokens"] += prompt_tokens
        step_entry["completion_tokens"] += completion_tokens
        step_entry["cost_usd"] += _coerce_float(raw.get("cost_usd"))
        step_entry["calls"] += 1

        provider = str(raw.get("provider", "") or "")
        model = str(raw.get("model", "") or "")
        key = _model_key(provider, model)
        model_entry = model_acc[key]
        model_entry["key"] = key
        model_entry["label"] = key
        model_entry["provider"] = provider
        model_entry["model"] = model
        model_entry["tokens"] += call_tokens
        model_entry["prompt_tokens"] += prompt_tokens
        model_entry["completion_tokens"] += completion_tokens
        model_entry["cost_usd"] += _coerce_float(raw.get("cost_usd"))
        model_entry["calls"] += 1

    steps = sorted(step_acc.values(), key=lambda item: int(item["tokens"]), reverse=True)
    models = sorted(model_acc.values(), key=lambda item: int(item["tokens"]), reverse=True)

    return {
        "total_tokens": total_tokens,
        "total_prompt_tokens": total_prompt_tokens,
        "total_completion_tokens": total_completion_tokens,
        "total_cost_usd": round(total_cost_usd, 6),
        "step_count": len(steps),
        "steps": steps,
        "models": models,
        "call_count": call_count,
    }


def collect_project_token_analytics(project_dir: Path) -> dict[str, Any]:
    display_name_map = _load_model_display_names(project_dir)
    logs_dir = project_dir / "logs"
    if not logs_dir.is_dir():
        return {
            "project_started_at": "",
            "run_count": 0,
            "step_count": 0,
            "total_tokens": 0,
            "total_prompt_tokens": 0,
            "total_completion_tokens": 0,
            "unattributed_tokens": 0,
            "total_call_count": 0,
            "logged_cost_usd": 0.0,
            "recovered_tokens": 0,
            "recovered_run_count": 0,
            "reconciled_tokens": 0,
            "reconciled_run_count": 0,
            "runs": [],
            "steps": [],
            "models": [],
        }

    run_entries: list[tuple[Path, dict[str, Any]]] = []
    for run_dir in sorted([p for p in logs_dir.iterdir() if p.is_dir()]):
        summary_path = run_dir / "summary.json"
        if not summary_path.exists():
            continue
        try:
            payload = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        if isinstance(payload, dict):
            run_entries.append((run_dir, payload))

    run_entries.sort(key=lambda item: str(item[1].get("started_at", "")))

    total_tokens = 0
    total_prompt_tokens = 0
    total_completion_tokens = 0
    logged_cost_usd = 0.0
    recovered_tokens = 0
    recovered_run_count = 0
    reconciled_tokens = 0
    reconciled_run_count = 0
    total_call_count = 0
    step_count = 0

    runs: list[dict[str, Any]] = []
    step_acc: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "step": "",
            "tokens": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "cost_usd": 0.0,
            "calls": 0,
            "runs": 0,
            "kind_tokens": {"init": 0, "chapter": 0, "repair": 0},
        }
    )
    model_acc: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "key": "",
            "label": "",
            "display_name": "",
            "provider": "",
            "model": "",
            "tokens": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "cost_usd": 0.0,
            "calls": 0,
        }
    )

    for run_dir, summary in run_entries:
        trace = _best_trace_summary(summary)
        trace = trace if isinstance(trace, dict) else {}
        metadata = summary.get("metadata", {})
        metadata = metadata if isinstance(metadata, dict) else {}
        model_fallback = _collect_model_calls_fallback(run_dir)

        steps_raw = trace.get("steps", [])
        steps_raw = steps_raw if isinstance(steps_raw, list) else []
        trace_steps: list[dict[str, Any]] = [
            item
            for item in steps_raw
            if isinstance(item, dict) and str(item.get("name", "")).strip()
        ]
        steps: list[dict[str, Any]] = trace_steps
        completed_steps = trace.get("completed_steps", [])
        completed_steps = completed_steps if isinstance(completed_steps, list) else []
        fallback_step_count = _coerce_int(trace.get("completed_step_count"))
        if fallback_step_count <= 0 and completed_steps:
            fallback_step_count = len(completed_steps)

        run_tokens = _coerce_int(trace.get("total_tokens"))
        run_prompt_tokens = _coerce_int(trace.get("total_prompt_tokens"))
        run_completion_tokens = _coerce_int(trace.get("total_completion_tokens"))
        run_cost_usd = _coerce_float(trace.get("total_cost_usd"))

        trace_tokens = run_tokens
        fallback_tokens = _coerce_int(model_fallback.get("total_tokens"))
        fallback_prompt = _coerce_int(model_fallback.get("total_prompt_tokens"))
        fallback_completion = _coerce_int(model_fallback.get("total_completion_tokens"))
        fallback_cost = _coerce_float(model_fallback.get("total_cost_usd"))
        token_source = "trace_summary"
        if fallback_tokens > 0 and fallback_tokens == trace_tokens:
            if fallback_prompt + fallback_completion > 0:
                run_prompt_tokens = fallback_prompt
                run_completion_tokens = fallback_completion
            if fallback_cost > 0:
                run_cost_usd = fallback_cost
            token_source = "verified"
        if fallback_tokens > 0 and fallback_tokens != trace_tokens:
            run_tokens = fallback_tokens
            if fallback_prompt + fallback_completion > 0:
                run_prompt_tokens = fallback_prompt
                run_completion_tokens = fallback_completion
            if fallback_cost > 0:
                run_cost_usd = fallback_cost
            if trace_tokens <= 0:
                token_source = "model_calls"
                recovered_tokens += run_tokens
                recovered_run_count += 1
            else:
                token_source = "reconciled"
                reconciled_tokens += abs(fallback_tokens - trace_tokens)
                reconciled_run_count += 1

        use_fallback_steps = fallback_tokens > 0 and (
            not trace_steps or fallback_tokens != trace_tokens
        )
        if use_fallback_steps and _coerce_int(model_fallback.get("step_count")) > 0:
            fb_steps = model_fallback.get("steps", [])
            if isinstance(fb_steps, list):
                steps = [item for item in fb_steps if isinstance(item, dict)]

        run_step_count = len(steps) if steps else fallback_step_count
        if run_step_count <= 0 and _coerce_int(model_fallback.get("step_count")) > 0:
            run_step_count = _coerce_int(model_fallback.get("step_count"))

        total_tokens += run_tokens
        total_prompt_tokens += run_prompt_tokens
        total_completion_tokens += run_completion_tokens
        logged_cost_usd += run_cost_usd
        run_call_count = _coerce_int(model_fallback.get("call_count"))
        if run_call_count <= 0:
            run_call_count = sum(
                _coerce_int(item.get("model_call_count"))
                for item in trace_steps
                if isinstance(item, dict)
            )
        total_call_count += run_call_count

        kind = str(metadata.get("kind") or summary.get("command") or "")
        run_group = _step_filter_group_for_kind(kind)
        chapter_number = _coerce_int(metadata.get("chapter_number"))

        runs.append(
            {
                "run_id": str(summary.get("run_id", "") or ""),
                "started_at": str(summary.get("started_at", "") or ""),
                "status": str(summary.get("status", "") or ""),
                "kind": kind,
                "chapter": chapter_number,
                "tokens": run_tokens,
                "prompt_tokens": run_prompt_tokens,
                "completion_tokens": run_completion_tokens,
                "calls": run_call_count,
                "step_count": run_step_count,
                "token_source": token_source,
            }
        )
        step_count += run_step_count

        traced_step_names = {
            str(raw_step.get("name", "") or "").strip()
            for raw_step in steps
            if isinstance(raw_step, dict)
        }
        if completed_steps:
            for raw_name in completed_steps:
                step_name = str(raw_name or "").strip()
                if not step_name or step_name in traced_step_names:
                    continue
                entry = step_acc[step_name]
                entry["step"] = step_name
                entry["runs"] += 1

        for raw_step in steps:
            step_name = str(raw_step.get("name", raw_step.get("step", "")) or "").strip()
            if not step_name:
                continue
            prompt_tokens = _coerce_int(raw_step.get("prompt_tokens"))
            completion_tokens = _coerce_int(raw_step.get("completion_tokens"))
            tokens = _coerce_int(raw_step.get("tokens"))
            if tokens <= 0:
                tokens = prompt_tokens + completion_tokens

            entry = step_acc[step_name]
            entry["step"] = step_name
            entry["tokens"] += tokens
            entry["prompt_tokens"] += prompt_tokens
            entry["completion_tokens"] += completion_tokens
            entry["cost_usd"] += _coerce_float(raw_step.get("cost"))
            _call_cnt = _coerce_int(raw_step.get("model_call_count"))
            if _call_cnt <= 0:
                _call_cnt = sum(1 for _c in raw_step.get("model_calls", []) if isinstance(_c, dict))
            if _call_cnt <= 0:
                _call_cnt = _coerce_int(raw_step.get("calls"))
            entry["calls"] += _call_cnt
            entry["runs"] += 1
            if run_group is not None:
                entry.setdefault("kind_tokens", {"init": 0, "chapter": 0, "repair": 0})
                kind_map = entry.get("kind_tokens")
                if isinstance(kind_map, dict):
                    kind_map[run_group] = _coerce_int(kind_map.get(run_group)) + tokens

            if fallback_tokens > 0:
                continue
            calls = raw_step.get("model_calls", [])
            if not isinstance(calls, list):
                continue
            for call in calls:
                if not isinstance(call, dict):
                    continue
                provider = str(call.get("provider", "") or "")
                model = str(call.get("model", "") or "")
                key = _model_key(provider, model)
                model_entry = model_acc[key]
                model_entry["key"] = key
                model_entry["label"] = key
                model_entry["display_name"] = display_name_map.get(key, key)
                model_entry["provider"] = provider
                model_entry["model"] = model
                model_entry["prompt_tokens"] += _coerce_int(call.get("prompt_tokens"))
                model_entry["completion_tokens"] += _coerce_int(call.get("completion_tokens"))
                call_tokens = _coerce_int(call.get("total_tokens"))
                if call_tokens <= 0:
                    call_tokens = _coerce_int(call.get("prompt_tokens")) + _coerce_int(
                        call.get("completion_tokens")
                    )
                model_entry["tokens"] += call_tokens
                model_entry["cost_usd"] += _coerce_float(call.get("cost_usd"))
                model_entry["calls"] += 1

        if fallback_tokens > 0:
            fallback_models = model_fallback.get("models", [])
            if isinstance(fallback_models, list):
                for model_item in fallback_models:
                    if not isinstance(model_item, dict):
                        continue
                    key = str(
                        model_item.get("key", "")
                        or model_item.get("label", "")
                        or "unknown/unknown"
                    )
                    model_entry = model_acc[key]
                    model_entry["key"] = key
                    model_entry["label"] = key
                    model_entry["display_name"] = display_name_map.get(key, key)
                    model_entry["provider"] = str(model_item.get("provider", "") or "")
                    model_entry["model"] = str(model_item.get("model", "") or "")
                    model_entry["tokens"] += _coerce_int(model_item.get("tokens"))
                    model_entry["prompt_tokens"] += _coerce_int(model_item.get("prompt_tokens"))
                    model_entry["completion_tokens"] += _coerce_int(
                        model_item.get("completion_tokens")
                    )
                    model_entry["cost_usd"] += _coerce_float(model_item.get("cost_usd"))
                    model_entry["calls"] += _coerce_int(model_item.get("calls"))

    steps = sorted(step_acc.values(), key=lambda item: int(item["tokens"]), reverse=True)
    models = sorted(model_acc.values(), key=lambda item: int(item["tokens"]), reverse=True)
    for model_item in models:
        key = str(model_item.get("key") or model_item.get("label") or "unknown/unknown")
        model_item["key"] = key
        model_item["label"] = key
        model_item["display_name"] = display_name_map.get(key, key)

    return {
        "project_started_at": runs[0]["started_at"] if runs else "",
        "run_count": len(runs),
        "step_count": step_count,
        "total_tokens": total_tokens,
        "total_prompt_tokens": total_prompt_tokens,
        "total_completion_tokens": total_completion_tokens,
        "unattributed_tokens": max(
            0,
            total_tokens - total_prompt_tokens - total_completion_tokens,
        ),
        "total_call_count": total_call_count,
        "logged_cost_usd": round(logged_cost_usd, 6),
        "recovered_tokens": recovered_tokens,
        "recovered_run_count": recovered_run_count,
        "reconciled_tokens": reconciled_tokens,
        "reconciled_run_count": reconciled_run_count,
        "runs": runs,
        "steps": steps,
        "models": models,
    }


def estimate_token_cost_cny(tokens: int, *, price_per_million: float) -> float:
    if tokens <= 0 or price_per_million <= 0:
        return 0.0
    return (tokens / 1_000_000.0) * price_per_million
