"""Stable migration entrypoints for initialization coherence flows.

The active gate is v2.  The v1 module still owns artifact repair/readiness
helpers, so this facade makes that split explicit while larger modules are
decomposed.
"""

from __future__ import annotations

from novel_forge.pipeline.long.services.init.init_coherence import (
    build_init_readiness_report,
    init_readiness_path,
    normalize_artifact_key,
    readiness_payload_allows,
)
from novel_forge.pipeline.long.services.init.init_coherence_v2 import (
    CANDIDATES_REPORT,
    CLAIM_BATCH_CACHE_DIR,
    CLAIM_LEDGER_JSON,
    PROFILE_REPORT,
    claim_cache_stats,
    load_reusable_init_coherence_profile,
    prefetch_init_coherence_claim_chunks,
    refine_init_coherence_profile,
    run_init_coherence_v2_gate,
    set_init_profile_mode_metric,
)

__all__ = (
    "CLAIM_BATCH_CACHE_DIR",
    "CLAIM_LEDGER_JSON",
    "CANDIDATES_REPORT",
    "PROFILE_REPORT",
    "build_init_readiness_report",
    "claim_cache_stats",
    "init_readiness_path",
    "load_reusable_init_coherence_profile",
    "normalize_artifact_key",
    "prefetch_init_coherence_claim_chunks",
    "readiness_payload_allows",
    "refine_init_coherence_profile",
    "run_init_coherence_v2_gate",
    "set_init_profile_mode_metric",
)
