import { expect, test } from "@playwright/test";

const source = "雨线落在旧窗上。沈昭停住脚步，听见钟声。";
const candidate = "雨线落在旧窗上。沈昭在门槛前骤然停步，听见钟声。";

function caseDetail(stage: "verified" | "pending" | "approved" | "published") {
  const published = stage === "published";
  const proposalId = stage === "verified" ? "" : "1234567890abcdef1234567890abcdef";
  return {
    case: {
      case_id: "case-1",
      project_id: "book",
      content_type: "chapter_text",
      source: "author_annotation",
      artifact_id: "chapter:1:final",
      source_version: "chapter:1:final:source",
      source_hash: "source-hash",
      authority: "proposal_required",
      input_version: "input-v1",
      policy_version: 2,
      status: published ? "published" : "awaiting_approval",
      version: stage === "verified" ? 5 : stage === "pending" ? 6 : stage === "approved" ? 6 : 8,
      event_seq: 8,
      title: "动作需要更明确",
      chapter_numbers: [1],
      issues: [],
      targets: [],
      candidates: [{
        case_id: "case-1",
        version: 1,
        base_hash: "source-hash",
        candidate_hash: "candidate-hash",
        blob_hash: "blob-hash",
        origin: "human_edit",
        patch_count: 1,
        change_ratio: 0.08,
        patches: [],
        protected_items: ["未圈选正文", "作者锁定内容"],
        metadata: {},
      }],
      verification: {
        case_id: "case-1",
        candidate_version: 1,
        candidate_hash: "candidate-hash",
        passed: true,
        resolved_issue_ids: ["issue-1"],
        residual_issue_ids: [],
        regression_issue_ids: [],
        validators: [{
          validator_id: "author_exact_selection_v1",
          passed: true,
          required: true,
          details: ["仅圈选字符区间被替换"],
          evidence: {},
        }],
        details: [],
        metadata: {},
      },
      receipt: published ? {
        receipt_id: "receipt-1",
        case_id: "case-1",
        candidate_version: 1,
        authority: "proposal_required",
        target: "chapter:1:final",
        before_hash: "source-hash",
        after_hash: "candidate-hash",
        input_version: "input-v1",
        policy_version: 2,
        approval_id: "approval-1",
        proposal_id: proposalId,
        transaction_status: "committed",
        committed: true,
        recovered: false,
        message: "已发布",
        metadata: {},
      } : null,
      proposal_id: proposalId,
      failure_reason: "",
      metadata: {},
    },
    events: [],
    source_payload: source,
    candidate_payload: candidate,
    capabilities: {
      annotate: true,
      prepare: false,
      edit: !published,
      verify: false,
      request_approval: stage === "verified",
      reject_or_defer: !published,
      publish: stage === "approved",
      recover: false,
      reason: stage === "verified"
        ? "请创建版本化正文修订提案"
        : stage === "pending"
          ? "请在现有共创提案中核对差异并批准"
          : stage === "approved"
            ? "提案已精确批准"
            : "已发布",
    },
  };
}

function proposal(stage: "pending" | "approved" | "published") {
  return {
    id: "1234567890abcdef1234567890abcdef",
    project_id: "book",
    action: "revise",
    chapter_number: 1,
    policy_version: 2,
    input_version: "input-v1",
    candidate_version: "candidate-hash",
    title: "修复工作台：动作需要更明确",
    original: source,
    candidate,
    evidence: ["repair_case:case-1"],
    affected_chapters: [1],
    risks: [],
    lock_conflicts: [],
    cost_hint: "不调用模型",
    status: stage === "published" ? "applied" : stage,
    application_result: { approval_flow: "repair_workbench" },
  };
}

test("修复工作台将提案、批准和发布分开，且保留未保存草稿", async ({ page }) => {
  let stage: "verified" | "pending" | "approved" | "published" = "verified";
  let approvalBody: Record<string, unknown> | null = null;
  let decisionBody: Record<string, unknown> | null = null;
  let publishBody: Record<string, unknown> | null = null;
  await page.route("http://127.0.0.1:18791/**", async (route) => {
    const request = route.request();
    const headers = {
      "access-control-allow-origin": "*",
      "access-control-allow-headers": "content-type",
      "access-control-allow-methods": "GET,POST,OPTIONS",
      "content-type": "application/json",
    };
    if (request.method() === "OPTIONS") {
      await route.fulfill({ status: 204, headers });
      return;
    }
    const url = new URL(request.url());
    if (url.pathname.endsWith("/repairs/source")) {
      await route.fulfill({ headers, json: {
        project_id: "book", content_type: "chapter_text", artifact_id: "chapter:1:final",
        chapter_number: 1, source_version: "chapter:1:final:source", source_hash: "source-hash",
        content: source, state: "official",
      } });
      return;
    }
    if (url.pathname.endsWith("/repairs") && request.method() === "GET") {
      await route.fulfill({ headers, json: [caseDetail(stage).case] });
      return;
    }
    if (url.pathname.endsWith("/proposals") && request.method() === "GET") {
      await route.fulfill({ headers, json: stage === "verified" ? [] : [proposal(stage)] });
      return;
    }
    if (url.pathname.endsWith("/decision")) {
      decisionBody = request.postDataJSON() as Record<string, unknown>;
      stage = "approved";
      await route.fulfill({ headers, json: proposal(stage) });
      return;
    }
    if (url.pathname.endsWith("/approval")) {
      approvalBody = request.postDataJSON() as Record<string, unknown>;
      stage = "pending";
      await route.fulfill({ headers, json: caseDetail(stage) });
      return;
    }
    if (url.pathname.endsWith("/publish")) {
      publishBody = request.postDataJSON() as Record<string, unknown>;
      stage = "published";
      await route.fulfill({ headers, json: caseDetail(stage) });
      return;
    }
    await route.fulfill({ headers, json: caseDetail(stage) });
  });

  await page.goto("/tests/browser/repair.html");
  const draft = page.getByRole("textbox", { name: "章台未保存草稿" });
  await draft.fill("作者尚未保存的新段落");
  const trigger = page.getByRole("button", { name: "打开修复工作台" });
  await trigger.click();
  await expect(page.getByRole("heading", { name: "统一修复工作台" })).toBeVisible();
  await expect(page.getByText("分级发布 · 正文需批准")).toBeVisible();

  await page.getByRole("tab", { name: /验证与发布/ }).click();
  await page.getByRole("button", { name: "创建修订提案" }).click();
  await expect(page.getByText("版本化修订提案已创建")).toBeVisible();
  expect(approvalBody).toMatchObject({ case_version: 5, candidate_version: 1 });
  await expect(page.getByText("修复工作台：动作需要更明确")).toBeVisible();
  const approve = page.getByRole("button", { name: "批准此候选（暂不改正文）" });
  await expect(approve).toBeDisabled();
  await page.getByRole("checkbox", { name: "我已核对候选、影响章节和锁定冲突，只批准当前版本" }).check();
  await approve.click();
  await expect(page.getByText("候选已精确批准")).toBeVisible();
  expect(decisionBody).toMatchObject({
    decision: "accept",
    candidate_version: "candidate-hash",
    input_version: "input-v1",
    policy_version: 2,
  });
  await expect(page.getByRole("button", { name: "应用已批准提案" })).toBeEnabled();
  await page.getByRole("button", { name: "应用已批准提案" }).click();
  await expect(page.getByText("发布回执已保存")).toBeVisible();
  expect(publishBody).toMatchObject({ case_version: 6, candidate_version: 1, authority_version: 2 });

  await page.keyboard.press("Escape");
  await expect(page.getByRole("heading", { name: "统一修复工作台" })).toHaveCount(0);
  await expect(draft).toHaveValue("作者尚未保存的新段落");
  await expect(trigger).toBeFocused();
});

test("修复工作台在窄窗口内不产生页面级横向溢出", async ({ page }) => {
  await page.setViewportSize({ width: 480, height: 820 });
  await page.route("http://127.0.0.1:18791/**", async (route) => {
    const request = route.request();
    const headers = { "access-control-allow-origin": "*", "content-type": "application/json" };
    if (request.method() === "OPTIONS") {
      await route.fulfill({ status: 204, headers: { ...headers, "access-control-allow-headers": "content-type" } });
    } else if (request.url().includes("/repairs/source")) {
      await route.fulfill({ headers, json: {
        project_id: "book", content_type: "chapter_text", artifact_id: "chapter:1:final",
        chapter_number: 1, source_version: "v1", source_hash: "source-hash", content: source,
        state: "official",
      } });
    } else if (request.url().includes("/repairs?") || request.url().endsWith("/repairs")) {
      await route.fulfill({ headers, json: [caseDetail("verified").case] });
    } else {
      await route.fulfill({ headers, json: caseDetail("verified") });
    }
  });
  await page.goto("/tests/browser/repair.html");
  await page.getByRole("button", { name: "打开修复工作台" }).click();
  await expect(page.getByRole("heading", { name: "统一修复工作台" })).toBeVisible();
  await expect.poll(() => page.evaluate(() => (
    document.documentElement.scrollWidth - document.documentElement.clientWidth
  ))).toBe(0);
});
