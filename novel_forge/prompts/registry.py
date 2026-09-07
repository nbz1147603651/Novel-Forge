"""PromptRegistry — loads and caches Jinja2 templates by TaskType."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, Template, Undefined

from novel_forge.common.constants import TaskType
from novel_forge.prompts.packs import (
    DEFAULT_PROMPT_LOCALE,
    get_prompt_pack,
    normalize_prompt_locale,
)

_LEGACY_PROMPTS_DIR = Path(__file__).parent / "prompts"
_PROMPTS_DIR = get_prompt_pack(DEFAULT_PROMPT_LOCALE).templates_dir


class _SafeUndefined(Undefined):
    def get(self, key: Any, default: Any = None) -> Any:
        return default

    def __lt__(self, *args: Any, **kwargs: Any) -> Any:
        return False

    def __le__(self, *args: Any, **kwargs: Any) -> Any:
        return False

    def __gt__(self, *args: Any, **kwargs: Any) -> Any:
        return False

    def __ge__(self, *args: Any, **kwargs: Any) -> Any:
        return False

    def __eq__(self, other: Any) -> bool:
        return False

    def __ne__(self, other: Any) -> bool:
        return True

    def __bool__(self) -> bool:
        return False


_TASK_TEMPLATE_MAP: dict[TaskType, str] = {
    # Initialization tasks (initialization/)
    TaskType.SPEC_ENRICH: "initialization/enrich_spec.j2",
    # Beats tasks (beats/)
    TaskType.BEATS: "beats/spec_to_beats.j2",
    TaskType.DRAFT: "beats/beats_to_draft.j2",
    TaskType.EDIT: "writing/edit_draft.j2",
    TaskType.EVALUATE: "writing/evaluate_draft.j2",
    # Initialization tasks (initialization/)
    TaskType.INIT_STORY_BIBLE: "initialization/init_story_bible.j2",
    TaskType.INIT_STORY_CORE_PREMISE: "initialization/init_story_core_premise.j2",
    TaskType.INIT_STORY_WORLD_RULES: "initialization/init_story_world_rules.j2",
    TaskType.INIT_STORY_CONTINUITY_RULES: ("initialization/init_story_continuity_rules.j2"),
    TaskType.INIT_STORY_THEMES_AND_SYMBOLS: ("initialization/init_story_themes_and_symbols.j2"),
    TaskType.INIT_CREATIVE_DIRECTION_CANDIDATES: (
        "initialization/init_creative_direction_candidates.j2"
    ),
    TaskType.INIT_CREATIVE_DIRECTION_SELECT: ("initialization/init_creative_direction_select.j2"),
    TaskType.LOCATIONS_FIELD_BACKFILL: "initialization/locations_field_backfill.j2",
    TaskType.INIT_CHARACTER_BIBLE: "initialization/init_character_bible.j2",
    TaskType.INIT_KNOWLEDGE_BOUNDARIES: "initialization/init_knowledge_boundaries.j2",
    TaskType.INIT_CHARACTER_ROSTER: "initialization/init_character_roster.j2",
    TaskType.INIT_CHARACTER_PROFILE_BATCH: "initialization/init_character_profile_batch.j2",
    TaskType.INIT_CHARACTER_RELATIONSHIP_MATRIX: (
        "initialization/init_character_relationship_matrix.j2"
    ),
    TaskType.INIT_CHARACTER_ARC_PLAN: "initialization/init_character_arc_plan.j2",
    TaskType.ENRICH_CHARACTER: "initialization/enrich_character.j2",
    TaskType.ADJUDICATE_CHARACTER_INTRODUCTION: (
        "initialization/adjudicate_character_introduction.j2"
    ),
    TaskType.INTRODUCE_CHARACTER: "initialization/introduce_character.j2",
    TaskType.GENERATE_CONFIG: "initialization/generate_config.j2",
    TaskType.POLISH_CONFIG: "initialization/generate_config.j2",
    TaskType.AUTHORING_CHAT: "planning/authoring_chat.j2",
    # Planning tasks (planning/)
    TaskType.BLUEPRINT_ELEMENT_SELECT: "planning/blueprint_element_select.j2",
    TaskType.PLAN_OUTLINE: "planning/plan_outline.j2",
    TaskType.PLAN_OUTLINE_BATCH: "planning/plan_outline_batch.j2",
    TaskType.PLAN_OUTLINE_CONTINUE: "planning/plan_outline_continue.j2",
    TaskType.DERIVE_INIT_COHERENCE_PROFILE: "planning/derive_init_coherence_profile.j2",
    TaskType.REFINE_INIT_COHERENCE_PROFILE: "planning/refine_init_coherence_profile.j2",
    TaskType.INIT_COHERENCE_ONTOLOGY: "planning/init_coherence_ontology.j2",
    TaskType.INIT_COHERENCE_EXTRACTION_GUIDE: ("planning/init_coherence_extraction_guide.j2"),
    TaskType.INIT_COHERENCE_CONFLICT_RULES: "planning/init_coherence_conflict_rules.j2",
    TaskType.INIT_COHERENCE_PAYOFF_RULES: "planning/init_coherence_payoff_rules.j2",
    TaskType.EXTRACT_INIT_COHERENCE_CLAIMS: "checking/extract_init_coherence_claims.j2",
    TaskType.EXTRACT_BLUEPRINT_HOLISTIC_CLAIMS: ("checking/extract_blueprint_holistic_claims.j2"),
    TaskType.ADJUDICATE_INIT_CONFLICT_CANDIDATES: (
        "checking/adjudicate_init_conflict_candidates.j2"
    ),
    TaskType.ADJUDICATE_BLUEPRINT_COHERENCE: "checking/adjudicate_blueprint_coherence.j2",
    TaskType.ADJUDICATE_OUTLINE_INHERITANCE: "checking/adjudicate_outline_inheritance.j2",
    TaskType.INIT_ENTITY_REGISTRY: "initialization/init_entity_registry.j2",
    TaskType.INIT_NARRATIVE_CONTRACT: "initialization/init_narrative_contract.j2",
    TaskType.PLAN_CHAPTER_CONTRACTS: "planning/plan_chapter_contracts.j2",
    TaskType.SYNTHESIZE_INIT_RESEARCH_DOSSIER: (
        "initialization/synthesize_init_research_dossier.j2"
    ),
    TaskType.GROUND_OUTLINE_RESEARCH: "planning/ground_outline_research.j2",
    TaskType.PLAN_INIT_RESEARCH_QUERIES: "planning/plan_init_research_queries.j2",
    TaskType.SYNTHESIZE_MODEL_PRIOR_RESEARCH: ("initialization/synthesize_model_prior_research.j2"),
    TaskType.ADJUDICATE_CONTRACT_COHERENCE: "checking/adjudicate_contract_coherence.j2",
    TaskType.REPAIR_INIT_ARTIFACT_PATCH: "planning/repair_init_artifact_patch.j2",
    TaskType.REFINE_INIT_ARTIFACTS_FROM_SYNOPSIS: (
        "planning/refine_init_artifacts_from_synopsis.j2"
    ),
    TaskType.PLAN_CHAPTER: "planning/plan_chapter.j2",
    TaskType.PLAN_CHAPTER_SCENES: "planning/plan_chapter_scenes.j2",
    TaskType.VALIDATE_SCENE_PLAN: "planning/validate_scene_plan.j2",
    TaskType.SHORT_BLUEPRINT: "planning/short_blueprint.j2",
    TaskType.SHORT_CREATIVE_SUMMARY: "planning/short_creative_summary.j2",
    # Writing tasks (writing/)
    TaskType.DRAFT_CHAPTER: "writing/draft_chapter.j2",
    TaskType.DRAFT_SCENE: "writing/draft_scene.j2",
    TaskType.EDIT_CHAPTER: "writing/edit_chapter.j2",
    TaskType.WAVE_CHAPTER: "writing/wave_chapter.j2",
    TaskType.POLISH_CHAPTER: "writing/polish_chapter.j2",
    TaskType.POLISH_SUBPLOT: "planning/subplot_polish.j2",
    TaskType.POLISH_OUTLINE: "planning/polish_outline.j2",
    TaskType.REVIEW_FUTURE_OUTLINE: "planning/review_future_outline.j2",
    TaskType.PATCH_CHAPTER: "writing/patch_chapter.j2",
    TaskType.BRIDGE_CHAPTER: "writing/bridge_chapter.j2",
    # Canon tasks (kernel/)
    TaskType.EXTRACT_CANON: "kernel/extract_canon_delta.j2",
    TaskType.EXTRACT_CHAPTER_SUMMARY_EXIT: "kernel/extract_chapter_summary_exit.j2",
    TaskType.EXTRACT_CANON_DELTA: "kernel/extract_canon_delta_fragment.j2",
    TaskType.EXTRACT_CREATIVE_REPORT: "kernel/extract_creative_report.j2",
    TaskType.EXTRACT_CHARACTER_STATE_DELTAS: "kernel/extract_character_state_deltas.j2",
    TaskType.EXTRACT_RELATIONSHIP_DELTAS: "kernel/extract_relationship_deltas.j2",
    TaskType.EXTRACT_PLOT_THREAD_DELTAS: "kernel/extract_plot_thread_deltas.j2",
    TaskType.EXTRACT_CANDIDATE_STATE_DELTAS: "kernel/extract_candidate_state_deltas.j2",
    TaskType.ADJUDICATE_ENTITY_REFERENCES: "checking/adjudicate_entity_references.j2",
    TaskType.ADJUDICATE_FINAL_STATE: "kernel/adjudicate_final_state.j2",
    TaskType.EXTRACT_MOTIFS: "kernel/extract_motifs.j2",
    TaskType.ADJUST_OUTLINE: "kernel/adjust_outline.j2",
    # Checking tasks (checking/)
    TaskType.CHECK_ALIGNMENT: "checking/check_alignment.j2",
    TaskType.ELEMENT_PROGRESS_ARBITER: "checking/element_progress_arbiter.j2",
    TaskType.CHECK_CHAPTER: "checking/check_chapter.j2",
    TaskType.CHECK_CONTINUITY: "checking/continuity_eval.j2",
    TaskType.VALIDATE_CAUSAL: "checking/causal_validate.j2",
    TaskType.REPAIR_CONTINUITY: "checking/continuity_repair.j2",
    TaskType.REPAIR_CAUSAL: "checking/causal_repair_typed.j2",
    TaskType.REPAIR_STRATEGY_DIAGNOSE: "checking/repair_strategy_diagnose.j2",
    TaskType.REPAIR_READING_POWER: "checking/repair_reading_power.j2",
    TaskType.REPAIR_SEMANTIC_VERIFY: "checking/repair_semantic_verify.j2",
    TaskType.ADJUDICATE_STATE_DELTA: "checking/adjudicate_state_delta.j2",
    TaskType.ADJUDICATE_CONTRACT_COMPLETION: "checking/adjudicate_contract_completion.j2",
    TaskType.ADJUDICATE_FACT_CONFLICT: "checking/adjudicate_fact_conflict.j2",
    TaskType.REPAIR_ADJUDICATED_ISSUE: "checking/repair_adjudicated_issue.j2",
    TaskType.RECONCILE_ENTITIES: "initialization/reconcile_entities.j2",
    TaskType.VOLUME_AUDIT: "summary/volume_audit.j2",
    TaskType.BOOK_CONSISTENCY: "checking/book_consistency.j2",
    TaskType.BOOK_CONSISTENCY_NAMING: "checking/book_consistency.j2",
    TaskType.BOOK_CONSISTENCY_TIMELINE: "checking/book_consistency.j2",
    TaskType.BOOK_CONSISTENCY_WORLD_RULE: "checking/book_consistency.j2",
    TaskType.BOOK_CONSISTENCY_CHARACTER_STATE: "checking/book_consistency.j2",
    TaskType.BOOK_CONSISTENCY_PLOT_THREAD: "checking/book_consistency.j2",
    TaskType.BOOK_CONSISTENCY_NARRATIVE_DRIFT: "checking/book_consistency.j2",
    TaskType.BOOK_CONSISTENCY_VERIFY: "checking/book_consistency_verify.j2",
    # Critic tasks (checking/)
    TaskType.CRITIC_CONTINUITY: "checking/critic_continuity.j2",
    TaskType.CRITIC_CHARACTER: "checking/critic_character.j2",
    TaskType.CRITIC_CAUSAL: "checking/critic_causal.j2",
    TaskType.CRITIC_STRENGTHS: "checking/critic_strengths.j2",
    # Compression tasks (compression/)
    TaskType.CONTEXT_COMPRESS: "compression/context_compress.j2",
    TaskType.ADAPTIVE_COMPRESS: "compression/adaptive_compress.j2",
    TaskType.VERIFY_COMPRESSION: "compression/verify_compression.j2",
    TaskType.PLOT_GUARD_JUDGE: "compression/plot_guard_judge.j2",
    TaskType.GUARD_CONSTRAINT_CHECK: "compression/guard_constraint_check.j2",
    TaskType.MACRO_GUARD_AUDIT: "checking/macro_guard_audit.j2",
    TaskType.SUMMARY_DRIFT_CHECK: "checking/summary_drift_check.j2",
    TaskType.REPAIR_GUARDRAIL: "checking/guardrail_repair.j2",
    TaskType.KNOWLEDGE_BOUNDARY_AUDIT: "checking/knowledge_boundary_audit.j2",
    TaskType.AUDIT_POV_DRIFT: "checking/knowledge_boundary_audit.j2",  # reuses KB template shape
    TaskType.EXTRACT_KNOWLEDGE_DELTAS: "checking/extract_knowledge_deltas.j2",
    TaskType.REPAIR_KNOWLEDGE_BOUNDARY: "checking/repair_knowledge_boundary.j2",
    # Summary tasks (summary/)
    TaskType.SUMMARIZE_CHAPTER: "summary/summarize_chapter.j2",
    TaskType.SUMMARIZE_VOLUME: "summary/summarize_volume.j2",
    TaskType.SUMMARIZE_ARC: "summary/summarize_arc.j2",
    TaskType.SUMMARIZE_SCENE: "summary/summarize_scene.j2",
    # Style profiling
    TaskType.PROFILE_STYLE: "checking/style_profile_derive.j2",
    TaskType.PROFILE_STRUCTURE: "checking/structure_profile_derive.j2",
    TaskType.DERIVE_EDITORIAL_CONTRACT: "checking/derive_editorial_contract.j2",
    TaskType.DERIVE_EDITORIAL_CHARACTER_VOICES: ("checking/derive_editorial_character_voices.j2"),
    TaskType.DERIVE_EDITORIAL_STRUCTURE: "checking/derive_editorial_structure.j2",
    TaskType.DERIVE_EDITORIAL_STYLE_CONSTRAINTS: ("checking/derive_editorial_style_constraints.j2"),
    TaskType.DERIVE_EDITORIAL_ELEMENT_DIRECTIVES: (
        "checking/derive_editorial_element_directives.j2"
    ),
    TaskType.EXTRACT_EXPRESSION_OBSERVATIONS: ("checking/extract_expression_observations.j2"),
    TaskType.CHECK_EDITORIAL: "checking/check_editorial.j2",
    TaskType.BOOK_EDITORIAL_AUDIT: "checking/book_editorial_audit.j2",
    TaskType.BOOK_EDITORIAL_STRUCTURE_AUDIT: "checking/book_editorial_audit.j2",
    TaskType.BOOK_EDITORIAL_VOICE_AUDIT: "checking/book_editorial_audit.j2",
    TaskType.BOOK_EDITORIAL_LANGUAGE_AUDIT: "checking/book_editorial_audit.j2",
    TaskType.BOOK_EDITORIAL_THEME_SYMBOL_AUDIT: "checking/book_editorial_audit.j2",
    TaskType.BOOK_EDITORIAL_ELEMENT_AUDIT: "checking/book_editorial_audit.j2",
    # Reading power evaluation
    TaskType.EVALUATE_READING_POWER: "checking/evaluate_reading_power.j2",
    # Humanize scan
    TaskType.HUMANIZE_SCAN: "checking/humanize_scan.j2",
    TaskType.HUMANIZE_PARAGRAPH_REWRITE: "checking/humanize_paragraph_rewrite.j2",
    # TTS tasks (tts/)
    TaskType.TTS_BUILD_NARRATOR_PROFILE: "tts/build_narrator_profile.j2",
    TaskType.TTS_GENERATE_DUBBING_SCRIPT: "tts/generate_dubbing_script.j2",
    TaskType.TTS_ADJUDICATE_SCRIPT_SEGMENTS: "tts/adjudicate_script_segments.j2",
    TaskType.TTS_REVIEW_DUBBING_SCRIPT: "tts/review_dubbing_script.j2",
    TaskType.TTS_ANALYZE_DUBBING_STYLE: "tts/analyze_dubbing_style.j2",
    TaskType.TTS_REWRITE_SPOKEN_TEXT: "tts/rewrite_spoken_text.j2",
    TaskType.TTS_EMOTION_LABEL: "tts/emotion_label.j2",
    TaskType.TTS_ADJUDICATE_VOICE_MATCH: "tts/adjudicate_voice_match.j2",
    TaskType.TTS_SOUND_DESIGN: "tts/sound_design_extraction.j2",
    # Film adaptation tasks (film/)
    TaskType.ADAPT_SCREENPLAY: "film/adapt_screenplay.j2",
    TaskType.COMPLIANCE_CHECK: "film/compliance_check.j2",
    TaskType.VISION_QC_SCORE: "film/vision_qc_score.j2",
    TaskType.DRAMA_SERIES_PLAN: "film/drama_series_plan.j2",
    TaskType.EPISODE_OUTLINE: "film/episode_outline.j2",
    TaskType.EPISODE_SCREENPLAY: "film/episode_screenplay.j2",
    TaskType.FILM_SHOT_LAYOUT: "film/film_shot_layout.j2",
    # Comic adaptation tasks (comic/)
    TaskType.COMIC_PANEL_LAYOUT: "comic/comic_panel_layout.j2",
}


class PromptRegistry:
    """Loads Jinja2 prompt files from prompt packs."""

    def __init__(
        self,
        prompts_dir: Path | None = None,
        *,
        locale: str = DEFAULT_PROMPT_LOCALE,
        allow_pack_fallback: bool = False,
    ) -> None:
        self._custom_dir = prompts_dir
        self._default_locale = normalize_prompt_locale(locale)
        self._allow_pack_fallback = allow_pack_fallback
        # Register context helpers as Jinja2 globals for templates
        from novel_forge.core.domain.language import describe_language
        from novel_forge.pipeline.long.services.blueprint.outline_helpers import (
            extract_scoped_weave_links,
        )
        from novel_forge.prompts.context_helpers import (
            format_alignment_report,
            format_canon_characters,
            format_events,
            format_exit_state,
            format_foreshadowing,
            format_list_or_default,
            format_plot_threads,
            format_relationships,
            format_repair_report,
            recommend_character_count,
        )
        from novel_forge.prompts.style_golden_helpers import (
            render_style_golden_examples,
        )

        self._globals = {
            "fmt_list": format_list_or_default,
            "fmt_characters": format_canon_characters,
            "fmt_events": format_events,
            "fmt_foreshadowing": format_foreshadowing,
            "fmt_relationships": format_relationships,
            "fmt_plot_threads": format_plot_threads,
            "fmt_alignment": format_alignment_report,
            "fmt_repair": format_repair_report,
            "fmt_exit_state": format_exit_state,
            "describe_language": describe_language,
            # M3.5 — see docs/ai_flavor_quality.md
            "render_style_golden_examples": render_style_golden_examples,
            "recommend_character_count": recommend_character_count,
            "extract_scoped_weave_links": extract_scoped_weave_links,
        }
        self._envs: dict[str, Environment] = {}
        self._dirs: dict[str, Path] = {}
        self._cache: dict[tuple[str, str], Template] = {}

    def _resolve_dir(self, prompt_locale: str) -> tuple[str, Path]:
        if self._custom_dir is not None:
            return "__custom__", self._custom_dir

        locale = normalize_prompt_locale(prompt_locale)
        try:
            pack = get_prompt_pack(locale)
        except KeyError:
            if not self._allow_pack_fallback:
                raise
            pack = get_prompt_pack(DEFAULT_PROMPT_LOCALE)
            locale = pack.locale
        if not pack.templates_dir.exists():
            if not self._allow_pack_fallback:
                raise FileNotFoundError(
                    f"Prompt pack '{pack.locale}' has no templates directory: {pack.templates_dir}"
                )
            pack = get_prompt_pack(DEFAULT_PROMPT_LOCALE)
            locale = pack.locale
        return locale, pack.templates_dir

    def _environment(self, prompt_locale: str | None = None) -> tuple[str, Environment]:
        locale, directory = self._resolve_dir(prompt_locale or self._default_locale)
        env = self._envs.get(locale)
        if env is None or self._dirs.get(locale) != directory:
            env = Environment(
                loader=FileSystemLoader(str(directory)),
                autoescape=False,
                keep_trailing_newline=True,
                extensions=["jinja2.ext.do"],
                undefined=StrictUndefined,
            )
            json_kwargs = dict(env.policies.get("json.dumps_kwargs", {}))
            json_kwargs["ensure_ascii"] = False
            env.policies["json.dumps_kwargs"] = json_kwargs

            def _tojson_filter(
                value: object,
                indent: int | None = None,
                ensure_ascii: bool | None = None,
            ) -> str:
                """tojson accepting an explicit ensure_ascii override.

                Templates embedding structured canon data call
                ``tojson(ensure_ascii=False)``; the stock Jinja2 filter rejects
                that keyword, so we honour the env policy and the override.
                """
                kwargs = dict(env.policies.get("json.dumps_kwargs", {}))
                if indent is not None:
                    kwargs["indent"] = indent
                if ensure_ascii is not None:
                    kwargs["ensure_ascii"] = ensure_ascii
                return json.dumps(value, **kwargs)

            env.filters["tojson"] = _tojson_filter
            env.globals.update(self._globals)
            self._envs[locale] = env
            self._dirs[locale] = directory
        return locale, env

    def get_template(self, task_type: TaskType, *, prompt_locale: str | None = None) -> Template:
        """Return the Jinja2 template for a given task type."""
        filename = _TASK_TEMPLATE_MAP.get(task_type)
        if filename is None:
            raise KeyError(f"No template registered for task type {task_type}")
        locale, env = self._environment(prompt_locale)
        cache_key = (locale, filename)
        if cache_key not in self._cache:
            self._cache[cache_key] = env.get_template(filename)
        return self._cache[cache_key]

    def render(
        self,
        task_type: TaskType,
        *,
        prompt_locale: str | None = None,
        **context: object,
    ) -> str:
        """Render a template with the given context variables.

        Args:
            task_type: The type of task to render.
            **context: Additional context variables for template rendering.

        Returns:
            The rendered template string.
        """
        resolved_locale = normalize_prompt_locale(
            prompt_locale or context.get("prompt_locale") or self._default_locale
        )
        context.setdefault("prompt_locale", resolved_locale)
        template = self.get_template(task_type, prompt_locale=resolved_locale)
        return template.render(**context)
