import { renderToStaticMarkup } from "react-dom/server";

import type { EngineClient, EngineCommandClient, VoiceStudioView } from "@nimo/engine-contracts";
import { describe, expect, it } from "vitest";

import { VoiceStudioPage } from "./VoiceStudioPage";
import { SoundLibraryPanel } from "./voice-studio/SoundLibraryPanel";
import type { VoiceAudioModelCenterClient } from "./VoiceLocalModelCenter";
import type { TaskStreamClient } from "../lib/use-task-stream";

const commandClient = {} as EngineCommandClient;
const catalogClient = {} as Pick<EngineClient, "getVoiceCatalog">;
const modelCenterClient = {} as VoiceAudioModelCenterClient;
const streamClient = {} as TaskStreamClient;

const cast: VoiceStudioView["cast"] = [
  {
    id: "narrator",
    name: "旁白",
    role: "叙述者",
    statusLabel: "已配",
    voiceLabel: "沉静女声",
    description: "用于测试的旁白音色。",
  },
];

const script: VoiceStudioView["script"] = [
  {
    id: "segment-1",
    segmentIndex: 1,
    speakerId: "narrator",
    speakerLabel: "旁白",
    kindLabel: "旁白",
    emotionLabel: "中性",
    content: "测试配音片段。",
    statusLabel: "待合成",
    needsSpeakerReview: false,
  },
];

function renderVoiceStudio(
  overrides: Partial<VoiceStudioView>,
  initialTab: "team" | "script" | "room" | "post",
): string {
  return renderToStaticMarkup(
    <VoiceStudioPage
      catalogClient={catalogClient}
      commandClient={commandClient}
      initialTab={initialTab}
      modelCenterClient={modelCenterClient}
      streamClient={streamClient}
      studio={{
        projectId: "empty-voice-project",
        projectTitle: "空声腔项目",
        providerLabel: "mock",
        configuredModelLabel: "mock-tts",
        chapterNumber: 1,
        availableChapters: [],
        teamConfirmed: false,
        scriptFresh: false,
        unresolvedSpeakerCount: 0,
        audioReady: false,
        subtitleReady: false,
        deliveryState: "not_started",
        activeTaskId: null,
        subtitleText: "",
        mixTracks: [],
        soundAssets: [],
        cast: [],
        script: [],
        ...overrides,
        roomTakes: overrides.roomTakes ?? [],
      }}
    />,
  );
}

describe("VoiceStudioPage empty data", () => {
  it("observes an Engine TTS task that was already active before the page reloaded", () => {
    const markup = renderVoiceStudio({ activeTaskId: "tts-running-1" }, "post");

    expect(markup).toContain("音频合成");
    expect(markup).toContain("正在同步任务进度");
    expect(markup).not.toContain("进度待命");
  });

  it("restores active team and script tasks from the Engine task set", () => {
    const teamMarkup = renderVoiceStudio({
      activeTasks: [{ id: "voice-team-running", kind: "team_build" }],
    }, "team");
    const scriptMarkup = renderVoiceStudio({
      activeTasks: [{ id: "voice-script-running", kind: "script_generation" }],
    }, "script");

    expect(teamMarkup).toContain("正在组建并准备试听…");
    expect(teamMarkup).toContain("已提交 · 等待启动");
    expect(scriptMarkup).toContain("脚本生成");
    expect(scriptMarkup).not.toContain("进度待命");
  });

  it("renders the selected role's persisted voice detail and performance values", () => {
    const markup = renderVoiceStudio({
      cast: [{
        id: "chen-banxian",
        name: "陈半仙",
        role: "supporting",
        statusLabel: "待试听确认",
        voiceLabel: "ttv-voice-chen",
        voiceId: "ttv-voice-chen",
        voiceSourceLabel: "AI 特征设计",
        speedOffset: 0.12,
        pitchOffset: -2,
        volumeOffset: 0.08,
        performancePolicyLabel: "人工覆盖：语速；其余参数保持自动策略。",
        matchSummary: "画像匹配 92%",
        description: "声线克制，适合以旁观口吻揭示线索。",
        detailFacts: [{ label: "角色依据", value: "配角 · 男性 · 58岁" }],
        matchReasons: ["低沉声线能托住旁观者的距离感"],
        auditionWarnings: ["确认慢句不会拖沓"],
        auditionText: "有些事，到了该说的时候。",
        designBrief: "男声，偏低但清晰，语速平稳。",
      }],
      script,
    }, "team");

    expect(markup).toContain("音色详情");
    expect(markup).toContain("陈半仙");
    expect(markup).toContain("ttv-voice-chen");
    expect(markup).toContain("AI 特征设计");
    expect(markup).toContain("画像匹配 92%");
    expect(markup).toContain("人工覆盖：语速；其余参数保持自动策略。");
    expect(markup).toContain("配置状态");
    expect(markup).toContain("评估状态");
    expect(markup).toContain("声纹档案 · 8项");
    expect(markup).toContain('aria-label="当前角色声音档案"');
    expect(markup).toContain("声音档案");
    expect(markup).toContain("工作进度");
    expect(markup).toContain("生成试听确认效果");
    expect(markup).toContain("确认配音团队");
    expect(markup).toContain("声纹依据");
    expect(markup).toContain("查看完整档案");
    expect(markup).toContain("角色依据");
    expect(markup).not.toContain('aria-expanded="false"');
    expect(markup).not.toContain('id="voiceprint-full-dossier"');
    expect(markup).not.toContain("低沉声线能托住旁观者的距离感");
    expect(markup).not.toContain("确认慢句不会拖沓");
    expect(markup).not.toContain("有些事，到了该说的时候。");
    expect(markup).toContain("编辑声纹");
    expect(markup).not.toContain("声纹工作台");
    expect(markup).toContain('value="12"');
    expect(markup).toContain('value="-2"');
    expect(markup).toContain('value="8"');
  });

  it("renders a team setup state when the backend has no cast", () => {
    const markup = renderVoiceStudio({ cast: [], script }, "team");

    expect(markup).toContain("尚未建立配音团队");
    expect(markup).toContain("自动组建");
  });

  it("makes narrator rebuilding explicit in the team workspace", () => {
    const markup = renderVoiceStudio({ cast, script }, "team");

    expect(markup).toContain("重新生成旁白音色");
    expect(markup).not.toContain("上传参考音频");
  });

  it("renders script and room setup states when the backend has no script", () => {
    const scriptMarkup = renderVoiceStudio({ cast, script: [] }, "script");
    const roomMarkup = renderVoiceStudio({ cast, script: [] }, "room");

    expect(scriptMarkup).toContain("当前章节还没有配音脚本");
    expect(roomMarkup).toContain("暂无可试听片段");
  });

  it("renders persisted script segments in one vertical document lane", () => {
    const markup = renderVoiceStudio({
      cast,
      script: [
        script[0]!,
        {
          ...script[0]!,
          id: "segment-2",
          segmentIndex: 2,
          content: "第二段测试配音片段。",
        },
      ],
    }, "script");

    expect(markup).toContain('class="voice-script-list"');
    expect(markup.indexOf("测试配音片段。")).toBeLessThan(
      markup.indexOf("第二段测试配音片段。"),
    );
    expect(markup).not.toContain("voice-status-spinner");
    expect(markup).toContain("风格模仿");
    expect(markup).toContain("选择参考配音脚本");
    expect(markup).toContain('accept=".txt,.md,.srt,.vtt');
    expect(markup).toContain("参考原文不会写入风格画像");
  });

  it("reserves the bottom bar for workflow progress instead of script content", () => {
    const markup = renderVoiceStudio({ cast, script }, "script");
    const footerMarkup = markup.slice(markup.indexOf("<footer"));

    expect(footerMarkup).toContain("进度待命");
    expect(footerMarkup).not.toContain("测试配音片段。");
    expect(footerMarkup).not.toContain("当前章节");
    expect(markup).toContain('aria-label="配音脚本生成进度"');
    expect(markup).toContain('class="voice-progress-bar"');
  });

  it("renders accessible export and cleanup menu triggers in post production", () => {
    const markup = renderVoiceStudio({ cast, script }, "post");

    expect(markup).toContain('aria-controls="voice-export-menu"');
    expect(markup).toContain('aria-controls="voice-more-menu"');
    expect(markup).toContain('aria-haspopup="menu"');
    expect(markup).toContain('aria-expanded="false"');
    expect(markup).toContain("导出 ▾");
    expect(markup).toContain("更多 ▾");
  });

  it("separates listening approval from commercial-rights clearance in the sound library", () => {
    const markup = renderToStaticMarkup(
      <SoundLibraryPanel
        assets={[{
        id: "generated-night-bed",
        kind: "bgm",
        name: "夜色底乐",
        status: "approved",
        scope: "project",
        source: "generated",
        tags: ["夜色", "悬疑"],
        provider: "minimax_music",
        model: "music-2.6",
        license: "MiniMax 账号条款待复核",
        commercialUseStatus: "review_required",
        prompt: "subtle suspense instrumental",
        audioUrl: "",
        }]}
        busy={false}
        commandClient={commandClient}
        onNotice={() => {}}
        onRefresh={async () => {}}
        projectId="empty-voice-project"
      />,
    );

    expect(markup).toContain("商用权利复核");
    expect(markup).toContain("保存授权结论");
    expect(markup).toContain("听感批准不代表获得发行权");
    expect(markup).toContain("待核对授权");
  });
});
