# Constrained Decoding PoC

> Status: PoC only. Do not route production Novel Forge gateway traffic through this path until a dedicated adapter interface exists.

## Scope

The first production hardening phase keeps provider-native JSON Schema / JSON mode / prompt-only fallback as the supported gateway path. Constrained decoding remains an offline benchmark because it adds backend coupling, latency tradeoffs, and Chinese-token behavior that must be measured before rollout.

The PoC uses Novel Forge contracts as the only schema source. Export cases with:

```bash
.venv/bin/python scripts/export_constrained_decoding_poc_cases.py \
  --out /tmp/novel_forge_constrained_decoding_cases.json
```

The exported cases cover:

| Case | Task | Why it exists |
|------|------|---------------|
| `plan_outline_character_arcs_fragment` | `PLAN_OUTLINE` | Regression for the original `character_arcs` / `emotional_arcs` fragment failure. |
| `plan_outline_batch` | `PLAN_OUTLINE_BATCH` | Deep nested chapter outline arrays. |
| `plan_chapter_contracts` | `PLAN_CHAPTER_CONTRACTS` | Large executable contract items with nested operations. |
| `extract_init_coherence_claims` | `EXTRACT_INIT_COHERENCE_CLAIMS` | Extraction schema with enums, optional fields, and claim arrays. |
| `book_consistency` | `BOOK_CONSISTENCY` | Audit output with issue arrays and repair plans. |

## Candidate Backends

Evaluate these backends outside the main gateway:

| Backend | Notes |
|---------|-------|
| vLLM structured outputs | OpenAI-compatible server path; current docs describe JSON Schema / Pydantic schema inputs with xgrammar or guidance backends. |
| XGrammar | Grammar-guided constrained generation; evaluate nested JSON Schema behavior and Chinese text throughput. |
| Outlines | Mature constrained-generation API; useful for comparing JSON Schema ergonomics. |
| LM Format Enforcer | Token-filtering approach with JSON Schema / regex support and vLLM integration. |

Reference docs checked on 2026-07-05:

- [vLLM structured outputs](https://docs.vllm.ai/en/latest/features/structured_outputs/)
- [XGrammar documentation](https://xgrammar.mlc.ai/docs/)
- [LM Format Enforcer](https://github.com/noamgat/lm-format-enforcer)

## Metrics

Record at least these fields per backend/model/case:

| Metric | Requirement |
|--------|-------------|
| `json_parse_success` | Output parses as one JSON object with no trailing data. |
| `schema_validation_success` | Output validates against the exported Novel Forge JSON Schema. |
| `required_key_coverage` | All contract required top-level keys are present. |
| `chinese_content_quality` | Manual or evaluator score for useful Chinese narrative content, not just empty schema filler. |
| `latency_ms` | End-to-end generation latency. |
| `tokens_per_second` | Decode throughput. |
| `failure_mode` | Timeout, unsupported schema keyword, repetitive filler, invalid enum, empty array, etc. |

## Promotion Criteria

Production integration requires all of the following:

1. A new constrained-decoding adapter interface, separate from `openai_compat`.
2. Stable schema support for the five PoC cases without ad hoc schema rewrites.
3. No regression in Chinese content usefulness compared with provider-native JSON Schema mode.
4. Structured observability matching existing `structured_output_mode`, downgrade, and repair metrics.
5. A feature flag that can fall back to the existing policy chain without changing task contracts.
