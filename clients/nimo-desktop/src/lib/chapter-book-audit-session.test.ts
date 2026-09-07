import { describe, expect, it } from "vitest";

import {
  buildAuditBookCommand,
  buildAuditBookEditorialCommand,
  defaultChapterAuditParameters,
  validateChapterBookAuditRequest,
} from "./chapter-book-audit-session";

describe("chapter book audit session", () => {
  it("maps every dialog parameter into the engine command", () => {
    expect(buildAuditBookCommand("novel-a", {
      selectedChapters: [2, 4],
      parameters: { ...defaultChapterAuditParameters, promptHint: "优先核对称谓" },
    })).toEqual({
      kind: "audit_book",
      projectId: "novel-a",
      chapterRange: [2, 4],
      analysisMode: "full_text",
      twoPhaseEnabled: true,
      twoPhaseMaxTargetChapters: 4,
      locationStrictness: "balanced",
      maxTokens: 8192,
      temperature: 0.2,
      auditMaxChaptersPerBatch: 4,
      auditMaxIssuesPerChunk: 12,
      auditIssuePoolMaxItems: 160,
      twoPhaseThreshold: 0.7,
      chapterMaxChars: 12000,
      parallelChunks: false,
      parallelDimensions: true,
      promptHint: "优先核对称谓",
    });
  });

  it("builds a publication editorial audit from the shared chapter scope", () => {
    expect(buildAuditBookEditorialCommand("novel-a", {
      selectedChapters: [2, 3],
      parameters: { ...defaultChapterAuditParameters, chaptersPerBatch: 2 },
    })).toEqual({
      kind: "audit_book_editorial",
      projectId: "novel-a",
      chapterRange: [2, 3],
      maxTokens: 8192,
      temperature: 0.2,
      batchSize: 2,
    });
  });

  it("uses the PySide6 empty-list convention for the all-chapters choice", () => {
    expect(validateChapterBookAuditRequest({
      range: "all",
      selectedChapters: new Set([1, 2, 3, 4]),
      parameters: defaultChapterAuditParameters,
      completedChapterCount: 4,
    })).toMatchObject({ kind: "valid", request: { selectedChapters: [] } });
  });

  it("keeps the source minimum-two-chapter validation for custom audits", () => {
    expect(validateChapterBookAuditRequest({
      range: "custom",
      selectedChapters: new Set([4]),
      parameters: defaultChapterAuditParameters,
      completedChapterCount: 4,
    })).toEqual({ kind: "minimum-chapter-count" });
  });

  it("orders a valid custom request without changing its audit settings", () => {
    const result = validateChapterBookAuditRequest({
      range: "custom",
      selectedChapters: new Set([4, 1, 3]),
      parameters: { ...defaultChapterAuditParameters, analysisMode: "summary", promptHint: "优先核对称谓" },
      completedChapterCount: 4,
    });

    expect(result).toEqual({
      kind: "valid",
      request: {
        selectedChapters: [1, 3, 4],
        parameters: { ...defaultChapterAuditParameters, analysisMode: "summary", promptHint: "优先核对称谓" },
      },
    });
  });

  it("normalizes browser number input to the source spin-box bounds", () => {
    const result = validateChapterBookAuditRequest({
      range: "all",
      selectedChapters: new Set([1, 2, 3, 4]),
      parameters: {
        ...defaultChapterAuditParameters,
        targetChapterLimit: 99,
        maxTokens: Number.NaN,
        temperature: 3,
        chaptersPerBatch: 0,
        issuesPerChunk: 80,
        issuePoolLimit: -1,
        twoPhaseThreshold: -0.1,
        chapterMaxChars: 999,
        promptHint: "  只核对人物称谓  ",
      },
      completedChapterCount: 4,
    });

    expect(result).toEqual({
      kind: "valid",
      request: {
        selectedChapters: [],
        parameters: {
          ...defaultChapterAuditParameters,
          targetChapterLimit: 4,
          maxTokens: 512,
          temperature: 2,
          chaptersPerBatch: 1,
          issuesPerChunk: 50,
          issuePoolLimit: 0,
          twoPhaseThreshold: 0,
          chapterMaxChars: 1000,
          promptHint: "只核对人物称谓",
        },
      },
    });
  });
});
