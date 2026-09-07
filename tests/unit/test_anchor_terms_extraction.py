"""Unit tests for anchor_terms extraction."""


from novel_forge.pipeline.long.services.anchor_terms import (
    _extract_anchor_terms_from_bible,
)


def test_empty_inputs():
    assert _extract_anchor_terms_from_bible(None, None) == []
    assert _extract_anchor_terms_from_bible({}, {}) == []
    assert _extract_anchor_terms_from_bible(
        {"characters": []}, {"characters": {}}
    ) == []


def test_bible_relationship_descriptions():
    bible = {
        "characters": [
            {"name": "Alice", "relationships": {"Bob": "父亲", "Charlie": "宿敌"}},
            {"name": "Bob", "relationships": {"Dave": "上司"}},
        ]
    }
    result = _extract_anchor_terms_from_bible(bible, None)
    assert "父亲" in result
    assert "宿敌" in result
    assert "上司" in result
    assert result.count("父亲") == 1


def test_canon_social_status():
    canon = {
        "characters": {
            "Alice": {"social_status": "六品翰林"},
            "Bob": {"social_status": "镇北将军"},
        }
    }
    result = _extract_anchor_terms_from_bible(None, canon)
    assert "六品翰林" in result
    assert "镇北将军" in result


def test_canon_relationship_public_status():
    canon = {
        "relationships": {
            "alice_bob": {"public_status": "宿敌，暗中较劲"},
            "alice_charlie": {"public_status": "君臣"},
        }
    }
    result = _extract_anchor_terms_from_bible(None, canon)
    assert "宿敌" in result
    assert "暗中较劲" in result
    assert "君臣" in result


def test_active_relationships_list():
    canon = {
        "active_relationships": [
            {"public_status": "师徒，亦敌亦友"},
        ]
    }
    result = _extract_anchor_terms_from_bible(None, canon)
    assert "师徒" in result
    assert "亦敌亦友" in result


def test_mixed_sources():
    bible = {
        "characters": [
            {"relationships": {"Bob": "师兄"}},
        ]
    }
    canon = {
        "characters": {
            "Alice": {"social_status": "掌门"},
        },
        "relationships": {
            "ab": {"public_status": "师兄妹"},
        },
    }
    result = _extract_anchor_terms_from_bible(bible, canon)
    assert "师兄" in result
    assert "掌门" in result
    assert "师兄妹" in result


def test_object_like_input():
    class FakeRel:
        public_status = "同盟"

    class FakeChar:
        social_status = "将军"

    class FakeCanon:
        characters = {"A": FakeChar()}
        relationships = {"ab": FakeRel()}

    class FakeBibleChar:
        relationships = {"B": "盟友"}

    class FakeBible:
        characters = [FakeBibleChar()]

    result = _extract_anchor_terms_from_bible(FakeBible(), FakeCanon())
    assert "盟友" in result
    assert "将军" in result
    assert "同盟" in result


def test_no_duplicate_terms():
    bible = {
        "characters": [
            {"relationships": {"A": "父亲"}},
            {"relationships": {"B": "父亲"}},
        ]
    }
    result = _extract_anchor_terms_from_bible(bible, None)
    assert result.count("父亲") == 1


def test_empty_status_skipped():
    canon = {
        "characters": {
            "Alice": {"social_status": ""},
            "Bob": {"social_status": "  "},
        }
    }
    result = _extract_anchor_terms_from_bible(None, canon)
    assert result == []


def test_single_char_public_status_ignored():
    canon = {
        "relationships": {
            "ab": {"public_status": "A，BC"},
        }
    }
    result = _extract_anchor_terms_from_bible(None, canon)
    assert "A" not in result
    assert "BC" in result
