# 07 格式契约对照矩阵（142 个 TaskType 契约；动态渲染为 144 个变体）

> 本矩阵核对四层一致性：**模板正文声明 → TaskFormatContract（format_contracts.py）→ 响应 Schema（response_schemas.py）→ 解析器/校验器**。
> **分析基准**：模板正文以运行时目录 `packs/zh/templates/` 为准；旧副本 `prompts/prompts/` 的 53 个分叉文件影响见 G-13，矩阵中「⚠️ 旧版差异」标注受影响行。
> 基线：`audit_prompt_format_layers.py --all` 与 `verify_format_contracts.py` 均 PASS（元数据↔契约层）；本矩阵补充模板正文 ↔ 解析器的字段级核对。
> 结论图例：✅ 三方一致 / ⚠️ 有差异（见附注）/ 🔴 解析风险 / ❌ 不一致

## 1. 写作族（TEXT_ONLY）

| TaskType | 模板 | 契约 | 模板正文输出声明 | 解析器 | 结论 |
|----------|------|------|------------------|--------|------|
| BRIDGE_CHAPTER | writing/bridge_chapter.j2 | JSON FULL 10 键 | 「只输出 10 个 snake_case 顶层字段」（字段名依赖 builder 注入） | `_BridgeChapterResponse` 10 字段 | ✅ 契约=Schema；⚠️ 模板未列字段名（依赖注入），建议显式列出 |
| DRAFT_CHAPTER | writing/draft_chapter.j2 | TEXT | 纯正文 | DraftStep 直接取 text | ✅ |
| DRAFT_SCENE | writing/draft_scene.j2 | TEXT | 纯正文（禁止 scene_id） | 场景级拼接 | ✅ |
| WAVE_CHAPTER | writing/wave_chapter.j2 | TEXT | 纯正文（禁止规划术语） | WaveStep 后置 6 检查（never raise） | ⚠️ 后置条件不硬门（G-08） |
| EDIT_CHAPTER | writing/edit_chapter.j2 | TEXT | 纯正文 | EditStep | ✅ |
| EDIT（短篇） | writing/edit_draft.j2 | TEXT | 纯正文 | EditStep（max_edit_rounds） | ✅ |
| POLISH_CHAPTER | writing/polish_chapter.j2 | TEXT | 纯正文 | PolishStep + re-eval | ✅ |
| PATCH_CHAPTER | writing/patch_chapter.j2 | JSON PATCH_PLAN `patches` | 「original 精确原文子串 + replacement」 | patch_engine 精确匹配 | ✅ 精确匹配（含空白/换行）、原文顺序与不重叠均已声明 |
| EVALUATE | writing/evaluate_draft.j2 | JSON FULL 6 键 | scores 维度枚举 7+1 | `_EvaluateResponse` | ✅ |
| REPAIR_GUARDRAIL 侧 | writing/repair_literary_quality.j2 | TEXT | 纯正文 | 遗留调用 | ⚠️ legacy 双轨（见 01 章） |

## 2. 规划族

| TaskType | 模板 | 契约 required | Schema | 结论 |
|----------|------|---------------|--------|------|
| BLUEPRINT_ELEMENT_SELECT | planning/blueprint_element_select.j2 | FULL（动态 schema） | `_BlueprintElementSelectResponse` | ✅ |
| PLAN_OUTLINE | planning/plan_outline.j2 | FULL 14 键 | `_PlanOutlineResponse` | ✅（编号 4 重复见 G-06） |
| PLAN_OUTLINE_BATCH | planning/plan_outline_batch.j2 | FRAGMENT `chapters` | `_PlanOutlineBatchResponse` | ✅ |
| PLAN_OUTLINE_CONTINUE | planning/plan_outline_continue.j2 | FRAGMENT `chapters` | 同上 | ✅ |
| POLISH_OUTLINE | planning/polish_outline.j2 | PATCH_PLAN 2 键 | `_PolishOutlineResponse` | ✅ |
| ADJUST_OUTLINE | kernel/adjust_outline.j2 | PATCH_PLAN 2 键 | `_AdjustOutlineResponse` | ✅ |
| PLAN_CHAPTER | planning/plan_chapter.j2 | FULL 16 键 | `_PlanChapterResponse` | ✅ 含 cross_scene_intent 协议与 WAVE 对齐 |
| PLAN_CHAPTER_SCENES | planning/plan_chapter_scenes.j2 | FRAGMENT `scene_plan` | `_PlanChapterScenesResponse` | ✅ 同 16 字段协议；scene_plan 内嵌 scene_intents |
| PLAN_CHAPTER_CONTRACTS | planning/plan_chapter_contracts.j2 | FULL `chapter_contracts` | `_ChapterContractsResponse` | ✅ |
| VALIDATE_SCENE_PLAN | planning/validate_scene_plan.j2 | FULL 5 键 | `_ValidateScenePlanResponse` | ✅ |
| SHORT_BLUEPRINT | planning/short_blueprint.j2 | FULL 7 键 | `_ShortBlueprintResponse` | ✅ |
| BEATS | beats/spec_to_beats.j2 | FULL `beats` | `_BeatsResponse` | ✅ |
| SHORT_CREATIVE_SUMMARY | planning/short_creative_summary.j2 | FULL 6 键 | `_ShortCreativeSummaryResponse` | ✅ |
| POLISH_SUBPLOT | planning/subplot_polish.j2 | FRAGMENT `subplots` | `_PolishSubplotResponse` | ✅ 已注册并有对应契约 |
| DERIVE_INIT_COHERENCE_PROFILE / REFINE | planning/derive|refine_init_coherence_profile.j2 | FULL 6 键 | `_InitCoherenceProfileResponse` | ✅ |
| INIT_COHERENCE_ONTOLOGY | planning/init_coherence_ontology.j2 | FRAGMENT 3 键 | `_InitCoherenceOntologyResponse` | ✅ |
| INIT_COHERENCE_EXTRACTION_GUIDE | planning/init_coherence_extraction_guide.j2 | FRAGMENT 1 键 | `_InitCoherenceExtractionGuideResponse` | ✅ |
| INIT_COHERENCE_CONFLICT_RULES | planning/init_coherence_conflict_rules.j2 | FRAGMENT 1 键 | `_InitCoherenceConflictRulesResponse` | ✅ |
| INIT_COHERENCE_PAYOFF_RULES | planning/init_coherence_payoff_rules.j2 | FRAGMENT 2 键 | `_InitCoherencePayoffRulesResponse` | ✅ |
| PLAN_INIT_RESEARCH_QUERIES | planning/plan_init_research_queries.j2 | FULL 2 键 | `_PlanInitResearchQueriesResponse` | ✅ |
| GROUND_OUTLINE_RESEARCH | planning/ground_outline_research.j2 | FULL 7 键 | `_GroundOutlineResearchResponse` | ✅ |
| SYNTHESIZE_INIT_RESEARCH_DOSSIER | initialization/synthesize_init_research_dossier.j2 | FULL 7 键 | `_ResearchDossierSynthesisResponse` | ✅ |
| SYNTHESIZE_MODEL_PRIOR_RESEARCH | initialization/synthesize_model_prior_research.j2 | FULL 3 键 | `_SynthesizeModelPriorResearchResponse` | ✅ |
| REPAIR_INIT_ARTIFACT_PATCH | planning/repair_init_artifact_patch.j2 | PATCH_PLAN 8 键 | `_InitArtifactPatchResponse` | ✅ |
| REFINE_INIT_ARTIFACTS_FROM_SYNOPSIS | planning/refine_init_artifacts_from_synopsis.j2 | PATCH_PLAN 6 键 | `_InitCreativeRefinementResponse` | ✅ |
| ADJUDICATE_BLUEPRINT_COHERENCE | checking/adjudicate_blueprint_coherence.j2 | FULL 12 键 | `_ContractCoherenceResponse` | ✅ |
| ADJUDICATE_OUTLINE_INHERITANCE | checking/adjudicate_outline_inheritance.j2 | FULL 12 键 | 同上 | ✅ |
| ADJUDICATE_INIT_CONFLICT_CANDIDATES | checking/adjudicate_init_conflict_candidates.j2 | FULL 12 键 | 同上 | ✅ |
| ADJUDICATE_CONTRACT_COHERENCE | checking/adjudicate_contract_coherence.j2 | FULL 8 键 | `_ContractCoherenceResponse`（简化） | ✅ |
| EXTRACT_BLUEPRINT_HOLISTIC_CLAIMS | checking/extract_blueprint_holistic_claims.j2 | FRAGMENT `claims` | `_InitCoherenceClaimsResponse` | ✅ |
| EXTRACT_INIT_COHERENCE_CLAIMS | checking/extract_init_coherence_claims.j2 | FRAGMENT `claims` | 同上 | ✅ |

## 3. 检查修复族

| TaskType | 模板 | 契约 required | Schema | 解析器/校验器 | 结论 |
|----------|------|---------------|--------|--------------|------|
| CHECK_CHAPTER | checking/check_chapter.j2 | FULL 8 键 | `_CheckChapterResponse` | 修复 ticket 生成 | ✅ |
| CHECK_ALIGNMENT | checking/check_alignment.j2 | FULL 2 键（alignment_score/summary） | `_CheckAlignmentResponse` | alignment 修复 | ✅ |
| CHECK_EDITORIAL | checking/check_editorial.j2 | FULL 4 键 | `_EditorialAuditResponse` | revision_planner | ✅ |
| CHECK_CONTINUITY | checking/continuity_eval.j2 | FULL 2 键 | `_CheckContinuityResponse` | 复检模式 | ✅ |
| VALIDATE_CAUSAL | checking/causal_validate.j2 | FULL 2 键 | `_ValidateCausalResponse` | 复检模式 | ✅ |
| REPAIR_CONTINUITY | checking/continuity_repair.j2 | TEXT | - | 文本校验 | ✅ |
| REPAIR_CAUSAL | checking/causal_repair_typed.j2 | TEXT | - | 文本校验 | ✅ |
| EVALUATE_READING_POWER | checking/evaluate_reading_power.j2 | FULL 11 required 键 | `_ReadingPowerResponse` | 阅读力修复映射 | ✅ HookType 与修复映射一致 |
| REPAIR_READING_POWER | checking/repair_reading_power.j2 | TEXT | - | 类型化修复 | ✅ |
| PATCH_CHAPTER（对齐修复侧） | writing/patch_chapter.j2 | PATCH_PLAN | - | patch_engine | ✅ |
| ELEMENT_PROGRESS_ARBITER | checking/element_progress_arbiter.j2 | FULL 3 键 | `_ElementProgressArbiterResponse` | 要素进度 | ✅ |
| GUARD_CONSTRAINT_CHECK | compression/guard_constraint_check.j2 | FULL 4 键 | `_GuardConstraintCheckResponse` | 护栏校验 | ✅ |
| PLOT_GUARD_JUDGE | compression/plot_guard_judge.j2 | FULL 8 键 | `_PlotGuardDecisionResponse` | 大纲裁决 | ✅ |
| MACRO_GUARD_AUDIT | checking/macro_guard_audit.j2 | FULL 7 键 | `_MacroGuardAuditResponse` | 宏观审计 | ✅ |
| REPAIR_GUARDRAIL | checking/guardrail_repair.j2 | TEXT | - | 文本校验 | ✅ |
| REPAIR_SEMANTIC_VERIFY | checking/repair_semantic_verify.j2 | FULL 3 键 | `_RepairSemanticVerifyResponse` | 修复后验证 | ✅ |
| REPAIR_STRATEGY_DIAGNOSE | checking/repair_strategy_diagnose.j2 | FULL 6 键 | `_RepairStrategyDiagnoseResponse` | 修复策略路由 | ✅ |
| HUMANIZE_SCAN | checking/humanize_scan.j2 | FULL 5 键 | `_HumanizeScanResponse` | 🔴 require_native（G-04） | ⚠️ native 硬依赖 |
| HUMANIZE_PARAGRAPH_REWRITE | checking/humanize_paragraph_rewrite.j2 | TEXT | - | 段落替换 | ✅ |
| CRITIC_CONTINUITY | checking/critic_continuity.j2 | FULL `issues` | `_CriticContinuityResponse` | CriticAgent | ✅ |
| CRITIC_CHARACTER | checking/critic_character.j2 | FULL `issues` | `_CriticCharacterResponse` | CriticAgent | ✅ |
| CRITIC_CAUSAL | checking/critic_causal.j2 | FULL `causal_breaks` | `_CriticCausalResponse` | CriticAgent | ✅ |
| CRITIC_STRENGTHS | checking/critic_strengths.j2 | FULL `strengths` | `_CriticStrengthsResponse` | CriticAgent | ✅ |
| EXTRACT_EXPRESSION_OBSERVATIONS | checking/extract_expression_observations.j2 | FRAGMENT 3 键 | `_ExpressionObservationExtractionResponse` | 表达通道冷却 | ✅ |
| DERIVE_EDITORIAL_CONTRACT | checking/derive_editorial_contract.j2 | FULL 10 键 | `_EditorialContractResponse` | editorial 系统 | ✅（生成要求连续 1–13） |
| DERIVE_EDITORIAL_CHARACTER_VOICES | checking/derive_editorial_character_voices.j2 | FRAGMENT 1 键 | `_EditorialCharacterVoicesResponse` | 声纹消费 | ✅ |
| DERIVE_EDITORIAL_STRUCTURE | checking/derive_editorial_structure.j2 | FRAGMENT 5 键 | `_EditorialStructureResponse` | ✅ |
| DERIVE_EDITORIAL_STYLE_CONSTRAINTS | checking/derive_editorial_style_constraints.j2 | FRAGMENT 8 键 | `_EditorialStyleConstraintsResponse` | ✅ |
| DERIVE_EDITORIAL_ELEMENT_DIRECTIVES | checking/derive_editorial_element_directives.j2 | FRAGMENT 1 键 | `_EditorialElementDirectivesResponse` | ✅ |
| BOOK_CONSISTENCY（8 任务共用） | checking/book_consistency.j2 | FULL 4 键 | `_BookConsistencyResponse` | 维度注入 | ✅ active_audit_dimension 必注入 |
| BOOK_CONSISTENCY_VERIFY | checking/book_consistency_verify.j2 | FULL 1 键 | `_BookConsistencyVerifyResponse` | 复核 | ✅ |
| BOOK_EDITORIAL_*_AUDIT（6 任务共用） | checking/book_editorial_audit.j2 | FULL 4 键 | `_EditorialAuditResponse` | 维度注入 | ✅ |
| SUMMARY_DRIFT_CHECK | checking/summary_drift_check.j2 | FULL 3 键 | `_SummaryDriftCheckResponse` | ✅ |
| KNOWLEDGE_BOUNDARY_AUDIT | checking/knowledge_boundary_audit.j2 | FULL 2 键 | `_KnowledgeBoundaryAuditResponse` | ✅ |
| REPAIR_KNOWLEDGE_BOUNDARY | checking/repair_knowledge_boundary.j2 | PARTIAL 宽松 | `_RepairKnowledgeBoundaryResponse` | ✅ PARTIAL 解析与修复路径完整 |
| INIT_KNOWLEDGE_BOUNDARIES | initialization/init_knowledge_boundaries.j2 | FULL `characters` | `_InitKnowledgeBoundariesResponse` | ✅ |
| EXTRACT_KNOWLEDGE_DELTAS | checking/extract_knowledge_deltas.j2 | FULL 1 键 | `_ExtractKnowledgeDeltasResponse` | ✅ |
| AUDIT_POV_DRIFT | checking/（kernel 目录） | FULL 2 键 | `_AuditPovDriftResponse` | ✅ |

## 4. 初始化族

| TaskType | 模板 | 契约 required | Schema | 结论 |
|----------|------|---------------|--------|------|
| SPEC_ENRICH | initialization/enrich_spec.j2 | FULL 动态（spec_enrich schema） | `_SpecEnrichResponse` | ✅ |
| GENERATE_CONFIG | initialization/generate_config.j2 | FULL 动态（18 键） | `_CreativeConfigResponse` | ✅ |
| POLISH_CONFIG | initialization/generate_config.j2 | PARTIAL 宽松（同模板） | 同上 | ✅（信息守恒由模板规则承担） |
| INIT_STORY_BIBLE | initialization/init_story_bible.j2 | FULL `story_bible` | `_InitStoryBibleResponse` | ✅ |
| INIT_STORY_CORE_PREMISE | initialization/init_story_core_premise.j2 | FRAGMENT 1 键 | `_InitStoryCorePremiseResponse` | ✅ |
| INIT_STORY_WORLD_RULES | initialization/init_story_world_rules.j2 | FRAGMENT 1 键 | `_InitStoryWorldRulesResponse` | ✅ |
| INIT_STORY_CONTINUITY_RULES | initialization/init_story_continuity_rules.j2 | FRAGMENT 1 键 | `_InitStoryContinuityRulesResponse` | ✅ |
| INIT_STORY_THEMES_AND_SYMBOLS | initialization/init_story_themes_and_symbols.j2 | FRAGMENT 1 键 | `_InitStoryThemesAndSymbolsResponse` | ✅ |
| INIT_CHARACTER_BIBLE | initialization/init_character_bible.j2 | FULL `character_bible` + schema | `_InitCharacterBibleResponse` | ✅ |
| INIT_CHARACTER_ROSTER | initialization/init_character_roster.j2 | FRAGMENT 1 键 | `_InitCharacterRosterResponse` | ✅ |
| INIT_CHARACTER_PROFILE_BATCH | initialization/init_character_profile_batch.j2 | FRAGMENT 1 键 + schema | `_InitCharacterProfilesResponse` | ✅ |
| INIT_CHARACTER_RELATIONSHIP_MATRIX | initialization/init_character_relationship_matrix.j2 | FRAGMENT 1 键 | `_InitCharacterRelationshipMatrixResponse` | ✅ |
| INIT_CHARACTER_ARC_PLAN | initialization/init_character_arc_plan.j2 | FRAGMENT 1 键 | `_InitCharacterArcPlanResponse` | ✅ |
| INIT_NARRATIVE_CONTRACT | initialization/init_narrative_contract.j2 | FULL 6 键 | `_NarrativeContractResponse` | ✅ |
| INIT_ENTITY_REGISTRY | initialization/init_entity_registry.j2 | FULL `entities` | `_EntityRegistryResponse` | ✅ |
| INTRODUCE_CHARACTER | initialization/introduce_character.j2 | FULL 12 键 + schema | `_IntroduceCharacterResponse` | ✅ |
| ENRICH_CHARACTER | initialization/enrich_character.j2 | FRAGMENT 4 键 + schema | `_EnrichCharacterResponse` | ✅ |
| ADJUDICATE_CHARACTER_INTRODUCTION | initialization/adjudicate_character_introduction.j2 | FULL 2 键 | `_CharacterIntroductionAdjudicationResponse` | ✅ |
| RECONCILE_ENTITIES | initialization/reconcile_entities.j2 | FULL `resolutions` + schema | `_ReconcileEntitiesResponse` | ✅ |
| PROFILE_STYLE | checking/style_profile_derive.j2 | FRAGMENT 4 键 | `_StyleProfileResponse` | ✅ |
| PROFILE_STRUCTURE | checking/structure_profile_derive.j2 | FRAGMENT 4 键 | `_StructureProfileResponse` | ✅ |

## 5. Kernel 提取族

| TaskType | 模板 | 契约 required | Schema | 结论 |
|----------|------|---------------|--------|------|
| EXTRACT_CANON | kernel/extract_canon_delta.j2（全量路径） | FULL 7 键 + 🔴 require_native | `_ExtractCanonResponse` | ⚠️ native 硬依赖（G-04） |
| EXTRACT_CANON_DELTA | kernel/extract_canon_delta.j2 | FRAGMENT 1 键 | `_ExtractCanonDeltaResponse` | ✅ normalizer 与 StoryKernel 合并边界仅保留男/女/空值 |
| EXTRACT_CHAPTER_SUMMARY_EXIT | kernel/extract_chapter_summary_exit.j2 | FRAGMENT 2 键 | `_ExtractChapterSummaryExitResponse` | ✅ |
| EXTRACT_CREATIVE_REPORT | kernel/extract_creative_report.j2 | FRAGMENT 1 键 | `_ExtractCreativeReportResponse` | ✅ |
| EXTRACT_CHARACTER_STATE_DELTAS | kernel/extract_character_state_deltas.j2 | FRAGMENT 1 键 | `_ExtractCharacterStateDeltasResponse` | ✅ |
| EXTRACT_RELATIONSHIP_DELTAS | kernel/extract_relationship_deltas.j2 | FRAGMENT 1 键 | `_ExtractRelationshipDeltasResponse` | ✅ |
| EXTRACT_PLOT_THREAD_DELTAS | kernel/extract_plot_thread_deltas.j2 | FRAGMENT 1 键 | `_ExtractPlotThreadDeltasResponse` | ✅ |
| EXTRACT_CANDIDATE_STATE_DELTAS | kernel/extract_candidate_state_deltas.j2 | FRAGMENT 1 键 + 🔴 require_native | `_CandidateStateDeltasResponse` | ⚠️ native 硬依赖 |
| ADJUDICATE_ENTITY_REFERENCES | checking/adjudicate_entity_references.j2 | FULL 2 键 + schema | `_EntityReferenceAdjudicationResponse` | ✅ |
| ADJUDICATE_STATE_DELTA | checking/adjudicate_state_delta.j2 | FULL 4 键 + 🔴 require_native | `_StateDeltaAdjudicationResponse` | ⚠️ native 硬依赖 |
| ADJUDICATE_CONTRACT_COMPLETION | checking/adjudicate_contract_completion.j2 | FULL 11 键 + 🔴 require_native | `_ContractCompletionAdjudicationResponse` | ⚠️ native 硬依赖 |
| ADJUDICATE_FACT_CONFLICT | checking/adjudicate_fact_conflict.j2 | FULL 3 键 | `_StateDeltaAdjudicationResponse` | ✅ |
| ADJUDICATE_FINAL_STATE | kernel/adjudicate_final_state.j2 | FULL 6 键 + 🔴 require_native | `_FinalStateAdjudicationResponse` | ⚠️ native 硬依赖 |
| EXTRACT_MOTIFS | kernel/extract_motifs.j2 | FRAGMENT 1 键 | `_ExtractMotifsResponse` | ✅ |

## 6. 摘要 / 压缩 / 卷审计族

| TaskType | 模板 | 契约 required | Schema | 结论 |
|----------|------|---------------|--------|------|
| SUMMARIZE_CHAPTER | summary/summarize_chapter.j2 | FRAGMENT 1 键 | `_SummarizeChapterResponse` | ✅ |
| SUMMARIZE_VOLUME | summary/summarize_volume.j2 | FRAGMENT 1 键 | `_SummarizeVolumeResponse` | ✅ |
| SUMMARIZE_ARC | summary/summarize_arc.j2 | FRAGMENT 1 键 | `_SummarizeArcResponse` | ✅ |
| SUMMARIZE_SCENE | summary/summarize_scene.j2 | FRAGMENT 1 键 | `_SummarizeSceneResponse` | ✅ |
| VOLUME_AUDIT | summary/volume_audit.j2 | FULL 12 键 | `_VolumeAuditResponse` | ✅ |
| CONTEXT_COMPRESS | compression/context_compress.j2 | FRAGMENT `items` | `_CompressionItemsResponse` | ✅ |
| ADAPTIVE_COMPRESS | compression/adaptive_compress.j2 | FRAGMENT `items` | 同上 | ✅ |
| VERIFY_COMPRESSION | compression/verify_compression.j2 | FULL 2 键 | `_VerifyCompressionResponse` | ✅ |

## 7. 配音族（8 任务，详见 05 章）

| TaskType | 模板 | 契约 required | Schema | 解析器 | 结论 |
|----------|------|---------------|--------|--------|------|
| TTS_BUILD_NARRATOR_PROFILE | tts/build_narrator_profile.j2 | FULL 5 键 | 11 字段（含默认值） | narrator_profile_step（范围钳制） | ✅ 示例字段齐全且已标明不可照抄（G-11 已关闭） |
| TTS_GENERATE_DUBBING_SCRIPT | tts/generate_dubbing_script.j2 | FULL 5 键 | 5 键 | `_validate_script_text_fidelity` 逐字拼接 | ✅ 运行时版（390 行）编号 1-14 连续已修复（G-01 关闭）；温度 0.5 仍偏高（G-05） |
| TTS_ADJUDICATE_SCRIPT_SEGMENTS | tts/adjudicate_script_segments.j2 | FULL 2 键 + schema | 2 键 | 先验保留裁决 | ✅ |
| TTS_REVIEW_DUBBING_SCRIPT | tts/review_dubbing_script.j2 | FULL 4 键 + schema | 4 键 | apply_dubbing_review_decisions | ✅ |
| TTS_REWRITE_SPOKEN_TEXT | tts/rewrite_spoken_text.j2 | FULL 1 键 + schema | item 3 字段 | validate_spoken_rewrite（50%-130% + 相似度） | ✅ 模板约束与校验器一致 |
| TTS_EMOTION_LABEL | tts/emotion_label.j2 | FULL 1 键 + schema | item 4 字段 | emotion 回退（`_coerce_emotion`/`_coerce_intensity`，emotion_label.py:108/137） | ✅ 模板枚举覆盖完整 15 值 EmotionTag |
| TTS_ADJUDICATE_VOICE_MATCH | tts/adjudicate_voice_match.j2 | FULL 2 键 + schema | item 6 字段 | 按 verdict 分支校验 | ✅（schema required 仅 native 路由时冲突） |
| TTS_SOUND_DESIGN | tts/sound_design_extraction.j2 | FULL 4 键 | 4 键 | sound_design_extraction.py | ✅ |

## 8. 需要代码侧核对的遗留项（落地 P0/P1 时逐项验证）

| # | 核对项 | 现状 | 风险等级 | 验证方式 |
|---|--------|------|----------|----------|
| V-1 | emotion_label 非法枚举/越界强度的解析器回退实现 | ✅ 已核对：`_coerce_emotion`（emotion_label.py:108，非法值→关键词推理）+ `_coerce_intensity`（:137，钳制 [0.0,1.0]）存在，模板现覆盖完整枚举 | 低 | 已关闭 |
| V-2 | evaluate_reading_power 的 hook_type 枚举 ↔ repair_reading_power 类型 ↔ 解析器三方一致 | ✅ 已核对：`HookType`（`core/schemas/reading_power.py:22`，6 值 crisis/mystery/emotion/choice/desire/none）与模板枚举（evaluate_reading_power.j2:95）完全一致 | 低 | 已关闭 |
| V-3 | plan_chapter.scene_intents 与 plan_chapter_scenes.scene_plan 字段定义源 | ✅ 已核对：`_PlanChapterScenesPayloadResponse`（16 顶层字段）与 `_PlanChapterResponse`（16 顶层字段）字段集完全一致（scene_intents/world_rule_applications/required_literals/cross_scene_intent/…），scene_plan 内嵌 scene_intents；两任务为同一协议的不同批次组织，无字段漂移 | 低 | 已关闭 |
| V-4 | PARTIAL_OBJECT（POLISH_CONFIG / REPAIR_KNOWLEDGE_BOUNDARY）解析路径 | ✅ 已核对：`format_repair.py:378/396` 有完整 PARTIAL 分支（提示修复指令「只输出本轮新增/修改字段，不补全回显」）；宽松模式语义完整 | 低 | 已关闭 |
| V-5 | book_consistency 维度注入段是否必渲染（8 任务共用模板） | ✅ 已核对：`book_consistency_step.py:520/567/1002/1203` 总是设置 `active_audit_dimension`（默认 ""，spec.name 必填枚举），模板 `{% if active_audit_dimension %}` 段正常渲染；仅当 spec.name 异常为空时维度段缺失（理论风险，低） | 低 | 已关闭 |
| V-6 | TTS 字幕拆段（18-42 字）与 pause_marker_injection / timeline_builder 消费协议 | ✅ 已核对：LLM 拆段（18-42 字）直接成为 segments 即片段级字幕单位；timeline_builder（:182-191）只做「不为缺失/跳过段建字幕」与同步保护，不重新切分；无二次长度协议冲突（字词级字幕由 `tts_subtitle_word_level` 配置另行处理） | 低 | 已关闭 |
| V-7 | 六个 require_native_structured_output 任务的降级路径 | ✅ 已核对：`router.py:1519-1554` 对不支持 native 的 route 整体拒绝；全部不满足时抛 `ModelGatewayError`（"task requires native structured output but no configured stream route supports it"）——**无 prompt-only 降级**，确认 G-04 为硬依赖 | 中 | 维持现状（文档化）或加降级开关 |
| V-8 | responsibility_matrix 宏是否死代码 | ✅ 已核对：仅在 `_role_definitions.j2` 定义处出现，无任何调用方模板引用——确认为死代码 | 低 | 已关闭（可删除或补调用） |
| V-9 | Canon 性别值在提取与持久化边界的白名单约束 | ✅ `normalize_gender_value()` 仅接受男/女及其常见英文别名；`CharacterNormalizer` 与 StoryKernel 合并均使用该规范化，未知值降为空 | 中→低 | 已关闭并有回归测试 |

## 9. 全局核对结论

- 本矩阵的历史“128 + 11 = 139”汇总不是当前契约注册表的全量计数。当前注册表有 142 个 TaskType 契约；`verify_format_contracts.py` 因 `GENERATE_CONFIG` / `POLISH_CONFIG` 的动态分支渲染 144 个变体，均可渲染。
- **无 ❌ 级（模板与解析器直接冲突）发现**——格式契约体系整体健康，风险集中在：编号卫生（提示词层）、温度与校验强度匹配（参数层）、native 路由依赖（架构层）、枚举词汇漂移（语义层）
- 已关闭的格式与枚举缺口不再需要提示词层改动。剩余事项只有需要真实运行数据或产品决策的温度 A/B、native structured output 降级策略，以及 WAVE 告警是否升级为硬门。
