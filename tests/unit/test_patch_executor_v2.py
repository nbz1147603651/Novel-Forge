"""Unit tests for PatchExecutorV2 and supporting components.

Covers:
1. Unique match success
2. Ambiguous match rejection (strict)
3. Ambiguous match pass-through (best_effort)
4. replace_all multi-match
5. Editable window boundary enforcement
6. Stale source hash rejection
7. Patch span overlap conflict detection
8. Transactional rollback (strict: any ambiguous → full batch fail)
9. Matcher chain priority (exact > trimmed > normalized > block_anchor)
10. patches_from_dicts conversion
11. build_issue_windows confidence injection
12. ContinuityIssue backward compat
"""

from __future__ import annotations

import hashlib

from novel_forge.core.patch_engine import (
    FailureCode,
    MatchPolicy,
    PatchExecutorV2,
    PatchOperation,
)
from novel_forge.core.patch_engine.matchers import (
    block_anchor_match,
    exact_match,
    normalized_match,
    run_matcher_chain,
)
from novel_forge.core.schemas.continuity import ContinuityIssue
from novel_forge.core.utils.patch_utils import (
    build_issue_windows,
    patches_from_dicts,
)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


# ─── Matcher tests ────────────────────────────────────────────────────────────


class TestExactMatcher:
    def test_single_match(self):
        r = exact_match("一段文字内容", "文字")
        assert r is not None
        assert r.match_count == 1
        assert r.matcher_name == "exact"

    def test_multi_match(self):
        r = exact_match("重复 重复 重复", "重复")
        assert r is not None
        assert r.match_count == 3

    def test_no_match(self):
        r = exact_match("一段文字", "不存在")
        assert r is None


class TestNormalizedMatcher:
    def test_whitespace_diff(self):
        window = "苏令婉  站在   院中"
        original = "苏令婉 站在 院中"
        r = normalized_match(window, original)
        assert r is not None
        assert r.matcher_name == "normalized"

    def test_punct_diff(self):
        window = "她说\u201c你好\u201d"  # 中文引号
        original = '她说"你好"'          # ASCII引号
        r = normalized_match(window, original)
        assert r is not None


class TestBlockAnchorMatcher:
    def test_head_tail_match(self):
        original = "开头十二个字符的锚点内容，中间部分可能被改写了也许加了几句话，结尾十二个字符的锚点终止"
        window = "开头十二个字符的锚点内容，中间部分被略微改写了也许增加了几句，结尾十二个字符的锚点终止"
        r = block_anchor_match(window, original)
        assert r is not None
        assert r.matcher_name == "block_anchor"

    def test_too_short(self):
        r = block_anchor_match("短文", "短文")
        assert r is None  # below min_anchor * 3


class TestMatcherChain:
    def test_exact_takes_priority(self):
        r = run_matcher_chain("精确匹配内容", "精确匹配内容")
        assert r is not None
        assert r.matcher_name == "exact"

    def test_fallback_to_normalized(self):
        r = run_matcher_chain("苏令婉  站在了   院中", "苏令婉 站在了 院中")
        assert r is not None
        assert r.matcher_name in ("exact", "normalized", "trimmed_line")


# ─── Executor tests ──────────────────────────────────────────────────────────


class TestPatchExecutorV2:
    """Core executor tests."""

    TEXT = "第一段内容。\n\n第二段有错误描述。\n\n第三段正确内容。"

    def test_unique_match_success(self):
        executor = PatchExecutorV2(strategy="best_effort")
        patches = [
            PatchOperation(patch_id="p1", original="错误描述", replacement="正确描述",
                           para_start=1, para_end=1, editable_para_start=1, editable_para_end=1),
        ]
        r = executor.apply_batch(self.TEXT, patches)
        assert r.applied_count == 1
        assert r.attempted_count == 1
        assert "正确描述" in r.revised_text
        assert not r.has_failures

    def test_ambiguous_match_strict_rejects_batch(self):
        text = "重复词 重复词 在此段\n\n另一段内容"
        executor = PatchExecutorV2(strategy="strict")
        patches = [PatchOperation(patch_id="p1", original="重复词")]
        r = executor.apply_batch(text, patches)
        assert r.applied_count == 0
        assert r.fallback is True
        assert FailureCode.AMBIGUOUS_MATCH in r.failure_codes()

    def test_ambiguous_match_best_effort_skips(self):
        text = "重复词 重复词 在此段\n\n另一段内容"
        executor = PatchExecutorV2(strategy="best_effort")
        patches = [
            PatchOperation(patch_id="p1", original="重复词"),
            PatchOperation(patch_id="p2", original="另一段", replacement="新一段"),
        ]
        r = executor.apply_batch(text, patches)
        # p1 fails (ambiguous), p2 succeeds
        assert r.applied_count == 1
        assert r.ambiguous_match_count == 1
        assert "新一段" in r.revised_text

    def test_replace_all_allows_multi(self):
        text = "坏词出现 坏词再次出现"
        executor = PatchExecutorV2(strategy="best_effort")
        patches = [
            PatchOperation(
                patch_id="p1", original="坏词", replacement="好词",
                replace_all=True, match_policy=MatchPolicy.ALLOW_MULTI,
            ),
        ]
        r = executor.apply_batch(text, patches)
        # replace_all + ALLOW_MULTI should not flag ambiguity
        # (executor replaces first occurrence via commit; replace_all is advisory)
        assert r.applied_count == 1  # commit applies once
        assert not r.has_failures

    def test_stale_source_hash_rejects_all(self):
        executor = PatchExecutorV2(strategy="best_effort")
        patches = [PatchOperation(patch_id="p1", original="错误描述", replacement="修复")]
        r = executor.apply_batch(self.TEXT, patches, source_text_hash="wrong_hash")
        assert r.applied_count == 0
        assert r.stale_reject_count == 1
        assert r.fallback is True

    def test_correct_source_hash_passes(self):
        executor = PatchExecutorV2(strategy="best_effort")
        patches = [
            PatchOperation(patch_id="p1", original="错误描述", replacement="正确描述",
                           para_start=1, para_end=1, editable_para_start=1, editable_para_end=1),
        ]
        h = _sha256(self.TEXT)
        r = executor.apply_batch(self.TEXT, patches, source_text_hash=h)
        assert r.applied_count == 1

    def test_out_of_window(self):
        text = "段A\n\n段B\n\n段C"
        executor = PatchExecutorV2(strategy="best_effort")
        # Original is in para 0 ("段A") but editable window is para 2 ("段C")
        patches = [
            PatchOperation(patch_id="p1", original="段A", replacement="新A",
                           para_start=2, para_end=2, editable_para_start=2, editable_para_end=2),
        ]
        r = executor.apply_batch(text, patches)
        assert r.applied_count == 0
        assert FailureCode.NOT_FOUND in r.failure_codes()

    def test_overlap_conflict_detection(self):
        text = "这是一段完整的较长文字内容用于测试重叠。"
        executor = PatchExecutorV2(strategy="best_effort")
        # Two patches targeting overlapping regions
        patches = [
            PatchOperation(patch_id="p1", original="完整的较长文字", replacement="修改一"),
            PatchOperation(patch_id="p2", original="较长文字内容", replacement="修改二"),
        ]
        r = executor.apply_batch(text, patches)
        # At least one should be rejected as overlap
        overlap_failures = [f for f in r.failed_items if f.failure_code == FailureCode.OVERLAP_CONFLICT]
        assert len(overlap_failures) >= 1

    def test_empty_patches_noop(self):
        executor = PatchExecutorV2()
        r = executor.apply_batch("text", [])
        assert r.applied_count == 0
        assert r.attempted_count == 0
        assert r.revised_text == "text"

    def test_not_found_failure(self):
        executor = PatchExecutorV2(strategy="best_effort")
        patches = [PatchOperation(patch_id="p1", original="根本不存在的文字", replacement="修复")]
        r = executor.apply_batch(self.TEXT, patches)
        assert r.applied_count == 0
        assert FailureCode.NOT_FOUND in r.failure_codes()

    def test_strict_batch_rollback_on_ambiguity(self):
        """In strict mode, if any patch has ambiguous match, the entire batch is rejected."""
        text = "好词 好词 坏词在这里"
        executor = PatchExecutorV2(strategy="strict")
        patches = [
            PatchOperation(patch_id="p1", original="好词"),           # ambiguous
            PatchOperation(patch_id="p2", original="坏词", replacement="新词"),  # would succeed
        ]
        r = executor.apply_batch(text, patches)
        assert r.applied_count == 0
        assert r.fallback is True
        assert "坏词" in r.revised_text  # original text unchanged


# ─── Utility tests ────────────────────────────────────────────────────────────


class TestPatchesFromDicts:
    def test_basic_conversion(self):
        raw = [
            {"original": "错误", "replacement": "正确", "para_start": 0, "para_end": 2},
            {"original": "", "replacement": "skip"},  # empty → filtered
            {"original": "另一个", "replacement": "修复"},
        ]
        ops = patches_from_dicts(raw)
        assert len(ops) == 2
        assert ops[0].original == "错误"
        assert ops[0].para_start == 0
        assert ops[1].para_start is None

    def test_enrichment_from_issue_windows(self):
        iw = [{"window_text": "文字内容错误描述后续这个窗口文本比较长以确保前四十字匹配生效", "para_start": 3, "para_end": 5, 
               "editable_para_start": 3, "editable_para_end": 5}]
        raw = [{"original": "文字内容错误描述后续这个窗口文本比较长以确保前四十字", "replacement": "修复"}]
        ops = patches_from_dicts(raw, iw)
        assert ops[0].para_start == 3


class TestBuildIssueWindowsConfidence:
    def test_evidence_match_confidence(self):
        paragraphs = ["苏令婉站在院中，", "因小阁的门紧闭着。", "远处传来钟声。"]

        class MockIssue:
            evidence = "苏令婉站在院中"
            location = ""
            severity = "high"
            summary = ""

        windows = build_issue_windows(paragraphs, [MockIssue()])
        assert windows[0]["anchor_type"] == "evidence_exact"
        assert windows[0]["location_confidence"] >= 0.9

    def test_explicit_para_confidence(self):
        paragraphs = ["段一", "段二", "段三"]

        class MockIssue:
            evidence = ""
            location = "第2段"
            severity = "high"
            summary = ""

        windows = build_issue_windows(paragraphs, [MockIssue()])
        assert windows[0]["anchor_type"] == "location_parsed"
        assert windows[0]["location_confidence"] >= 0.8

    def test_no_info_low_confidence(self):
        paragraphs = ["段一", "段二"]

        class MockIssue:
            evidence = ""
            location = ""
            severity = "high"
            summary = ""

        windows = build_issue_windows(paragraphs, [MockIssue()])
        assert windows[0]["anchor_type"] == "fallback"
        assert windows[0]["location_confidence"] <= 0.2

    def test_schema_paragraph_anchor_takes_precedence_over_misleading_evidence(self):
        paragraphs = ["第一段有误导证据", "第二段才是目标", "第三段"]

        class MockIssue:
            evidence = "第一段有误导证据"
            evidence_quote = "第一段有误导证据"
            location = ""
            paragraph_start = 2
            paragraph_end = 2
            location_confidence = 0.93
            anchor_type = "explicit_para"
            severity = "high"
            summary = ""

        windows = build_issue_windows(paragraphs, [MockIssue()])

        assert windows[0]["target_indices"] == [1]
        assert windows[0]["editable_para_start"] == 1
        assert windows[0]["editable_para_end"] == 1
        assert windows[0]["anchor_type"] == "explicit_para"
        assert windows[0]["location_confidence"] == 0.93


class TestContinuityIssueBackwardCompat:
    def test_old_data_parses(self):
        issue = ContinuityIssue(issue_type="opening_gap", severity="high")
        assert issue.location == ""
        assert issue.location_confidence == 0.0
        assert issue.anchor_type == ""

    def test_new_fields(self):
        issue = ContinuityIssue(
            issue_type="opening_gap",
            location="开头2段",
            location_confidence=0.85,
            anchor_type="explicit_para",
        )
        assert issue.location == "开头2段"
        assert issue.location_confidence == 0.85
