"""Performance benchmark for pre_evaluate_repair_alignment() and guard_repair_quality_gate().

Measures the latency of the two guardrail/repair functions:
  1. pre_evaluate_repair_alignment() - pre-evaluates alignment before repair
  2. guard_repair_quality_gate() - quality gate for repair operations

Each function runs N iterations to collect latency statistics.
Assertion: total combined latency < 30s for the benchmark suite.
"""

from __future__ import annotations

import asyncio
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from novel_forge.core.config import Settings
from novel_forge.core.schemas.continuity import ChapterPlan
from novel_forge.core.schemas.outline import ChapterOutline
from novel_forge.gateway.adapters.mock import MockAdapter
from novel_forge.gateway.router import ModelRouter
from novel_forge.obs.tracer import PipelineTrace
from novel_forge.pipeline.long.stages.continuity_repair import (
    guard_repair_quality_gate,
    pre_evaluate_repair_alignment,
)
from novel_forge.pipeline.steps.alignment_step import AlignmentInput
from novel_forge.prompts.builder import PromptBuilder

# ─── Mock Objects ─────────────────────────────────────────────────────────────


@dataclass
class MockRunner:
    """Minimal mock runner providing _router, _builder, _settings."""

    _router: ModelRouter = field(default=None)
    _builder: PromptBuilder = field(default=None)
    _settings: Settings = field(default=None)

    def __post_init__(self) -> None:
        if self._settings is None:
            self._settings = Settings(_env_file=None)
        if self._builder is None:
            self._builder = PromptBuilder()
        if self._router is None:
            adapter = MockAdapter()
            self._router = ModelRouter(adapters={"mock": adapter}, default_provider="mock")


@dataclass
class MockTrace:
    """Minimal trace context for benchmarking."""
    run_id: str = "benchmark_run"
    step_name: str = "benchmark"


# ─── Sample Data ───────────────────────────────────────────────────────────────


SAMPLE_ORIGINAL_TEXT = (
    "夜色沉沉，月光从窗棂漏进来，照在她苍白的脸上。\n\n"
    "她指尖微微颤抖，想起父亲临走前那句嘱咐，心头涌起一阵寒意。\n\n"
    "周明守在耳房，屏息听着外面的风声，脊背发紧。\n\n"
    "'必须镇定。'他对自己说，强自镇定地翻开账簿残页。\n\n"
    "灯光昏黄，空气中弥漫着铁锈味。"
)

SAMPLE_REPAIRED_TEXT = (
    "夜色深沉，月光从窗棂缝隙间洒落，照在她苍白的面容上。\n\n"
    "她指尖微微颤动，想起父亲离别时的叮嘱，心中涌起一阵寒意。\n\n"
    "周明守在耳房，屏息聆听外面的风声，脊背绷紧。\n\n"
    "'必须冷静。'他提醒自己，强作镇定地翻开账簿残页。\n\n"
    "灯火黯淡，空气中弥漫着铁锈气息。"
)

SAMPLE_OUTLINE = ChapterOutline(
    chapter_number=1,
    title="第一章",
    goal="故事开篇，介绍主要人物和场景",
)

SAMPLE_PLAN = ChapterPlan(
    scene_intents=[],
)

SAMPLE_ALIGNMENT_INPUT = AlignmentInput(
    chapter_outline=SAMPLE_OUTLINE,
    chapter_plan=SAMPLE_PLAN,
    chapter_text=SAMPLE_ORIGINAL_TEXT,
    narrative_context=None,
    genre="fantasy",
    pov_hint="第三人称",
)


# ─── Benchmark Configuration ───────────────────────────────────────────────────


ITERATIONS = 10


def _percentile(sorted_values: list[float], p: float) -> float:
    """Calculate percentile of sorted values."""
    if not sorted_values:
        return 0.0
    k = (len(sorted_values) - 1) * p / 100.0
    f = int(k)
    c = f + 1 if f + 1 < len(sorted_values) else f
    return sorted_values[f] + (k - f) * (sorted_values[c] - sorted_values[f])


# ─── Benchmark Functions ──────────────────────────────────────────────────────


async def _bench_pre_evaluate_repair_alignment(
    runner: MockRunner,
    trace: PipelineTrace,
) -> list[float]:
    """Benchmark pre_evaluate_repair_alignment() latency."""
    latencies: list[float] = []

    for _ in range(ITERATIONS):
        t0 = time.perf_counter()
        await pre_evaluate_repair_alignment(
            original_text=SAMPLE_ORIGINAL_TEXT,
            repaired_text=SAMPLE_REPAIRED_TEXT,
            chapter_outline=SAMPLE_OUTLINE,
            chapter_plan=SAMPLE_PLAN,
            alignment_input=SAMPLE_ALIGNMENT_INPUT,
            runner=runner,
            trace=trace,
        )
        t1 = time.perf_counter()
        latencies.append((t1 - t0) * 1000.0)  # Convert to ms

    return latencies


async def _bench_guard_repair_quality_gate(
    runner: MockRunner,
) -> list[float]:
    """Benchmark guard_repair_quality_gate() latency."""

    def on_step(event: str, data: Any) -> None:
        pass

    latencies: list[float] = []

    for _ in range(ITERATIONS):
        t0 = time.perf_counter()
        await guard_repair_quality_gate(
            alignment_score_before=0.85,
            alignment_score_after=0.82,
            original_text=SAMPLE_ORIGINAL_TEXT,
            repaired_text=SAMPLE_REPAIRED_TEXT,
            runner=runner,
            on_step=on_step,
            chapter_number=1,
            max_secondary_attempts=1,
        )
        t1 = time.perf_counter()
        latencies.append((t1 - t0) * 1000.0)  # Convert to ms

    return latencies


async def run_benchmarks() -> dict[str, Any]:
    """Run all benchmarks and return results."""
    runner = MockRunner()
    trace = PipelineTrace()

    print("=" * 60)
    print("Benchmark: guardrail_repair latency")
    print(f"Iterations per function: {ITERATIONS}")
    print("=" * 60)

    # Benchmark pre_evaluate_repair_alignment
    print("\n[1/2] Benchmarking pre_evaluate_repair_alignment()...")
    pre_evaluate_latencies = await _bench_pre_evaluate_repair_alignment(runner, trace)
    pre_evaluate_sorted = sorted(pre_evaluate_latencies)
    pre_evaluate_total = sum(pre_evaluate_latencies)
    print(f"  Median: {statistics.median(pre_evaluate_latencies):.2f} ms")
    print(f"  P95: {_percentile(pre_evaluate_sorted, 95):.2f} ms")
    print(f"  Max: {max(pre_evaluate_latencies):.2f} ms")
    print(f"  Total ({ITERATIONS} runs): {pre_evaluate_total:.2f} ms")

    # Benchmark guard_repair_quality_gate
    print("\n[2/2] Benchmarking guard_repair_quality_gate()...")
    quality_gate_latencies = await _bench_guard_repair_quality_gate(runner)
    quality_gate_sorted = sorted(quality_gate_latencies)
    quality_gate_total = sum(quality_gate_latencies)
    print(f"  Median: {statistics.median(quality_gate_latencies):.2f} ms")
    print(f"  P95: {_percentile(quality_gate_sorted, 95):.2f} ms")
    print(f"  Max: {max(quality_gate_latencies):.2f} ms")
    print(f"  Total ({ITERATIONS} runs): {quality_gate_total:.2f} ms")

    # Combined total
    combined_total_ms = pre_evaluate_total + quality_gate_total
    combined_total_s = combined_total_ms / 1000.0
    print(f"\n{'=' * 60}")
    print(f"Combined Total Latency: {combined_total_ms:.2f} ms ({combined_total_s:.2f} s)")
    print("Threshold: 30.00 s")
    print(f"Status: {'PASS' if combined_total_s < 30.0 else 'FAIL'}")
    print(f"{'=' * 60}")

    return {
        "pre_evaluate": {
            "latencies": pre_evaluate_latencies,
            "median_ms": statistics.median(pre_evaluate_latencies),
            "p95_ms": _percentile(pre_evaluate_sorted, 95),
            "max_ms": max(pre_evaluate_latencies),
            "total_ms": pre_evaluate_total,
        },
        "guard_repair_quality_gate": {
            "latencies": quality_gate_latencies,
            "median_ms": statistics.median(quality_gate_latencies),
            "p95_ms": _percentile(quality_gate_sorted, 95),
            "max_ms": max(quality_gate_latencies),
            "total_ms": quality_gate_total,
        },
        "combined_total_ms": combined_total_ms,
        "combined_total_s": combined_total_s,
        "threshold_s": 30.0,
        "passed": combined_total_s < 30.0,
    }


def format_report(results: dict[str, Any]) -> str:
    """Format benchmark results as a text report."""
    lines = [
        "=" * 60,
        "GUARDRAIL REPAIR LATENCY BENCHMARK REPORT",
        "=" * 60,
        "",
        "Configuration:",
        f"  Iterations per function: {ITERATIONS}",
        f"  Threshold: {results['threshold_s']:.2f} s",
        "",
        "pre_evaluate_repair_alignment():",
        f"  Median: {results['pre_evaluate']['median_ms']:.2f} ms",
        f"  P95: {results['pre_evaluate']['p95_ms']:.2f} ms",
        f"  Max: {results['pre_evaluate']['max_ms']:.2f} ms",
        f"  Total: {results['pre_evaluate']['total_ms']:.2f} ms",
        "",
        "guard_repair_quality_gate():",
        f"  Median: {results['guard_repair_quality_gate']['median_ms']:.2f} ms",
        f"  P95: {results['guard_repair_quality_gate']['p95_ms']:.2f} ms",
        f"  Max: {results['guard_repair_quality_gate']['max_ms']:.2f} ms",
        f"  Total: {results['guard_repair_quality_gate']['total_ms']:.2f} ms",
        "",
        "-" * 60,
        f"Combined Total: {results['combined_total_ms']:.2f} ms ({results['combined_total_s']:.2f} s)",
        f"Threshold: {results['threshold_s']:.2f} s",
        f"Status: {'PASS' if results['passed'] else 'FAIL'}",
        "-" * 60,
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    """Main entry point."""
    results = asyncio.run(run_benchmarks())

    # Write results to evidence file
    evidence_path = Path(__file__).resolve().parent.parent / ".sisyphus" / "evidence" / "task-10-perf-benchmark.txt"
    report = format_report(results)

    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(report, encoding="utf-8")

    print(f"\nResults written to: {evidence_path}")

    # Exit with appropriate code
    sys.exit(0 if results["passed"] else 1)


if __name__ == "__main__":
    main()