"""测试枚举值校验函数。

覆盖 validate_hook_type, validate_hook_strength, validate_pacing_mode。
"""

import pytest

from novel_forge.core.schemas.style_profile import (
    validate_hook_strength,
    validate_hook_type,
    validate_pacing_mode,
)


class TestValidateHookType:
    """测试 validate_hook_type 函数。"""

    # 有效值测试
    @pytest.mark.parametrize("value", ["crisis", "mystery", "emotion", "choice", "desire", "none"])
    def test_valid_values(self, value: str) -> None:
        """有效值应原样返回（小写）。"""
        assert validate_hook_type(value) == value

    # 无效值测试
    @pytest.mark.parametrize("value", ["cliffhanger", "suspense", "unknown", "invalid", ""])
    def test_invalid_values_return_none(self, value: str) -> None:
        """无效值应返回默认值 "none"。"""
        assert validate_hook_type(value) == "none"

    # 大小写不敏感测试
    @pytest.mark.parametrize(
        "input_value,expected",
        [
            ("CRISIS", "crisis"),
            ("Mystery", "mystery"),
            ("EMOTION", "emotion"),
            ("Choice", "choice"),
            ("DESIRE", "desire"),
            ("NONE", "none"),
            ("CrIsIs", "crisis"),
        ],
    )
    def test_case_insensitive(self, input_value: str, expected: str) -> None:
        """大小写不敏感，应正确解析为小写有效值。"""
        assert validate_hook_type(input_value) == expected

    # 空白处理测试
    @pytest.mark.parametrize(
        "input_value,expected",
        [
            ("  crisis  ", "crisis"),
            ("\tmystery\t", "mystery"),
            (" emotion ", "emotion"),
            ("  ", "none"),
            ("\n", "none"),
        ],
    )
    def test_whitespace_handling(self, input_value: str, expected: str) -> None:
        """前后空白应被 strip()，空字符串返回 "none"。"""
        assert validate_hook_type(input_value) == expected


class TestValidateHookStrength:
    """测试 validate_hook_strength 函数。"""

    # 有效值测试
    @pytest.mark.parametrize("value", ["strong", "medium", "weak"])
    def test_valid_values(self, value: str) -> None:
        """有效值应原样返回（小写）。"""
        assert validate_hook_strength(value) == value

    # 无效值测试
    @pytest.mark.parametrize("value", ["high", "low", "extreme", "invalid", ""])
    def test_invalid_values_return_medium(self, value: str) -> None:
        """无效值应返回默认值 "medium"。"""
        assert validate_hook_strength(value) == "medium"

    # 大小写不敏感测试
    @pytest.mark.parametrize(
        "input_value,expected",
        [
            ("STRONG", "strong"),
            ("Medium", "medium"),
            ("WEAK", "weak"),
            ("StRoNg", "strong"),
        ],
    )
    def test_case_insensitive(self, input_value: str, expected: str) -> None:
        """大小写不敏感，应正确解析为小写有效值。"""
        assert validate_hook_strength(input_value) == expected

    # 空白处理测试
    @pytest.mark.parametrize(
        "input_value,expected",
        [
            ("  strong  ", "strong"),
            ("\tmedium\t", "medium"),
            (" weak ", "weak"),
            ("  ", "medium"),
            ("\n", "medium"),
        ],
    )
    def test_whitespace_handling(self, input_value: str, expected: str) -> None:
        """前后空白应被 strip()，空字符串返回 "medium"。"""
        assert validate_hook_strength(input_value) == expected


class TestValidatePacingMode:
    """测试 validate_pacing_mode 函数。"""

    # 有效值测试
    @pytest.mark.parametrize("value", ["fast", "moderate", "slow"])
    def test_valid_values(self, value: str) -> None:
        """有效值应原样返回（小写）。"""
        assert validate_pacing_mode(value) == value

    # 无效值测试
    @pytest.mark.parametrize("value", ["quick", "normal", "medium", "invalid", ""])
    def test_invalid_values_return_moderate(self, value: str) -> None:
        """无效值应返回默认值 "moderate"。"""
        assert validate_pacing_mode(value) == "moderate"

    # 大小写不敏感测试
    @pytest.mark.parametrize(
        "input_value,expected",
        [
            ("FAST", "fast"),
            ("Moderate", "moderate"),
            ("SLOW", "slow"),
            ("FaSt", "fast"),
        ],
    )
    def test_case_insensitive(self, input_value: str, expected: str) -> None:
        """大小写不敏感，应正确解析为小写有效值。"""
        assert validate_pacing_mode(input_value) == expected

    # 空白处理测试
    @pytest.mark.parametrize(
        "input_value,expected",
        [
            ("  fast  ", "fast"),
            ("\tmoderate\t", "moderate"),
            (" slow ", "slow"),
            ("  ", "moderate"),
            ("\n", "moderate"),
        ],
    )
    def test_whitespace_handling(self, input_value: str, expected: str) -> None:
        """前后空白应被 strip()，空字符串返回 "moderate"。"""
        assert validate_pacing_mode(input_value) == expected


class TestEdgeCases:
    """边界值和特殊情况测试。"""

    def test_hook_type_none_string(self) -> None:
        """字符串 "none" 是有效值，应返回 "none"。"""
        assert validate_hook_type("none") == "none"

    def test_hook_type_none_uppercase(self) -> None:
        """大写 "NONE" 应返回小写 "none"。"""
        assert validate_hook_type("NONE") == "none"

    def test_hook_strength_medium_is_valid(self) -> None:
        """字符串 "medium" 是有效值，应返回 "medium"。"""
        assert validate_hook_strength("medium") == "medium"

    def test_pacing_mode_moderate_is_valid(self) -> None:
        """字符串 "moderate" 是有效值，应返回 "moderate"。"""
        assert validate_pacing_mode("moderate") == "moderate"

    def test_pacing_mode_medium_is_invalid(self) -> None:
        """字符串 "medium" 对 pacing 是无效值，应返回 "moderate"。"""
        # 注意：medium 是 hook_strength 的有效值，但不是 pacing_mode 的有效值
        assert validate_pacing_mode("medium") == "moderate"

    def test_hook_strength_high_is_invalid(self) -> None:
        """字符串 "high" 是无效值，应返回 "medium"。"""
        assert validate_hook_strength("high") == "medium"

    def test_hook_type_with_unicode_whitespace(self) -> None:
        """Unicode 空白字符应被 strip() 处理。"""
        # Python strip() 处理 Unicode 空白字符（包括 EM SPACE）
        assert validate_hook_type("\u2003crisis\u2003") == "crisis"
