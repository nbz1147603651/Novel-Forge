from __future__ import annotations

from novel_forge.core.domain import language as lang


def test_language_aliases_and_descriptions() -> None:
    assert lang.is_simplified_chinese_language("zh")
    assert lang.is_simplified_chinese_language("zh_Hans")
    assert not lang.is_simplified_chinese_language("zh-Hant")
    assert lang.is_traditional_chinese_language("zh-TW")
    assert lang.describe_language("zh") == "简体中文（zh-Hans）"
    assert lang.describe_language("zh-hk") == "繁体中文（zh-Hant）"
    assert lang.describe_language("en-US") == "English"
    assert lang.describe_language("fr") == "fr"


def test_language_output_rule_is_hard_for_supported_languages() -> None:
    assert "简体中文" in lang.language_output_rule("zh")
    assert "禁止输出繁体字" in lang.language_output_rule("zh")
    assert "繁体中文" in lang.language_output_rule("zh-Hant")
    assert lang.language_output_rule("en") == "Output language: English."
    assert lang.language_output_rule("fr") == ""


def test_contains_traditional_chinese_uses_marker_heuristic() -> None:
    assert lang.contains_traditional_chinese("這個鐘聲會響")
    assert not lang.contains_traditional_chinese("这个钟声会响")


def test_simplified_normalization_uses_fallback_when_opencc_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(lang, "_opencc_converter", lambda _config: None)

    result = lang.normalize_text_for_language(
        "陸雲崢與沈念卿會重逢，心底尖叫著，懷錶開始轉動。",
        "zh",
    )

    assert result == "陆云峥与沈念卿会重逢，心底尖叫着，怀表开始转动。"


def test_simplified_normalization_uses_opencc_when_available(monkeypatch) -> None:
    class FakeConverter:
        def convert(self, value: str) -> str:
            assert value == "傳統文本"
            return "传统文本"

    monkeypatch.setattr(lang, "_opencc_converter", lambda config: FakeConverter() if config == "t2s" else None)

    assert lang.normalize_text_for_language("傳統文本", "zh-cn") == "传统文本"


def test_traditional_normalization_uses_opencc_when_available(monkeypatch) -> None:
    class FakeConverter:
        def convert(self, value: str) -> str:
            assert value == "简体文本"
            return "簡體文本"

    monkeypatch.setattr(lang, "_opencc_converter", lambda config: FakeConverter() if config == "s2t" else None)

    assert lang.normalize_text_for_language("简体文本", "zh-Hant") == "簡體文本"


def test_normalize_payload_for_language_recurses_json_like_payload(monkeypatch) -> None:
    monkeypatch.setattr(lang, "_opencc_converter", lambda _config: None)
    payload = {
        "title": "鐘聲",
        "items": ["懷錶", {"note": "寫著祕密"}],
        "tuple_value": ("這裡", 3),
        "count": 2,
    }

    assert lang.normalize_payload_for_language(payload, "zh") == {
        "title": "钟声",
        "items": ["怀表", {"note": "写着秘密"}],
        "tuple_value": ("这里", 3),
        "count": 2,
    }


def test_non_chinese_languages_leave_text_unchanged(monkeypatch) -> None:
    monkeypatch.setattr(lang, "_opencc_converter", lambda _config: None)

    assert lang.normalize_text_for_language("這個詞保持原樣", "en") == "這個詞保持原樣"
