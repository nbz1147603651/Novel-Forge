"""
代词一致性检查步骤
在章节生成后自动检查角色代词使用是否一致
"""

from __future__ import annotations

import re
from logging import Logger
from typing import Any, TypedDict

from novel_forge.core.constants import (
    FEMALE_GENDER_INDICATORS,
    MALE_GENDER_INDICATORS,
)
from novel_forge.obs.logger import get_logger
from novel_forge.pipeline.steps.base import PipelineStep


class PronounInfo(TypedDict, total=False):
    """Character pronoun expectations derived from canon metadata."""

    correct: str
    wrong: str
    gender: str


class PronounIssue(TypedDict):
    """A single pronoun mismatch detected in chapter text."""

    character: str
    expected: str
    found: str
    position: tuple[int, int]
    match_text: str
    context: str
    severity: str


class PronounCheckResult(TypedDict, total=False):
    """Structured result returned by the pronoun consistency checker."""

    passed: bool
    score: float
    total_issues: int
    pov_issues: int
    issues: list[PronounIssue]
    message: str
    requires_rewrite: bool


class PronounCheckStep(PipelineStep[dict[str, Any], PronounCheckResult]):
    """
    代词一致性检查步骤
    
    检查生成的章节中，角色代词使用是否与character_bible一致
    如发现不一致，返回修复建议或触发重写
    
    通用设计：从context动态加载角色信息，不依赖特定项目
    """
    
    @property
    def step_name(self) -> str:
        return "pronoun_check"
    
    # 代词映射表
    PRONOUN_MAP = {
        "女": {"correct": "她", "wrong": "他"},
        "男": {"correct": "他", "wrong": "她"},
        "中性": {"correct": "它", "wrong": "他/她"},
    }

    # 性别相关代词列表
    GENDERED_PRONOUNS = {"他", "她", "他/她", "他或她", "他们", "她们", "他/她/它"}
    _PRONOUN_SCAN_CHARS = tuple(sorted(set("".join(GENDERED_PRONOUNS) + "它")))
    _OBJECT_PRONOUN_LEFT_CHARS = frozenset(
        "在向对给朝问看望盯凝瞧叫喊劝扶拉拽抱推迎送拦找寻追避躲替为把被让同跟和与着"
    )
    _OBJECT_PRONOUN_LEFT_SUFFIXES = frozenset(
        {
            "打断",
            "走到",
            "来到",
            "靠近",
            "靠到",
            "贴近",
            "越过",
            "经过",
            "绕过",
            "面向",
            "看向",
            "望向",
            "盯着",
            "看着",
            "凝着",
            "瞧着",
            "问起",
            "问向",
            "告诉",
            "回应",
            "安慰",
            "提醒",
            "拦住",
            "拉住",
            "扶住",
            "推开",
            "迎上",
            "送到",
            "带到",
            "留给",
            "递给",
            "交给",
            "还给",
        }
    )
    
    # 检查阈值
    CRITICAL_THRESHOLD = 1  # 超过此数量视为严重错误，需要重写
    WARNING_THRESHOLD = 0   # 超过此数量视为警告

    @classmethod
    def _normalize_gender(cls, value: Any) -> str:
        if value is None:
            return ""
        text = str(value).strip()
        if not text:
            return ""
        lowered = text.lower()
        if lowered in {"male", "man", "m", "男"}:
            return "男"
        if lowered in {"female", "woman", "f", "女"}:
            return "女"
        return text
    
    async def _execute(self, context: dict[str, Any]) -> PronounCheckResult:
        previous = context.get("previous_pronoun_reports")
        return self.evaluate_context(
            context,
            logger=self._logger,
            previous_reports=previous if isinstance(previous, list) else None,
        )

    @classmethod
    def evaluate_context(
        cls,
        context: dict[str, Any],
        *,
        logger: Logger | None = None,
        previous_reports: list[PronounCheckResult] | None = None,
    ) -> PronounCheckResult:
        """
        执行代词一致性检查

        Args:
            context: 包含chapter_text, chapter_plan, character_bible等
            previous_reports: 前序章节的代词检查结果，用于跨章一致性检测

        Returns:
            包含检查结果的字典
        """
        chapter_text = str(context.get("chapter_text", "") or "")
        chapter_plan = context.get("chapter_plan", {})
        if not isinstance(chapter_plan, dict):
            chapter_plan = {}
        pov_character = chapter_plan.get("pov_character", "")
        chapter_number = context.get("chapter_number", 0)

        # 从context动态加载角色信息
        character_bible = context.get("character_bible", {})
        canon_context = context.get("canon_context", {})
        kernel_entities: list[dict[str, Any]] | None = context.get("kernel_context")

        # 获取章节涉及角色列表（如有），用于精准过滤
        involved_characters: list[str] = chapter_plan.get("involved_characters", [])
        if not isinstance(involved_characters, list):
            involved_characters = []

        # 构建角色代词映射
        character_pronouns = cls._build_character_pronouns(
            character_bible,
            canon_context,
            involved_characters=involved_characters,
            kernel_entities=kernel_entities,
        )

        # 跨章代词一致性预检
        cross_chapter_issues: list[PronounIssue] = []
        if previous_reports:
            historical = _extract_historical_pronouns(previous_reports)
            cross_chapter_issues = _check_cross_chapter_pronouns(
                chapter_text, character_pronouns, historical
            )
        
        if not chapter_text:
            return {
                "passed": True,
                "score": 10.0,
                "issues": [],
                "message": "无文本内容，跳过检查"
            }
        
        # 如果没有角色信息，跳过检查
        if not character_pronouns:
            return {
                "passed": True,
                "score": 10.0,
                "issues": [],
                "message": "无角色信息，跳过检查"
            }

        # 快速短路：没有任何性别代词字符时，不做逐角色正则扫描。
        if not any(ch in chapter_text for ch in cls._PRONOUN_SCAN_CHARS):
            return {
                "passed": True,
                "score": 10.0,
                "issues": [],
                "message": "文本未检测到代词，跳过检查",
                "requires_rewrite": False,
            }

        # 只检查在正文中实际出现过名字的角色，降低长篇多角色时的扫描开销。
        active_character_pronouns = {
            name: info
            for name, info in character_pronouns.items()
            if name and name in chapter_text
        }
        if not active_character_pronouns:
            # Fallback: when names are omitted (common in tight POV narration),
            # still check whether POV pronouns are obviously flipped.
            pov_info = character_pronouns.get(pov_character, {})
            pov_fallback_issues = cls._check_unanchored_pov_pronouns(
                chapter_text,
                pov_character,
                pov_info,
                character_pronouns,
            )
            if not pov_fallback_issues:
                return {
                    "passed": True,
                    "score": 10.0,
                    "issues": [],
                    "message": "正文未出现角色名，跳过检查",
                    "requires_rewrite": False,
                }
            score = cls._calculate_score(pov_fallback_issues, pov_fallback_issues)
            return {
                "passed": False,
                "score": score,
                "total_issues": len(pov_fallback_issues),
                "pov_issues": len(pov_fallback_issues),
                "issues": pov_fallback_issues,
                "message": "检测到未锚定 POV 代词疑似错误",
                "requires_rewrite": True,
            }

        # 执行检查
        all_issues: list[PronounIssue] = list(cross_chapter_issues)

        for char_name, char_info in active_character_pronouns.items():
            issues = cls._check_character_pronouns(
                chapter_text,
                char_name,
                char_info,
                active_character_pronouns,
            )
            all_issues.extend(issues)
            
            plural_issues = cls._check_plural_pronouns(
                chapter_text, char_name, char_info
            )
            all_issues.extend(plural_issues)
        
        for char_name, char_info in active_character_pronouns.items():
            if char_info.get("gender") != "中性":
                continue
            neutral_issues = cls._check_neutral_characters(
                chapter_text, char_name
            )
            all_issues.extend(neutral_issues)
        
        # 特别关注POV角色的代词使用
        pov_issues = [i for i in all_issues if i["character"] == pov_character]
        
        # 计算得分
        score = cls._calculate_score(all_issues, pov_issues)
        
        # 判断是否通过
        passed = len(pov_issues) == 0 and len(all_issues) <= cls.WARNING_THRESHOLD
        
        result: PronounCheckResult = {
            "passed": passed,
            "score": score,
            "total_issues": len(all_issues),
            "pov_issues": len(pov_issues),
            "issues": all_issues,
            "requires_rewrite": len(pov_issues) > 0 or len(all_issues) > cls.CRITICAL_THRESHOLD,
        }
        
        # 记录日志
        if not passed:
            if logger is not None:
                logger.warning(
                f"第{chapter_number}章代词检查未通过: "
                f"发现{len(all_issues)}处问题，其中POV角色{pov_character}有{len(pov_issues)}处"
                )
                for issue in all_issues[:5]:  # 只记录前5个
                    logger.warning(f"  - {issue['character']}: {issue['context'][:50]}...")
        elif logger is not None:
            logger.info(f"第{chapter_number}章代词检查通过")
        
        return result

    @classmethod
    def _check_unanchored_pov_pronouns(
        cls,
        text: str,
        pov_character: str,
        pov_info: PronounInfo,
        all_character_pronouns: dict[str, PronounInfo],
    ) -> list[PronounIssue]:
        """Fallback detector for name-light POV passages.

        We only flag high-confidence cases:
        - wrong pronoun appears in narrative (not dialogue)
        - pronoun is likely clause-subject (sentence/sub-clause start)
        - nearby context has no opposite-gender named character or noun indicator
        """
        if not pov_character:
            return []

        correct = pov_info.get("correct", "")
        wrong = pov_info.get("wrong", "")
        gender = pov_info.get("gender", "")
        if not correct or not wrong or gender not in {"男", "女"}:
            return []

        opposite_indicators = (
            MALE_GENDER_INDICATORS if gender == "女" else FEMALE_GENDER_INDICATORS
        )
        issues: list[PronounIssue] = []

        for idx, ch in enumerate(text):
            if ch != wrong:
                continue
            if cls._is_in_dialogue(text, idx):
                continue
            if not cls._is_likely_subject_pronoun(text, idx):
                continue

            context_start = max(0, idx - 100)
            context_end = min(len(text), idx + 100)
            local = text[context_start:context_end]

            has_other_gender_name = any(
                name in local
                for name, info in all_character_pronouns.items()
                if name
                and name != pov_character
                and info.get("gender") in {"男", "女"}
                and info.get("gender") != gender
            )
            if has_other_gender_name:
                continue
            if any(ind in local for ind in opposite_indicators):
                continue

            excerpt_start = max(0, idx - 30)
            excerpt_end = min(len(text), idx + 30)
            issues.append(
                {
                    "character": pov_character,
                    "expected": correct,
                    "found": wrong,
                    "position": (idx, idx + 1),
                    "match_text": text[idx:idx + 1],
                    "context": text[excerpt_start:excerpt_end],
                    "severity": "critical",
                }
            )

        return issues

    @staticmethod
    def _is_likely_subject_pronoun(text: str, pos: int) -> bool:
        """Heuristic used for unanchored POV fallback detection."""
        if pos < 0 or pos >= len(text):
            return False
        left = pos - 1
        while left >= 0 and text[left].isspace():
            left -= 1
        if left < 0:
            return True
        return text[left] in {
            "。", "！", "？", "；", "：", "，", "\n",
            "“", "”", "「", "」", "『", "』", "（", "(", "[", "【",
        }

    @classmethod
    def _is_likely_character_coreference_pronoun(cls, text: str, pos: int) -> bool:
        """Return True only for pronouns likely referring back to the named character.

        The name-anchored scanner intentionally looks within a local window after
        a character name.  In prose that window often contains object pronouns
        referring to another person, e.g. ``陆云峥看向她`` or ``问她：``.  Those
        should not be treated as pronoun flips for 陆云峥.
        """

        if cls._is_likely_subject_pronoun(text, pos):
            return True

        return not cls._has_object_relation_before_pronoun(text, pos)

    @classmethod
    def _has_object_relation_before_pronoun(cls, text: str, pos: int) -> bool:
        left = pos - 1
        while left >= 0 and text[left].isspace():
            left -= 1
        if left < 0:
            return False
        if text[left] in cls._OBJECT_PRONOUN_LEFT_CHARS:
            return True
        prefix = text[max(0, left - 5): pos]
        return any(prefix.endswith(suffix) for suffix in cls._OBJECT_PRONOUN_LEFT_SUFFIXES)
    
    @classmethod
    def _build_character_pronouns_from_entities(
        cls,
        entities: list[dict[str, Any]],
        involved_characters: list[str] | None = None,
    ) -> dict[str, PronounInfo]:
        """Build character pronoun mapping from StoryKernel entities.

        Used when context includes ``kernel_context`` (serialized Entity list)
        instead of legacy ``character_bible``/``canon_context``.

        Only entities with ``entity_type == "character"`` and a recognized
        gender attribute are included.
        """
        involved_set = set(involved_characters) if involved_characters else None
        character_pronouns: dict[str, PronounInfo] = {}

        for entity in entities:
            if not isinstance(entity, dict):
                continue
            entity_type = str(entity.get("entity_type", "")).strip().lower()
            if entity_type != "character":
                continue
            name = str(entity.get("name", "")).strip()
            if not name:
                continue
            if involved_set is not None and name not in involved_set:
                continue
            attributes = entity.get("attributes", {})
            if not isinstance(attributes, dict):
                continue
            gender = cls._normalize_gender(attributes.get("gender", ""))
            if gender in cls.PRONOUN_MAP:
                character_pronouns[name] = {
                    "gender": gender,
                    "correct": cls.PRONOUN_MAP[gender]["correct"],
                    "wrong": cls.PRONOUN_MAP[gender]["wrong"],
                }

        return character_pronouns

    @classmethod
    def _build_character_pronouns(
        cls,
        character_bible: dict[str, Any],
        canon_context: dict[str, Any],
        involved_characters: list[str] | None = None,
        kernel_entities: list[dict[str, Any]] | None = None,
    ) -> dict[str, PronounInfo]:
        """
        从character_bible或canon_context构建角色代词映射
        
        支持多种数据格式：
        1. character_bible: {"characters": [{"name": "...", "gender": "..."}, ...]}
        2. character_bible: {"name": {"gender": "...", ...}, ...}
        3. canon_context: {"characters": {"name": {"gender": "..."}, ...}}
        
        当 involved_characters 不为空时，只构建涉及角色的代词映射，
        减少不相关角色带来的扫描开销和误报。
        
        Returns:
            {"角色名": {"gender": "...", "correct": "...", "wrong": "..."}, ...}
        """
        involved_set = set(involved_characters) if involved_characters else None
        character_pronouns: dict[str, PronounInfo] = {}
        
        # 从character_bible读取（格式1：列表）
        if isinstance(character_bible, dict):
            characters_list = character_bible.get("characters", [])
            if isinstance(characters_list, list):
                for char in characters_list:
                    if isinstance(char, dict):
                        name = char.get("name", "")
                        gender = cls._normalize_gender(char.get("gender", ""))
                        if name and gender in cls.PRONOUN_MAP:
                            if involved_set is not None and name not in involved_set:
                                continue
                            character_pronouns[name] = {
                                "gender": gender,
                                "correct": cls.PRONOUN_MAP[gender]["correct"],
                                "wrong": cls.PRONOUN_MAP[gender]["wrong"],
                            }
            
            # 从character_bible读取（格式2：字典）
            for key, value in character_bible.items():
                    if isinstance(value, dict) and "gender" in value:
                        gender = cls._normalize_gender(value.get("gender", ""))
                        if gender in cls.PRONOUN_MAP:
                            if involved_set is not None and key not in involved_set:
                                continue
                            character_pronouns[key] = {
                                "gender": gender,
                                "correct": cls.PRONOUN_MAP[gender]["correct"],
                                "wrong": cls.PRONOUN_MAP[gender]["wrong"],
                            }
        
        # canon_context 仅作兜底，不能覆盖 CharacterBible 的权威性别。
        if isinstance(canon_context, dict):
            canon_characters = canon_context.get("characters", {})
            if isinstance(canon_characters, dict):
                for name, state in canon_characters.items():
                    if name in character_pronouns:
                        continue
                    if isinstance(state, dict):
                        gender = cls._normalize_gender(state.get("gender", ""))
                        if gender in cls.PRONOUN_MAP:
                            if involved_set is not None and name not in involved_set:
                                continue
                            character_pronouns[name] = {
                                "gender": gender,
                                "correct": cls.PRONOUN_MAP[gender]["correct"],
                                "wrong": cls.PRONOUN_MAP[gender]["wrong"],
                            }

        # kernel_context (StoryKernel entities) as last-resort fallback.
        if kernel_entities and not character_pronouns:
            character_pronouns = cls._build_character_pronouns_from_entities(
                kernel_entities,
                involved_characters=involved_characters,
            )

        return character_pronouns
    
    @classmethod
    def _check_character_pronouns(
        cls,
        text: str,
        char_name: str,
        char_info: PronounInfo,
        all_character_pronouns: dict[str, PronounInfo] | None = None,
    ) -> list[PronounIssue]:
        """
        检查特定角色的代词使用
        
        策略：在角色名出现后的一段距离内，检查是否使用了错误代词
        """
        issues: list[PronounIssue] = []
        correct = char_info.get("correct", "")
        wrong = char_info.get("wrong", "")
        if not correct or not wrong:
            return issues

        # Indicators of the same gender as `wrong`: if any appear in the gap
        # between char_name and the suspicious pronoun, the pronoun most likely
        # refers to an unnamed NPC — skip as false positive.
        # e.g. "风伏京…中年男人…他" → "他" refers to 中年男人, not 风伏京.
        wrong_gender_indicators = (
            MALE_GENDER_INDICATORS if wrong == "他" else FEMALE_GENDER_INDICATORS
        )
        
        # 模式: 角色名 + 任意内容(0-150字符) + 错误代词 + 标点
        # 使用非贪婪匹配，避免跨段落匹配
        pattern = (
            rf"({re.escape(char_name)}[\s\S]{{0,150}}?)"
            rf"({re.escape(wrong)})(?=[\s，。；：、！？\u201c\u201d\u2018\u2019\"'])"
        )
        
        for match in re.finditer(pattern, text):
            start, end = match.span(2)
            
            # 检查是否在对话中（如果是，可能是引用他人话语，跳过）
            if cls._is_in_dialogue(text, start):
                continue

            # Skip if gap contains a gender-indicator noun anchoring the pronoun
            # to a different (unnamed) NPC rather than char_name.
            gap = match.group(1)
            after_name = gap[len(char_name):].lstrip()
            if after_name.startswith(("的", "之")):
                continue
            if any(ind in gap for ind in wrong_gender_indicators):
                continue
            if not cls._is_likely_character_coreference_pronoun(text, start):
                continue
            if all_character_pronouns:
                own_gender = char_info.get("gender", "")
                has_opposite_named_anchor = any(
                    name in gap
                    for name, info in all_character_pronouns.items()
                    if name
                    and name != char_name
                    and info.get("gender") in {"男", "女"}
                    and info.get("gender") != own_gender
                )
                if has_opposite_named_anchor:
                    continue
            
            # 获取上下文
            context_start = max(0, start - 30)
            context_end = min(len(text), end + 30)
            context = text[context_start:context_end]
            
            issues.append({
                "character": char_name,
                "expected": correct,
                "found": wrong,
                "position": (start, end),
                "match_text": match.group(0),
                "context": context,
                "severity": "critical",
            })
        
        return issues
    
    @classmethod
    def _check_plural_pronouns(
        cls,
        text: str,
        char_name: str,
        char_info: PronounInfo,
    ) -> list[PronounIssue]:
        """
        检查复数代词使用（如"他们/她们"用于单数角色）
        
        策略：检测角色名出现在前文中，但后面使用了复数代词
        """
        issues: list[PronounIssue] = []
        correct = char_info.get("correct", "")
        wrong = char_info.get("wrong", "")
        if not correct or not wrong:
            return issues
        correct_plural = correct + "们"  # 他 -> 他们, 她 -> 她们
        wrong_plural = wrong + "们" if wrong in {"他", "她"} else None
        
        # 检测：角色名 + 150字符内 + 错误的复数代词
        if wrong_plural:
            pattern = (
                rf"({re.escape(char_name)}[\s\S]{{0,150}}?)"
                rf"({re.escape(wrong_plural)})(?=[\s，。；：、！？""''\"'])"
            )
            
            for match in re.finditer(pattern, text):
                start, end = match.span(2)
                
                if cls._is_in_dialogue(text, start):
                    continue
                
                context_start = max(0, start - 30)
                context_end = min(len(text), end + 30)
                context = text[context_start:context_end]
                
                issues.append({
                    "character": char_name,
                    "expected": correct_plural,
                    "found": wrong_plural,
                    "position": (start, end),
                    "match_text": match.group(0),
                    "context": context,
                    "severity": "medium",
                })
        
        return issues
    
    @classmethod
    def _check_neutral_characters(
        cls,
        text: str,
        char_name: str,
    ) -> list[PronounIssue]:
        """
        检查中性角色（如物体、动物）是否被错误地使用了性别代词
        
        策略：检测"它"以外的人称代词用于可能是中性的角色
        """
        issues: list[PronounIssue] = []
        
        # 检测：他/她 + 50字符内 + 角色名（可能的中性角色）
        pattern = (
            rf"([他她][\s\S]{{0,50}}?)"
            rf"({re.escape(char_name)})"
        )
        
        for match in re.finditer(pattern, text):
            start, end = match.span(1)
            
            if cls._is_in_dialogue(text, start):
                continue
            
            found_pronoun = match.group(1).strip()[:2]
            
            context_start = max(0, start - 30)
            context_end = min(len(text), end + 30)
            context = text[context_start:context_end]
            
            issues.append({
                "character": char_name,
                "expected": "它",
                "found": found_pronoun,
                "position": (start, end),
                "match_text": match.group(0),
                "context": context,
                "severity": "low",
            })
        
        return issues
    
    @staticmethod
    def _is_in_dialogue(text: str, pos: int) -> bool:
        """检查指定位置是否在对话引号内"""
        quote_pairs = {
            "“": "”",
            "‘": "’",
            "「": "」",
            "『": "』",
        }
        toggled_quotes = {'"', "'"}
        stack: list[str] = []

        for char in text[:pos]:
            if char in quote_pairs:
                stack.append(quote_pairs[char])
                continue
            if char in toggled_quotes:
                if stack and stack[-1] == char:
                    stack.pop()
                else:
                    stack.append(char)
                continue
            if stack and char == stack[-1]:
                stack.pop()

        return bool(stack)
    
    @staticmethod
    def _calculate_score(
        all_issues: list[PronounIssue],
        pov_issues: list[PronounIssue],
    ) -> float:
        """计算代词一致性得分"""
        base_score = 10.0
        
        # POV角色代词错误扣分更严重
        for _ in pov_issues:
            base_score -= 3.0
        
        # 其他角色代词错误
        non_pov_issues = [i for i in all_issues if i not in pov_issues]
        for _ in non_pov_issues:
            base_score -= 1.0
        
        return max(0.0, base_score)
    
    @staticmethod
    def generate_fix_prompt(
        chapter_text: str,
        issues: list[PronounIssue],
        character_pronouns: dict[str, PronounInfo] | None = None,
    ) -> str:
        """
        生成修复提示，用于连续性修复步骤
        
        Args:
            chapter_text: 章节文本
            issues: 发现的问题列表
            character_pronouns: 角色代词映射（可选，如果未提供则从issues推断）
        """
        if not issues:
            return ""
        
        # 如果没有提供character_pronouns，从issues推断
        if character_pronouns is None:
            character_pronouns = {}
            for issue in issues:
                char = issue["character"]
                if char not in character_pronouns:
                    character_pronouns[char] = {
                        "correct": issue["expected"],
                        "wrong": issue["found"]
                    }
        
        prompt_parts = ["【代词一致性修复要求】"]
        prompt_parts.append("以下代词使用错误必须修复：\n")
        
        # 按角色分组
        char_issues: dict[str, list[PronounIssue]] = {}
        for issue in issues:
            char = issue["character"]
            if char not in char_issues:
                char_issues[char] = []
            char_issues[char].append(issue)
        
        for char, char_issue_list in char_issues.items():
            char_info: PronounInfo = character_pronouns.get(char, {})
            gender = char_info.get("gender", "未知")
            correct = char_info.get("correct", char_issue_list[0]["expected"])
            wrong = char_info.get("wrong", char_issue_list[0]["found"])
            
            prompt_parts.append(
                f"- {char}（性别：{gender}）："
                f"必须使用'{correct}'，"
                f"严禁使用'{wrong}'"
            )
            prompt_parts.append(f"  发现 {len(char_issue_list)} 处错误，例如：")
            for issue in char_issue_list[:2]:  # 只显示前2个示例
                prompt_parts.append(f"    • {issue['context'][:60]}...")
        
        prompt_parts.append("\n修复要求：")
        prompt_parts.append("1. 全文检查，确保每个角色的代词使用与其性别一致")
        prompt_parts.append("2. 特别注意POV角色的代词，必须100%正确")
        prompt_parts.append("3. 保留原文的所有情节、对话和风格")
        prompt_parts.append("4. 仅修改代词，不修改其他内容")
        
        return "\n".join(prompt_parts)


# 快捷函数，用于在pipeline中调用
async def check_pronoun_consistency(
    chapter_text: str,
    chapter_plan: dict[str, Any],
    chapter_number: int = 0,
    character_bible: dict[str, Any] | None = None,
    canon_context: dict[str, Any] | None = None,
    kernel_context: list[dict[str, Any]] | None = None,
    router: Any = None,
    builder: Any = None,
    settings: Any = None,
) -> PronounCheckResult:
    """
    快速检查代词一致性
    
    Args:
        chapter_text: 章节文本
        chapter_plan: 章节计划
        chapter_number: 章节编号
        character_bible: 角色圣经数据（可选）
        canon_context: Canon上下文（可选）
        kernel_context: StoryKernel entities 字段切片（可选）
        router: ModelRouter实例（可选，用于创建step）
        builder: PromptBuilder实例（可选，用于创建step）
        settings: Settings实例（可选，用于创建step）
        
    Returns:
        检查结果字典
    """
    context: dict[str, Any] = {
        "chapter_text": chapter_text,
        "chapter_plan": chapter_plan,
        "chapter_number": chapter_number,
        "character_bible": character_bible or {},
        "canon_context": canon_context or {},
    }
    if kernel_context is not None:
        context["kernel_context"] = kernel_context
    
    # 如果提供了完整的依赖，创建step实例并运行
    if router is not None and builder is not None and settings is not None:
        step = PronounCheckStep(router, builder, settings=settings)
        return await step.run(context)
    
    return PronounCheckStep.evaluate_context(
        context,
        logger=get_logger("pronoun_check"),
    )


# ---------------------------------------------------------------------------
# Cross-chapter pronoun helpers
# ---------------------------------------------------------------------------


def _extract_historical_pronouns(
    previous_reports: list[PronounCheckResult],
) -> dict[str, str]:
    """Build a character -> expected pronoun map from prior chapter reports.

    Scans each previous report for the *correct* pronoun recorded for every
    character that had an issue.  If multiple chapters disagree, the most
    recent chapter wins.
    """
    historical: dict[str, str] = {}
    for report in previous_reports:
        for issue in report.get("issues", []):
            char = issue.get("character", "")
            expected = issue.get("expected", "")
            if char and expected:
                historical[char] = expected
    return historical


def _check_cross_chapter_pronouns(
    text: str,
    character_pronouns: dict[str, PronounInfo],
    historical: dict[str, str],
) -> list[PronounIssue]:
    """Detect pronoun flips between current text and historical usage.

    If a character was consistently '她' in previous chapters but now
    appears with '他', flag it as a cross-chapter consistency issue.
    """
    issues: list[PronounIssue] = []
    for char_name, char_info in character_pronouns.items():
        historical_pronoun = historical.get(char_name)
        if not historical_pronoun:
            continue
        current_correct = char_info.get("correct", "")
        if not current_correct or current_correct == historical_pronoun:
            continue
        wrong = char_info.get("wrong", "")
        for m in re.finditer(re.escape(wrong), text):
            idx = m.start()
            if PronounCheckStep._is_in_dialogue(text, idx):
                continue
            excerpt_start = max(0, idx - 30)
            excerpt_end = min(len(text), idx + 30)
            issues.append(
                {
                    "character": char_name,
                    "expected": historical_pronoun,
                    "found": wrong,
                    "position": (idx, idx + 1),
                    "match_text": wrong,
                    "context": text[excerpt_start:excerpt_end],
                    "severity": "critical",
                }
            )
    return issues
