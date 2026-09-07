import { afterEach, describe, expect, it, vi } from "vitest";

import { LegacyLocalEngineClient } from "./legacy-engine-client";

describe("LegacyLocalEngineClient", () => {
  it("binds proposal decisions to candidate, story input and policy versions", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ id: "proposal", status: "approved", candidate_version: "c1" }) });
    vi.stubGlobal("fetch", fetchMock);
    const client = new LegacyLocalEngineClient({ baseUrl: "http://127.0.0.1:8900" });
    await client.decideAuthoringProposal("book", "proposal", { decision: "accept", candidateVersion: "c1", inputVersion: "i1", policyVersion: 2 });
    expect(JSON.parse(fetchMock.mock.calls[0]?.[1].body)).toEqual({ decision: "accept", candidate_version: "c1", input_version: "i1", policy_version: 2 });
    expect(fetchMock.mock.calls[0]?.[0]).toContain("/proposals/proposal/decision");
  });
  it("retries only the durable planning batch, without an outline extension command", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ status: "accepted", task_id: "horizon-1", message: "已复用" }) });
    vi.stubGlobal("fetch", fetchMock);
    const client = new LegacyLocalEngineClient({ baseUrl: "http://127.0.0.1:8900" });
    expect((await client.retryPlanningHorizon("book", 3)).taskId).toBe("horizon-1");
    expect(fetchMock.mock.calls[0]?.[0]).toContain("/authoring/book/planning/retry");
    expect(JSON.parse(fetchMock.mock.calls[0]?.[1].body)).toEqual({ current_chapter: 3 });
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("reads backend authoring authority without inferring permission from UI mode", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        project_id: "book", configured: true,
        policy: { version: 2, mode: "coauthor", start_chapter: 1, end_chapter: 5, stopped: false },
        input_version: "input-a",
        allowed_actions: [{ action: "archive", allowed: false, requires_approval: true, reason: "请验收" }],
        stop_state: "idle",
      }),
    });
    vi.stubGlobal("fetch", fetchMock);
    const client = new LegacyLocalEngineClient({ baseUrl: "http://127.0.0.1:8900" });
    const view = await client.getAuthoringSession("book", 3);
    expect(view.policy.endChapter).toBe(5);
    expect(view.allowedActions[0]).toMatchObject({ allowed: false, requiresApproval: true });
    expect(fetchMock.mock.calls[0]?.[0]).toBe("http://127.0.0.1:8900/api/v1/engine/authoring/book?chapter=3");
  });

  it("maps path-free repair evidence and exact candidate commands", async () => {
    const detail = {
      case: {
        case_id: "case-1",
        project_id: "book",
        content_type: "chapter_text",
        source: "author_annotation",
        artifact_id: "chapter:2:review",
        source_hash: "hash-a",
        status: "located",
        version: 2,
        candidates: [],
      },
      events: [],
      source_payload: "原文",
      candidate_payload: null,
      capabilities: { annotate: true, edit: true, verify: false, publish: false },
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          project_id: "book",
          content_type: "chapter_text",
          artifact_id: "chapter:2:review",
          chapter_number: 2,
          source_version: "chapter:2:review:hash-a",
          source_hash: "hash-a",
          content: "原文",
          state: "working_candidate",
        }),
      })
      .mockResolvedValueOnce({ ok: true, json: async () => detail })
      .mockResolvedValueOnce({ ok: true, json: async () => detail })
      .mockResolvedValueOnce({ ok: true, json: async () => detail })
      .mockResolvedValueOnce({ ok: true, json: async () => detail })
      .mockResolvedValueOnce({ ok: true, json: async () => detail });
    vi.stubGlobal("fetch", fetchMock);
    const client = new LegacyLocalEngineClient({ baseUrl: "http://127.0.0.1:8900" });

    const source = await client.getRepairSource("book", 2);
    const annotated = await client.createRepairAnnotation("book", {
      chapterNumber: 2,
      artifactId: source.artifactId,
      sourceHash: source.sourceHash,
      charStart: 0,
      charEnd: 2,
      selectedText: "原文",
      summary: "人工问题",
      severity: "medium",
    });
    await client.saveRepairCandidate("book", annotated.case.caseId, {
      caseVersion: 2,
      candidateVersion: 0,
      replacementText: "候选",
    });
    await client.requestRepairApproval("book", annotated.case.caseId, {
      caseVersion: 5,
      candidateVersion: 1,
    });
    await client.publishRepairCase("book", annotated.case.caseId, {
      caseVersion: 6,
      candidateVersion: 1,
      authorityVersion: 2,
    });
    await client.recoverRepairReceipt("book", annotated.case.caseId, {
      caseVersion: 7,
      receiptId: "receipt-1",
    });

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "http://127.0.0.1:8900/api/v1/engine/authoring/book/repairs/source?chapter=2",
    );
    expect(annotated.sourcePayload).toBe("原文");
    expect(annotated.capabilities.publish).toBe(false);
    expect(JSON.parse(String((fetchMock.mock.calls[1]?.[1] as RequestInit).body))).toEqual({
      chapter_number: 2,
      artifact_id: "chapter:2:review",
      source_hash: "hash-a",
      char_start: 0,
      char_end: 2,
      selected_text: "原文",
      summary: "人工问题",
      severity: "medium",
    });
    expect(JSON.parse(String((fetchMock.mock.calls[2]?.[1] as RequestInit).body))).toEqual({
      case_version: 2,
      candidate_version: 0,
      replacement_text: "候选",
    });
    expect(fetchMock.mock.calls[3]?.[0]).toContain("/repairs/case-1/approval");
    expect(JSON.parse(String((fetchMock.mock.calls[3]?.[1] as RequestInit).body))).toEqual({
      case_version: 5,
      candidate_version: 1,
    });
    expect(JSON.parse(String((fetchMock.mock.calls[4]?.[1] as RequestInit).body))).toEqual({
      case_version: 6,
      candidate_version: 1,
      authority_version: 2,
    });
    expect(JSON.parse(String((fetchMock.mock.calls[5]?.[1] as RequestInit).body))).toEqual({
      case_version: 7,
      receipt_id: "receipt-1",
    });
  });

  it("serializes typed settings commands into the Python API's snake_case contract", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        status: "saved",
        persistence: "persisted",
        message: "已保存",
        accepted_route_ids: ["draft_chapter"],
        rejected_routes: [],
        saved_at_label: "12:00:00",
      }),
    });
    vi.stubGlobal("fetch", fetchMock);
    const client = new LegacyLocalEngineClient({ baseUrl: "http://127.0.0.1:8900" });

    const result = await client.saveSettings({
      kind: "save_settings",
      defaultProfileId: "ollama:local",
      creativeTemperature: {
        enabled: true,
        scope: "custom",
        downDelta: 0.3,
        upDelta: 0.1,
        customTaskKeys: ["draft_chapter"],
      },
      routes: {
        draft_chapter: {
          primaryProfileId: "ollama:local",
          fallbackRoutes: [{ profileId: "ollama:backup", thinkingEnabled: false, multiTurnEnabled: true }],
          thinkingEnabled: false,
          multiTurnEnabled: true,
          temperature: 0.8,
        },
      },
      creationParameters: { "long-max-repair-rounds": "4" },
    });

    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(JSON.parse(String(init.body))).toEqual({
      kind: "save_settings",
      default_profile_id: "ollama:local",
      creative_temperature: {
        enabled: true,
        scope: "custom",
        down_delta: 0.3,
        up_delta: 0.1,
        custom_task_keys: ["draft_chapter"],
      },
      routes: {
        draft_chapter: {
          primary_profile_id: "ollama:local",
          fallback_routes: [{ profile_id: "ollama:backup", thinking_enabled: false, multi_turn_enabled: true }],
          thinking_enabled: false,
          multi_turn_enabled: true,
          temperature: 0.8,
        },
      },
      creation_parameters: { "long-max-repair-rounds": "4" },
    });
    expect(result.acceptedRouteIds).toEqual(["draft_chapter"]);
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "http://127.0.0.1:8900/api/v1/engine/commands/save-settings",
    );
  });

  it("sends permanent project deletion through the versioned Engine command", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        status: "deleted",
        message: "已永久删除 1 个项目目录。",
        deleted_project_ids: ["demo"],
        failures: [],
      }),
    });
    vi.stubGlobal("fetch", fetchMock);
    const client = new LegacyLocalEngineClient({ baseUrl: "http://127.0.0.1:8900" });

    const result = await client.deleteProjects({ kind: "delete_projects", projectIds: ["demo"] });

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "http://127.0.0.1:8900/api/v1/engine/commands/delete-projects",
    );
    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(JSON.parse(String(init.body))).toEqual({
      kind: "delete_projects",
      project_ids: ["demo"],
    });
    expect(result.deletedProjectIds).toEqual(["demo"]);
  });

  it("sends a real model probe command through the versioned Engine API", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        ok: true,
        detail: "连通 42ms",
        latency_ms: 42,
        supports_thinking: true,
        supports_multi_turn: true,
      }),
    });
    vi.stubGlobal("fetch", fetchMock);
    const client = new LegacyLocalEngineClient({ baseUrl: "http://127.0.0.1:8900" });

    const result = await client.testModelProfile({
      kind: "test_model_profile",
      id: "openai:gpt-5.6",
      previousId: "openai:gpt-4o",
      provider: "openai",
      model: "gpt-5.6",
      apiKeyAction: "replace",
      apiKey: "secret",
      baseUrl: "https://example.test/v1",
    });

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "http://127.0.0.1:8900/api/v1/engine/commands/test-model-profile",
    );
    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(JSON.parse(String(init.body))).toMatchObject({
      previous_id: "openai:gpt-4o",
      api_key_action: "replace",
      base_url: "https://example.test/v1",
    });
    expect(result.latencyMs).toBe(42);
  });

  it("uses Engine-owned error archive views and commands", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ entry_count: 2, project_count: 1, latest_time: "2026-08-12T00:00:00+00:00" }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ status: "cleared", message: "已清空", removed_project_count: 1 }),
      });
    vi.stubGlobal("fetch", fetchMock);
    const client = new LegacyLocalEngineClient({ baseUrl: "http://127.0.0.1:8900" });

    const summary = await client.getErrorArchiveSummary();
    const cleared = await client.clearErrorArchive({ kind: "clear_error_archive" });

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "http://127.0.0.1:8900/api/v1/engine/error-archive",
    );
    expect(fetchMock.mock.calls[1]?.[0]).toBe(
      "http://127.0.0.1:8900/api/v1/engine/commands/clear-error-archive",
    );
    expect(summary).toEqual({ entryCount: 2, projectCount: 1, latestTime: "2026-08-12T00:00:00+00:00" });
    expect(cleared.removedProjectCount).toBe(1);
  });

  it("uses the dedicated narrator rebuild command instead of a character design request", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        status: "accepted",
        message: "旁白音色已按当前作品档案重新生成，请试听确认。",
      }),
    });
    vi.stubGlobal("fetch", fetchMock);
    const client = new LegacyLocalEngineClient({ baseUrl: "http://127.0.0.1:8900" });

    await client.rebuildNarratorVoice({
      kind: "rebuild_narrator_voice",
      projectId: "demo",
      provider: "minimax",
    });

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "http://127.0.0.1:8900/api/v1/engine/commands/rebuild-narrator-voice",
    );
    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(JSON.parse(String(init.body))).toEqual({
      kind: "rebuild_narrator_voice",
      project_id: "demo",
      provider: "minimax",
    });
  });

  it("loads the selected voice chapter instead of only changing the local label", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        project_id: "demo / voice",
        project_title: "配音测试",
        provider_label: "mock",
        configured_model_label: "mock-tts",
        chapter_number: 3,
        available_chapters: [1, 2, 3],
        cast: [],
        team_confirmed: false,
        script: [],
        script_fresh: false,
        unresolved_speaker_count: 0,
        audio_ready: false,
        subtitle_ready: false,
        delivery_state: "not_started",
        active_task_id: null,
        subtitle_text: "",
        mix_tracks: [],
        sound_assets: [],
      }),
    });
    vi.stubGlobal("fetch", fetchMock);
    const client = new LegacyLocalEngineClient({ baseUrl: "http://127.0.0.1:8900" });

    const result = await client.getVoiceStudio("demo / voice", 3);

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "http://127.0.0.1:8900/api/v1/engine/voice/projects/demo%20%2F%20voice/studio?chapter_number=3",
    );
    expect(result.chapterNumber).toBe(3);
  });

  it("serializes a durable outline polish command for the local engine", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ status: "accepted", task_id: "outline-1", message: "已提交" }),
    });
    vi.stubGlobal("fetch", fetchMock);
    const client = new LegacyLocalEngineClient({ baseUrl: "http://127.0.0.1:8900" });

    const result = await client.polishOutline({
      kind: "polish_outline",
      projectId: "demo",
      userHint: "增强悬念",
      focusFields: ["goal"],
      chapterRange: "3-5",
      syncContracts: true,
    });

    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(JSON.parse(String(init.body))).toEqual({
      kind: "polish_outline",
      project_id: "demo",
      user_hint: "增强悬念",
      focus_fields: ["goal"],
      chapter_range: "3-5",
      sync_contracts: true,
    });
    expect(result.taskId).toBe("outline-1");
  });

  it("submits audio delivery without a long HTTP wait and resolves its persisted link", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ status: "accepted", task_id: "audio-export-1", message: "已提交" }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          task_id: "audio-export-1",
          title: "导出第 2 章音频",
          step_id: "completed",
          step_label: "已完成",
          status: "completed",
          progress_percent: 100,
          job_state: "succeeded",
          delivery: {
            filename: "chapter_002.mp3",
            download_url: "/api/v1/engine/voice/projects/demo/exports/chapter_002.mp3",
          },
          events: [],
        }),
      });
    vi.stubGlobal("fetch", fetchMock);
    const client = new LegacyLocalEngineClient({ baseUrl: "http://127.0.0.1:8900" });

    const submitted = await client.exportAudio({
      kind: "export_audio",
      projectId: "demo",
      scope: "chapter",
      chapterNumber: 2,
      format: "mp3",
    });
    const task = await client.getTaskStream("audio-export-1");

    expect(submitted.taskId).toBe("audio-export-1");
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "http://127.0.0.1:8900/api/v1/engine/commands/export-audio",
    );
    expect(fetchMock.mock.calls[1]?.[0]).toBe(
      "http://127.0.0.1:8900/api/v1/engine/jobs/audio-export-1/task-stream",
    );
    expect(task.delivery?.downloadUrl).toBe(
      "http://127.0.0.1:8900/api/v1/engine/voice/projects/demo/exports/chapter_002.mp3",
    );
  });

  it("serializes vector rebuilding through the durable Engine command", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ status: "accepted", task_id: "vectors-1", message: "已提交" }),
    });
    vi.stubGlobal("fetch", fetchMock);
    const client = new LegacyLocalEngineClient({ baseUrl: "http://127.0.0.1:8900" });

    const result = await client.rebuildMemoryVectors({
      kind: "rebuild_memory_vectors",
      projectId: "demo",
      includeExpression: true,
      fromChapter: 2,
      toChapter: 4,
    });

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "http://127.0.0.1:8900/api/v1/engine/commands/rebuild-memory-vectors",
    );
    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(JSON.parse(String(init.body))).toEqual({
      kind: "rebuild_memory_vectors",
      project_id: "demo",
      include_expression: true,
      from_chapter: 2,
      to_chapter: 4,
    });
    expect(result.taskId).toBe("vectors-1");
  });

  it("serializes chapter maintenance as durable Engine commands", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ status: "accepted", task_id: "maintenance-1", message: "已提交" }),
    });
    vi.stubGlobal("fetch", fetchMock);
    const client = new LegacyLocalEngineClient({ baseUrl: "http://127.0.0.1:8900" });

    await client.repairContinuity({
      kind: "repair_continuity",
      projectId: "demo",
      chapterNumber: 6,
      repairControlMode: "ai_assisted",
    });
    await client.repairCausal({ kind: "repair_causal", projectId: "demo", chapterNumber: 6 });
    await client.repairIssues({ kind: "repair_issues", projectId: "demo", chapterNumber: 6 });
    await client.reevaluateChapter({ kind: "reevaluate_chapter", projectId: "demo", chapterNumber: 6 });
    await client.reextractRelationships({ kind: "reextract_relationships", projectId: "demo" });
    await client.repairMotifHistory({ kind: "repair_motif_history", projectId: "demo", chapterNumber: 6, forceReExtract: true });

    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual([
      "http://127.0.0.1:8900/api/v1/engine/commands/repair-continuity",
      "http://127.0.0.1:8900/api/v1/engine/commands/repair-causal",
      "http://127.0.0.1:8900/api/v1/engine/commands/repair-issues",
      "http://127.0.0.1:8900/api/v1/engine/commands/reevaluate-chapter",
      "http://127.0.0.1:8900/api/v1/engine/commands/reextract-relationships",
      "http://127.0.0.1:8900/api/v1/engine/commands/repair-motif-history",
    ]);
    expect(JSON.parse(String((fetchMock.mock.calls[0]?.[1] as RequestInit).body))).toEqual({
      kind: "repair_continuity",
      project_id: "demo",
      chapter_number: 6,
      repair_control_mode: "ai_assisted",
    });
    expect(JSON.parse(String((fetchMock.mock.calls[4]?.[1] as RequestInit).body))).toEqual({
      kind: "reextract_relationships",
      project_id: "demo",
    });
    expect(JSON.parse(String((fetchMock.mock.calls[5]?.[1] as RequestInit).body))).toEqual({
      kind: "repair_motif_history",
      project_id: "demo",
      chapter_number: 6,
      force_re_extract: true,
    });
  });

  it("reads and saves manual init repair through the Engine boundary", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          project_id: "demo",
          available: true,
          artifact: "blueprint",
          artifact_label: "叙事蓝图",
          artifact_path: "plans/narrative_blueprint.json",
          payload: { synopsis: "旧蓝图" },
          revision: "rev-1",
          summary: "需要修复蓝图。",
          issues: [],
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          status: "saved",
          message: "已保存",
          repair: {
            project_id: "demo",
            available: true,
            artifact: "blueprint",
            artifact_label: "叙事蓝图",
            artifact_path: "plans/narrative_blueprint.json",
            payload: { synopsis: "新蓝图" },
            revision: "rev-2",
            summary: "需要修复蓝图。",
            issues: [],
          },
        }),
      });
    vi.stubGlobal("fetch", fetchMock);
    const client = new LegacyLocalEngineClient({ baseUrl: "http://127.0.0.1:8900" });

    const repair = await client.getInitManualRepair("demo");
    const saved = await client.saveInitManualRepair({
      kind: "save_init_manual_repair",
      projectId: "demo",
      artifact: "blueprint",
      payload: { synopsis: "新蓝图" },
      expectedRevision: repair.revision,
    });

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "http://127.0.0.1:8900/api/v1/engine/projects/demo/init-manual-repair",
    );
    expect(fetchMock.mock.calls[1]?.[0]).toBe(
      "http://127.0.0.1:8900/api/v1/engine/commands/save-init-manual-repair",
    );
    const init = fetchMock.mock.calls[1]?.[1] as RequestInit;
    expect(JSON.parse(String(init.body))).toEqual({
      kind: "save_init_manual_repair",
      project_id: "demo",
      artifact: "blueprint",
      payload: { synopsis: "新蓝图" },
      expected_revision: "rev-1",
    });
    expect(saved.repair?.revision).toBe("rev-2");
  });

  it("serializes workflow AI requests and restores camel-case candidate fields", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        status: "generated",
        message: "请审阅",
        payload: {
          premise: "新的前提",
          characters_hint: "新的角色提示",
        },
        suggestions: ["强化人物选择"],
        creative_note: { core_pitch: "被删除的相认" },
      }),
    });
    vi.stubGlobal("fetch", fetchMock);
    const client = new LegacyLocalEngineClient({ baseUrl: "http://127.0.0.1:8900" });

    const result = await client.generateWorkflowFields({
      kind: "generate_workflow_fields",
      mode: "long",
      operation: "polish",
      currentPayload: { premise: "旧前提", charactersHint: "旧角色提示" },
      userHint: "强化人物动机",
      focusFields: ["premise", "charactersHint"],
    });

    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(JSON.parse(String(init.body))).toEqual({
      kind: "generate_workflow_fields",
      mode: "long",
      operation: "polish",
      current_payload: { premise: "旧前提", characters_hint: "旧角色提示" },
      user_hint: "强化人物动机",
      focus_fields: ["premise", "characters_hint"],
    });
    expect(result.payload.charactersHint).toBe("新的角色提示");
    expect(result.creativeNote?.corePitch).toBe("被删除的相认");
  });

  it("serializes the restored outline maintenance commands", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ status: "accepted", task_id: "outline-maintenance-1", message: "已提交" }),
    });
    vi.stubGlobal("fetch", fetchMock);
    const client = new LegacyLocalEngineClient({ baseUrl: "http://127.0.0.1:8900" });

    await client.syncChapterContracts({
      kind: "sync_chapter_contracts",
      projectId: "demo",
      affectedChapterNumbers: [3, 4],
      proseUntouched: true,
    });
    await client.extendOutline({
      kind: "extend_outline",
      projectId: "demo",
      additionalChapters: 10,
      syncContracts: true,
    });

    const syncInit = fetchMock.mock.calls[0]?.[1] as RequestInit;
    const extendInit = fetchMock.mock.calls[1]?.[1] as RequestInit;
    expect(JSON.parse(String(syncInit.body))).toEqual({
      kind: "sync_chapter_contracts",
      project_id: "demo",
      affected_chapter_numbers: [3, 4],
      prose_untouched: true,
    });
    expect(JSON.parse(String(extendInit.body))).toEqual({
      kind: "extend_outline",
      project_id: "demo",
      additional_chapters: 10,
      sync_contracts: true,
    });
  });

  it("preserves full narrative descriptions through the snake-case transport", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        characters: [],
        character_details: [],
        relationships: [],
        visualization: {
          total_chapters: 60,
          phases: [],
          milestones: [{
            id: "milestone-0",
            chapter: 1,
            label: "许清禾上门求助",
            description: "许清禾上门求助，沈岸核验她连续七天的噩梦与现实幻觉。",
          }],
          subplot_lanes: [{
            id: "subplot-0",
            label: "秘密线",
            tone: "jade",
            events: [{
              id: "subplot-0-event",
              chapter: 1,
              label: "各角色隐瞒秘密",
              description: "各角色隐瞒自己的秘密，维持表面合作关系。",
            }],
          }],
          weave_links: [],
        },
        outline: [],
        subplots: [],
        humanize_patterns: [],
        revision_candidates: [],
      }),
    });
    vi.stubGlobal("fetch", fetchMock);
    const client = new LegacyLocalEngineClient({ baseUrl: "http://127.0.0.1:8900" });

    const result = await client.getNarrativeTools("入梦破局");

    expect(result.visualization.milestones[0]?.description).toBe(
      "许清禾上门求助，沈岸核验她连续七天的噩梦与现实幻觉。",
    );
    expect(result.visualization.subplotLanes[0]?.events[0]?.description).toBe(
      "各角色隐瞒自己的秘密，维持表面合作关系。",
    );
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "http://127.0.0.1:8900/api/v1/ui/projects/%E5%85%A5%E6%A2%A6%E7%A0%B4%E5%B1%80/narrative-tools",
    );
  });

  // The 章台卡死 bug fix relies on the chain:
  //   backend 422 -> EngineHttpError -> useNarrativeTools / chapter-studio effect
  //   catch -> narrativeToolsError / chapterStudioError -> buildChapterStudioLoadingState
  //   -> <LoadingSurface error="..." />
  // The two tests below pin down the HTTP boundary so the frontend wiring has
  // something to consume.
  it("surfaces the 422 missing-outline body from getChapterStudio as EngineHttpError", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 422,
      statusText: "Unprocessable Entity",
      json: async () => ({
        detail: "项目 demo 尚未生成大纲，请先在机杼完成大纲规划。",
      }),
    });
    vi.stubGlobal("fetch", fetchMock);
    const client = new LegacyLocalEngineClient({ baseUrl: "http://127.0.0.1:8900" });

    await expect(client.getChapterStudio("demo")).rejects.toMatchObject({
      name: "EngineHttpError",
      status: 422,
      endpoint: expect.stringContaining(
        "/api/v1/engine/novel/projects/demo/studio",
      ),
      message: expect.stringContaining("尚未生成大纲"),
    });
  });

  it("surfaces the 404 chapter-out-of-range body from getChapterStudio as EngineHttpError", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 404,
      statusText: "Not Found",
      json: async () => ({
        detail: "项目 demo 没有第 99 章。",
      }),
    });
    vi.stubGlobal("fetch", fetchMock);
    const client = new LegacyLocalEngineClient({ baseUrl: "http://127.0.0.1:8900" });

    await expect(client.getChapterStudio("demo")).rejects.toMatchObject({
      name: "EngineHttpError",
      status: 404,
      message: expect.stringContaining("没有第 99 章"),
    });
  });

  it("turns a Tauri Load failed response into an actionable connection error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Load failed")));
    const client = new LegacyLocalEngineClient({
      baseUrl: "http://127.0.0.1:8900",
      maxRetries: 0,
    });

    await expect(client.getChapterStudio("demo")).rejects.toMatchObject({
      name: "EngineConnectionError",
      endpoint: "/api/v1/engine/novel/projects/demo/studio",
      message: expect.stringContaining("后端需安全重启"),
    });
  });

  it("builds a voice preview plan through the engine command boundary", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        status: "accepted",
        message: "已为 1 个角色生成音色候选计划。",
        data: {
          plans: [
            {
              character_id: "lin-yuan",
              character_name: "林远",
              sample_text: "林远。黄昏时分。",
              speed: 1.0,
              volume: 1.0,
              candidates: [{ voice_id: "m1", voice_name: "男声", description: "" }],
            },
          ],
        },
      }),
    });
    vi.stubGlobal("fetch", fetchMock);
    const client = new LegacyLocalEngineClient({ baseUrl: "http://127.0.0.1:8900" });

    const result = await client.buildVoicePreviewPlan({
      kind: "build_voice_preview_plan",
      projectId: "demo",
      characters: [{ characterId: "lin-yuan", name: "林远", gender: "male" }],
      candidateCount: 3,
    });

    expect(result.status).toBe("accepted");
    expect(String(fetchMock.mock.calls[0]?.[0])).toContain(
      "/api/v1/engine/commands/build-voice-preview-plan",
    );
    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(JSON.parse(String(init.body))).toEqual({
      kind: "build_voice_preview_plan",
      project_id: "demo",
      characters: [{ character_id: "lin-yuan", name: "林远", gender: "male" }],
      candidate_count: 3,
    });
  });

  it("prefixes candidate audio urls after generating previews", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        status: "accepted",
        message: "候选试听已生成（1/1 成功）。",
        data: {
          plan: {
            character_id: "lin-yuan",
            character_name: "林远",
            sample_text: "试听。",
            speed: 1.0,
            volume: 1.0,
            candidates: [
              {
                voice_id: "m1",
                voice_name: "男声",
                description: "",
                audio_url: "/api/v1/engine/voice/projects/demo/audio/audio/chapter_000/preview_m1.mp3",
              },
            ],
          },
        },
      }),
    });
    vi.stubGlobal("fetch", fetchMock);
    const client = new LegacyLocalEngineClient({ baseUrl: "http://127.0.0.1:8900" });

    const result = await client.generateVoicePreviews({
      kind: "generate_voice_previews",
      projectId: "demo",
      plan: {
        characterId: "lin-yuan",
        characterName: "林远",
        sampleText: "试听。",
        speed: 1.0,
        volume: 1.0,
        candidates: [{ voiceId: "m1", voiceName: "男声", description: "" }],
      },
    });

    const payload = result.data as { plan?: { candidates?: { audioUrl?: string }[] } };
    expect(payload?.plan?.candidates?.[0]?.audioUrl).toBe(
      "http://127.0.0.1:8900/api/v1/engine/voice/projects/demo/audio/audio/chapter_000/preview_m1.mp3",
    );
  });

  it("confirms a voice preview into the team through the engine command boundary", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        status: "accepted",
        message: "已确认角色音色并写入配音团队（lin-yuan）。",
        data: { team: { entries: [], confirmed: false } },
      }),
    });
    vi.stubGlobal("fetch", fetchMock);
    const client = new LegacyLocalEngineClient({ baseUrl: "http://127.0.0.1:8900" });

    const result = await client.confirmVoicePreview({
      kind: "confirm_voice_preview",
      projectId: "demo",
      characterId: "lin-yuan",
      voiceId: "m1",
      speed: 1.1,
      volume: 0.9,
      sampleText: "试听。",
      samplePath: "/tts/audio/chapter_000/preview_m1.mp3",
    });

    expect(result.status).toBe("accepted");
    expect(String(fetchMock.mock.calls[0]?.[0])).toContain(
      "/api/v1/engine/commands/confirm-voice-preview",
    );
    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(JSON.parse(String(init.body))).toEqual({
      kind: "confirm_voice_preview",
      project_id: "demo",
      character_id: "lin-yuan",
      voice_id: "m1",
      speed: 1.1,
      volume: 0.9,
      sample_text: "试听。",
      sample_path: "/tts/audio/chapter_000/preview_m1.mp3",
    });
  });
});
