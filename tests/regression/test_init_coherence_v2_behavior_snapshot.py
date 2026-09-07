# behavior-snapshot — init_coherence_v2 behavior snapshot tests.
#
# These tests freeze the observable structure of the init coherence v2 module
# so that the upcoming init_coherence_v2.py refactor (Sprint 4) can be
# verified to preserve behavior.  The tests cover:
#   1. Module export surface (entry points are stable)
#   2. Sibling module separation (5 sibling files are importable & distinct)
#   3. Dedup audit (which functions live in v2 vs siblings vs old module)
#   4. Function signature stability (parameter names + counts)
#   5. Report schema keys (structural properties of report payloads)
#
# CRITICAL: init_coherence_v2.py is the most dangerous module to refactor.
# The old init_coherence.py is NOT a rollback anchor (different API surface).

from __future__ import annotations

import inspect
import json
from typing import Any

import pytest

from novel_forge.pipeline.long.services.init import (
    init_coherence,
    init_coherence_v2,
)


class TestCoherenceModuleExports:
    # behavior-snapshot: module export surface is stable

    def test_v2_exports_run_gate(self) -> None:
        assert callable(init_coherence_v2.run_init_coherence_v2_gate)

    def test_v2_exports_retrieve_candidates(self) -> None:
        assert callable(init_coherence_v2.retrieve_init_conflict_candidates)

    def test_sibling_modules_importable(self) -> None:
        from novel_forge.pipeline.long.services.init import (
            init_coherence_auto_repair,
            init_coherence_claim_cache,
            init_coherence_entrypoints,
            init_coherence_profile,
            init_coherence_report_merge,
        )

        assert init_coherence_auto_repair is not None
        assert init_coherence_claim_cache is not None
        assert init_coherence_entrypoints is not None
        assert init_coherence_profile is not None
        assert init_coherence_report_merge is not None

    def test_old_module_separate_from_v2(self) -> None:
        assert init_coherence is not init_coherence_v2
        assert hasattr(init_coherence, "normalize_coherence_report")
        assert hasattr(init_coherence_v2, "run_init_coherence_v2_gate")
        assert not hasattr(init_coherence, "run_init_coherence_v2_gate")
        assert not hasattr(init_coherence_v2, "normalize_coherence_report")

    def test_application_service_import_does_not_pollute_legacy_module(self) -> None:
        # The new UI imports JobService during API startup. The compatibility
        # facade must not mutate the legacy coherence module as a side effect.
        from novel_forge.app_service import JobService

        assert JobService is not None
        assert not hasattr(init_coherence, "run_init_coherence_v2_gate")


class TestDedupAudit:
    # behavior-snapshot: which functions live in v2 vs siblings vs old module
    # This is the dedup audit — knowing what's where before refactoring.

    def test_v2_public_functions_not_in_old_module(self) -> None:
        v2_funcs = {
            name for name, obj in inspect.getmembers(init_coherence_v2)
            if inspect.isfunction(obj) and not name.startswith("_")
        }
        old_funcs = {
            name for name, obj in inspect.getmembers(init_coherence)
            if inspect.isfunction(obj) and not name.startswith("_")
        }
        v2_only = v2_funcs - old_funcs
        assert "run_init_coherence_v2_gate" in v2_only
        assert "retrieve_init_conflict_candidates" in v2_only

    def test_v2_does_not_reexport_old_module_functions(self) -> None:
        v2_public = {
            name for name, obj in inspect.getmembers(init_coherence_v2)
            if inspect.isfunction(obj) and not name.startswith("_")
        }
        old_exclusive = {
            "normalize_coherence_report",
            "blocking_issues",
            "coherence_blocks",
            "collect_repair_scopes",
            "build_init_readiness_report",
            "apply_targeted_init_patch",
            "apply_scoped_init_patch",
        }
        leaked = v2_public & old_exclusive
        assert not leaked, (
            f"v2 re-exports old module functions (should be in old module only): {leaked}"
        )

    def test_sibling_modules_have_distinct_responsibilities(self) -> None:
        from novel_forge.pipeline.long.services.init import (
            init_coherence_auto_repair,
            init_coherence_claim_cache,
            init_coherence_entrypoints,
            init_coherence_profile,
            init_coherence_report_merge,
        )

        siblings = {
            "auto_repair": init_coherence_auto_repair,
            "claim_cache": init_coherence_claim_cache,
            "entrypoints": init_coherence_entrypoints,
            "profile": init_coherence_profile,
            "report_merge": init_coherence_report_merge,
        }
        for name, mod in siblings.items():
            all_callables = {
                f for f, o in inspect.getmembers(mod)
                if (inspect.isfunction(o) or inspect.isclass(o))
            }
            assert all_callables, f"Sibling '{name}' has no callable members"


class TestFunctionSignatureStability:
    # behavior-snapshot: key function signatures are stable

    def test_run_gate_signature(self) -> None:
        sig = inspect.signature(init_coherence_v2.run_init_coherence_v2_gate)
        params = list(sig.parameters.keys())
        assert params[0] == "ctx", (
            f"run_init_coherence_v2_gate first param should be 'ctx', got: {params[0]}"
        )
        kw_only = {
            p.name for p in sig.parameters.values()
            if p.kind == inspect.Parameter.KEYWORD_ONLY
        }
        expected_kw = {"stage", "repair_artifact", "profile", "artifacts"}
        assert expected_kw.issubset(kw_only), (
            f"Missing keyword-only params: {expected_kw - kw_only}"
        )

    def test_retrieve_candidates_signature(self) -> None:
        sig = inspect.signature(init_coherence_v2.retrieve_init_conflict_candidates)
        params = list(sig.parameters.keys())
        assert "ctx" in params
        assert "claims" in params

    def test_run_gate_is_async(self) -> None:
        assert inspect.iscoroutinefunction(init_coherence_v2.run_init_coherence_v2_gate)

    def test_retrieve_candidates_is_async(self) -> None:
        assert inspect.iscoroutinefunction(
            init_coherence_v2.retrieve_init_conflict_candidates
        )


class TestReportSchemaKeys:
    # behavior-snapshot: report payload keys produced by run_init_coherence_v2_gate
    # These are the keys added to the report_payload in run_init_coherence_v2_gate.

    _EXPECTED_REPORT_KEYS: frozenset[str] = frozenset({
        "extracted_claims_count",
        "active_claims_count",
        "ledger_active_claims_count",
        "retrieval_scope",
        "exact_candidate_count",
        "semantic_candidate_count",
        "post_filter_drop_count",
        "fully_pushed_down_ratio",
        "zvec_backend",
        "semantic_skipped_reason",
        "claim_ledger_path",
        "artifact_hashes",
        "focus_chapters",
    })

    def test_expected_report_keys_are_present_in_source(self) -> None:
        source = inspect.getsource(init_coherence_v2.run_init_coherence_v2_gate)
        for key in self._EXPECTED_REPORT_KEYS:
            assert f'"{key}"' in source or f"'{key}'" in source, (
                f"Report key '{key}' not found in run_init_coherence_v2_gate source"
            )

    def test_candidate_report_keys_present_in_source(self) -> None:
        source = inspect.getsource(init_coherence_v2.run_init_coherence_v2_gate)
        candidate_keys = [
            "candidate_payload",
            "CANDIDATES_REPORT",
            "ADJUDICATION_REPORT",
        ]
        for key in candidate_keys:
            assert key in source, (
                f"Key '{key}' not found in run_init_coherence_v2_gate source"
            )


class TestCoherenceE2E:
    # behavior-snapshot: mock init_long e2e.
    # This guards the artifact-producing path needed before Sprint 4 refactors.

    @pytest.mark.timeout(300)
    async def test_init_long_produces_coherence_report(self, tmp_path: Any) -> None:
        from novel_forge.core.config import Settings
        from novel_forge.gateway.adapters.mock import MockAdapter
        from novel_forge.gateway.router import ModelRouter
        from novel_forge.persistence.filesystem import FileSystemStorage
        from novel_forge.pipeline.chapter_runner import ChapterRunner, ChapterRunnerConfig
        from novel_forge.prompts.builder import PromptBuilder

        runner = ChapterRunner(
            ModelRouter(adapters={"mock": MockAdapter()}, default_provider="mock"),
            PromptBuilder(),
            FileSystemStorage(tmp_path),
            config=ChapterRunnerConfig(),
            settings=Settings(_env_file=None),
        )
        await runner.init_long(
            premise="一个关于时间旅行的故事",
            project_id="coherence_e2e",
            genre="scifi",
            tone="mysterious",
            total_chapters=3,
            words_per_chapter=3000,
        )

        project_dir = tmp_path / "coherence_e2e"
        claim_ledger_path = project_dir / "memory" / "init_coherence_claim_ledger.json"
        assert claim_ledger_path.exists()
        claim_ledger = json.loads(claim_ledger_path.read_text(encoding="utf-8"))
        assert isinstance(claim_ledger.get("active_claim_ids"), list)
        assert isinstance(claim_ledger.get("stages"), dict)

        for report_name in ("blueprint_coherence", "outline_inheritance"):
            report_path = project_dir / "reports" / f"{report_name}.json"
            assert report_path.exists(), f"Missing coherence report: {report_name}"
            report = json.loads(report_path.read_text(encoding="utf-8"))
            expected_keys = {
                "schema_version",
                "stage",
                "artifact",
                "verdict",
                "issues",
                "summary",
                "claim_ledger_path",
                "artifact_hashes",
                "candidate_count",
                "adjudicated_candidate_count",
            }
            assert expected_keys.issubset(set(report.keys())), (
                f"{report_name} missing keys: {expected_keys - set(report.keys())}"
            )
            assert report["schema_version"] == "audit_v2"
            assert report["claim_ledger_path"] == "memory/init_coherence_claim_ledger.json"
            assert isinstance(report["artifact_hashes"], dict)

        for bundle_name in ("init_conflict_candidates", "init_conflict_adjudication"):
            bundle_path = project_dir / "reports" / f"{bundle_name}.json"
            assert bundle_path.exists(), f"Missing stage bundle: {bundle_name}"
            bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
            assert isinstance(bundle.get("stages"), dict)
            assert {"blueprint_coherence", "outline_inheritance"}.issubset(
                set(bundle["stages"])
            )
