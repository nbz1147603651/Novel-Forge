"""ABTestRunner — runs the same input through two model routes and compares."""

from __future__ import annotations

from dataclasses import dataclass

from novel_forge.core.schemas.eval_schema import EvalReport
from novel_forge.eval.evaluator import DraftEvaluator
from novel_forge.gateway.router import ModelRouter
from novel_forge.gateway.types import ModelRequest, ModelResponse


@dataclass
class ABTestResult:
    """Comparison result of two model runs."""

    response_a: ModelResponse
    response_b: ModelResponse
    eval_a: EvalReport
    eval_b: EvalReport
    winner: str  # "A", "B", or "tie"
    score_delta: float


class ABTestRunner:
    """Runs the same request through two configurations and compares quality."""

    def __init__(
        self,
        router: ModelRouter,
        evaluator: DraftEvaluator,
    ) -> None:
        self._router = router
        self._evaluator = evaluator

    async def run(
        self,
        request: ModelRequest,
        *,
        model_a: str,
        model_b: str,
    ) -> ABTestResult:
        """Execute A/B test with two different model IDs."""
        req_a = request.model_copy(update={"model_id": model_a})
        req_b = request.model_copy(update={"model_id": model_b})

        resp_a = await self._router.route(req_a)
        resp_b = await self._router.route(req_b)

        eval_a = await self._evaluator.evaluate(resp_a.content)
        eval_b = await self._evaluator.evaluate(resp_b.content)

        delta = eval_a.overall_score - eval_b.overall_score
        if abs(delta) < 0.5:
            winner = "tie"
        elif delta > 0:
            winner = "A"
        else:
            winner = "B"

        return ABTestResult(
            response_a=resp_a,
            response_b=resp_b,
            eval_a=eval_a,
            eval_b=eval_b,
            winner=winner,
            score_delta=round(delta, 2),
        )
