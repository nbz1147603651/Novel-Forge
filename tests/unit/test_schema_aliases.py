"""Unit tests for _SchemaFixMixin._field_aliases coverage."""

from __future__ import annotations

from pydantic import Field

from novel_forge.core.schemas.base import VersionedSchema, _SchemaFixMixin


class AliasTestSchema(VersionedSchema):
    """Test schema with typical field names for alias testing."""

    _correct_fields: set[str] = {
        "character_name",
        "chapter_number",
        "description",
        "motifs",
        "summary",
        "word_count",
        "pov_character",
        "element_focus",
    }

    character_name: str = Field(default="")
    chapter_number: int = Field(default=1)
    description: str = Field(default="")
    motifs: list[str] = Field(default_factory=list)
    summary: str = Field(default="")
    word_count: int = Field(default=0)
    pov_character: str = Field(default="")
    element_focus: list[str] = Field(default_factory=list)


class TestFieldAliasesBasics:
    """Test basic _field_aliases functionality."""

    def test_camelcase_to_snakecase_aliases(self):
        """camelCase LLM output should be mapped to snake_case canonical names."""
        data = {
            "characterName": "张三",
            "chapterNumber": 5,
            "description": "测试章节",
        }
        result = AliasTestSchema.model_validate(data)
        assert result.character_name == "张三"
        assert result.chapter_number == 5
        assert result.description == "测试章节"

    def test_abbreviation_aliases(self):
        """Common abbreviations should be mapped to full field names."""
        data = {
            "desc": "简短描述",
            "motif": ["母题A", "母题B"],
        }
        result = AliasTestSchema.model_validate(data)
        assert result.description == "简短描述"
        assert result.motifs == ["母题A", "母题B"]

    def test_content_variant_aliases(self):
        """Content variants like titleText should map to correct fields."""
        data = {
            "summaryText": "章节摘要内容",
            "wordCount": 3000,
        }
        result = AliasTestSchema.model_validate(data)
        assert result.summary == "章节摘要内容"
        assert result.word_count == 3000

    def test_pov_character_alias(self):
        """POV character aliases should work."""
        data = {"povChar": "李四"}
        result = AliasTestSchema.model_validate(data)
        assert result.pov_character == "李四"

    def test_alias_with_correct_key_present(self):
        """When both correct key and alias are present, correct key takes precedence."""
        data = {
            "description": "via correct key",
            "desc": "via abbreviation",
        }
        result = AliasTestSchema.model_validate(data)
        assert result.description == "via correct key"


class TestBaseMixinsAliases:
    """Test that base _SchemaFixMixin aliases are inherited/available."""

    def test_base_aliases_exist(self):
        """Base _SchemaFixMixin should have at least 20 aliases defined."""
        base_aliases = _SchemaFixMixin._field_aliases
        assert len(base_aliases) >= 20, (
            f"Base _SchemaFixMixin._field_aliases should have >=20 aliases, got {len(base_aliases)}"
        )

    def test_base_aliases_cover_camelcase(self):
        """Base aliases should cover common camelCase conversions."""
        base_aliases = _SchemaFixMixin._field_aliases

        required_camelcase = [
            "characterName",
            "chapterNumber",
            "elementFocus",
            "alignmentScore",
        ]
        for alias in required_camelcase:
            assert alias in base_aliases, f"Missing camelCase alias: {alias}"

    def test_base_aliases_cover_abbreviations(self):
        """Base aliases should cover common abbreviations."""
        base_aliases = _SchemaFixMixin._field_aliases

        required_abbrevs = ["desc", "motif", "char", "cnt", "num"]
        for alias in required_abbrevs:
            assert alias in base_aliases, f"Missing abbreviation alias: {alias}"

    def test_inherited_aliases_work_on_schema(self):
        """Schema inheriting from VersionedSchema should benefit from base aliases."""

        class MinimalSchema(VersionedSchema):
            _correct_fields: set[str] = {"description", "word_count", "character_name"}

            description: str = Field(default="")
            word_count: int = Field(default=0)
            character_name: str = Field(default="")

        data = {
            "desc": "test description",
            "wordCount": 1500,
            "characterName": "张三",
        }
        result = MinimalSchema.model_validate(data)
        assert result.description == "test description"
        assert result.word_count == 1500
        assert result.character_name == "张三"


class TestSchemaSpecificAliases:
    """Test schema-specific _field_aliases (subclass-defined) still work."""

    def test_chapter_outline_aliases(self):
        """ChapterOutline schema-specific aliases should still work."""
        from novel_forge.core.schemas.outline import ChapterOutline

        data = {
            "focus_elements": ["elem1", "elem2"],
            "element_focus_ids": ["elem3"],
            "chapter_number": 1,
            "goal": "测试目标",
        }
        result = ChapterOutline.model_validate(data)
        assert "elem1" in result.element_focus
        assert "elem2" in result.element_focus

    def test_chapter_outline_accepts_outline_batch_field_aliases(self):
        """PLAN_OUTLINE_BATCH/CONTINUE drift aliases should parse into ChapterOutline."""
        from novel_forge.core.schemas.outline import ChapterOutline

        result = ChapterOutline.model_validate(
            {
                "chapter_number": 1,
                "chapter_title": "逆光重逢",
                "goal": "建立宿命初遇。",
                "pov": "沈念卿",
                "main_location": "上海国际会议中心VIP休息室",
                "estimated_word_count": 4500,
                "note": "开篇建立宿命感。",
                "subplot_focus": None,
            }
        )

        assert result.title == "逆光重逢"
        assert result.pov_character == "沈念卿"
        assert result.setting == "上海国际会议中心VIP休息室"
        assert result.expected_word_count == 4500
        assert result.notes == "开篇建立宿命感。"
        assert result.subplot_focus == ""

    def test_chapter_outline_accepts_minimax_scene_and_word_count_aliases(self):
        """MiniMax outline batches often use scene arrays and target_word_count."""
        from novel_forge.core.schemas.outline import ChapterOutline

        result = ChapterOutline.model_validate(
            {
                "chapter_number": 19,
                "title": "暗流涌动",
                "goal": "商业间谍潜伏期。",
                "beats_summary": ["服务器日志异常。"],
                "main_scenes": ["智云科技办公室深夜", "锦绣品牌供应链会议室"],
                "target_word_count": 4500,
            }
        )

        assert result.setting == "智云科技办公室深夜、锦绣品牌供应链会议室"
        assert result.expected_word_count == 4500

    def test_subplot_plan_aliases(self):
        """SubplotPlan schema-specific aliases should still work."""
        from novel_forge.core.schemas.outline import SubplotPlan

        data = {
            "subplot_name": "复仇线",
            "description": "复仇线描述",
        }
        result = SubplotPlan.model_validate(data)
        assert result.name == "复仇线"
        assert result.description == "复仇线描述"

    def test_character_profile_aliases(self):
        """CharacterProfile schema-specific aliases should still work."""
        from novel_forge.core.schemas.bible import CharacterProfile

        data = {
            "name": "张三",
            "relationships_2": {"李四": "朋友"},
        }
        result = CharacterProfile.model_validate(data)
        assert "李四" in result.relationships

        data2 = {"name": "李四", "relationship": {"王五": "敌人"}}
        result2 = CharacterProfile.model_validate(data2)
        assert "王五" in result2.relationships


class TestExtraForbidCompatibility:
    """Test that aliases work correctly with extra='forbid'."""

    def test_alias_resolves_before_extra_check(self):
        """Alias should be resolved before extra='forbid' validation."""

        class ExtraForbidSchema(VersionedSchema):
            _correct_fields: set[str] = {"description"}

            description: str = Field(default="")

        data = {"desc": "test value"}
        result = ExtraForbidSchema.model_validate(data)
        assert result.description == "test value"

    def test_multiple_aliases_no_extra_forbid_error(self):
        """Multiple aliases that all resolve to valid fields should not cause extra errors."""

        class MultiFieldSchema(VersionedSchema):
            _correct_fields: set[str] = {"description", "summary", "character_name"}

            description: str = Field(default="")
            summary: str = Field(default="")
            character_name: str = Field(default="")

        data = {
            "desc": "描述",
            "summaryText": "摘要",
            "characterName": "角色名",
        }
        result = MultiFieldSchema.model_validate(data)
        assert result.description == "描述"
        assert result.summary == "摘要"
        assert result.character_name == "角色名"


class TestAliasCount:
    """Verify alias count requirements."""

    def test_at_least_20_base_aliases(self):
        """Task 1.6 requirement: at least 20 base aliases in _SchemaFixMixin."""
        aliases = _SchemaFixMixin._field_aliases
        assert len(aliases) >= 20, (
            f"Task 1.6 requires >=20 LLM field name variants, got {len(aliases)}"
        )

    def test_alias_categories_represented(self):
        """Aliases should cover camelCase, abbreviations, and content variants."""
        aliases = _SchemaFixMixin._field_aliases

        camelcase_aliases = [k for k in aliases if k[0].islower() and any(c.isupper() for c in k)]
        assert len(camelcase_aliases) >= 5, "Need at least 5 camelCase aliases"

        abbrev_aliases = [k for k in aliases if len(k) <= 6 and "_" not in k]
        assert len(abbrev_aliases) >= 3, "Need at least 3 abbreviation aliases"

        content_aliases = ["titleText", "summaryText", "chapterSummary"]
        content_found = sum(1 for a in content_aliases if a in aliases)
        assert content_found >= 2, "Need at least 2 content variant aliases"
