import type {
  ModelProfileView,
  RouteCommand,
  SettingsView,
  TaskRouteView,
} from "@nimo/engine-contracts";

export const voiceTextRouteDefinitions = [
  {
    taskKey: "tts_generate_dubbing_script",
    label: "配音脚本生成",
    hint: "将章节正文转换为分段、角色、情绪和声音提示的结构化脚本。",
    temperatureKey: "ttsScriptTemperature",
    defaultTemperature: "0.20",
  },
  {
    taskKey: "tts_adjudicate_script_segments",
    label: "说话人证据复核",
    hint: "只复核正文规则无法确定的对白归属，不改写台词。",
    temperatureKey: "ttsReviewTemperature",
    defaultTemperature: "0.00",
  },
  {
    taskKey: "tts_review_dubbing_script",
    label: "配音脚本专业审校",
    hint: "独立检查可演性、声音角色、情绪意图和合成稳定性。",
    temperatureKey: "ttsReviewTemperature",
    defaultTemperature: "0.00",
  },
  {
    taskKey: "tts_adjudicate_voice_match",
    label: "角色音色复核",
    hint: "在硬约束候选白名单内比较音色，并给出试听或改配建议。",
    temperatureKey: "ttsReviewTemperature",
    defaultTemperature: "0.00",
  },
  {
    taskKey: "tts_build_narrator_profile",
    label: "旁白画像构建",
    hint: "分析作品气质并生成旁白语速、叙述距离与情绪范围。",
    temperatureKey: "ttsNarratorTemperature",
    defaultTemperature: "0.20",
  },
  {
    taskKey: "tts_sound_design",
    label: "声音设计提取",
    hint: "从定稿脚本中提取 SFX、BGM、环境声与转场设计。",
    temperatureKey: "ttsSoundDesignTemperature",
    defaultTemperature: "0.50",
  },
] as const;

export type VoiceTextRouteTaskKey = typeof voiceTextRouteDefinitions[number]["taskKey"];
export type VoiceTextTemperatureKey = typeof voiceTextRouteDefinitions[number]["temperatureKey"];

export interface VoiceTextRouteDraft {
  readonly primaryProfileId: string;
  readonly fallbackProfileIds: readonly string[];
}

export type VoiceTextRouteDrafts = Readonly<Record<VoiceTextRouteTaskKey, VoiceTextRouteDraft>>;

export const voiceTextRouteTaskKeys = new Set<string>(
  voiceTextRouteDefinitions.map((definition) => definition.taskKey),
);

function allRoutes(settings: Pick<SettingsView, "routingGroups">): readonly TaskRouteView[] {
  return settings.routingGroups.flatMap((group) => group.routes);
}

export function routeableVoiceProfiles(
  settings: Pick<SettingsView, "modelProfiles">,
): readonly ModelProfileView[] {
  return settings.modelProfiles.filter(
    (profile) =>
      profile.isEmbedding !== true
      && profile.keyConfigured !== false
      && (
        profile.statusLabel.includes("已配置")
        || profile.statusLabel.includes("通过")
        || profile.keyConfigured === true
      ),
  );
}

export function createVoiceTextRouteDrafts(
  settings?: Pick<SettingsView, "defaultProfileId" | "modelProfiles" | "routingGroups">,
): VoiceTextRouteDrafts {
  const routes = settings ? allRoutes(settings) : [];
  const profiles = settings ? routeableVoiceProfiles(settings) : [];
  const fallbackPrimary = settings?.defaultProfileId || profiles[0]?.id || "";

  return Object.fromEntries(voiceTextRouteDefinitions.map((definition) => {
    const route = routes.find((candidate) => candidate.taskKey === definition.taskKey);
    return [
      definition.taskKey,
      {
        primaryProfileId: route?.primaryProfileId || fallbackPrimary,
        fallbackProfileIds: (route?.fallbackRoutes ?? [])
          .map((fallback) => fallback.profileId)
          .filter(Boolean)
          .slice(0, 3),
      },
    ];
  })) as unknown as VoiceTextRouteDrafts;
}

export function voiceTextRoutesToCommands(
  routes: VoiceTextRouteDrafts,
  temperatures: Readonly<Record<VoiceTextTemperatureKey, string>>,
): Readonly<Record<string, RouteCommand>> {
  const commands: Record<string, RouteCommand> = {};
  for (const definition of voiceTextRouteDefinitions) {
    const route = routes[definition.taskKey];
    if (!route.primaryProfileId) continue;
    const seen = new Set<string>([route.primaryProfileId]);
    const fallbackRoutes = route.fallbackProfileIds
      .filter((profileId) => {
        if (!profileId || seen.has(profileId)) return false;
        seen.add(profileId);
        return true;
      })
      .slice(0, 3)
      .map((profileId) => ({
        profileId,
        thinkingEnabled: false,
        multiTurnEnabled: false,
      }));
    const parsedTemperature = Number.parseFloat(
      temperatures[definition.temperatureKey] || definition.defaultTemperature,
    );
    commands[definition.taskKey] = {
      primaryProfileId: route.primaryProfileId,
      fallbackRoutes,
      thinkingEnabled: false,
      multiTurnEnabled: false,
      temperature: Number.isFinite(parsedTemperature) ? parsedTemperature : null,
    };
  }
  return commands;
}

export function withoutVoiceTextRoutes(settings: SettingsView): SettingsView {
  return {
    ...settings,
    routingGroups: settings.routingGroups
      .map((group) => {
        const routes = group.routes.filter((route) => !voiceTextRouteTaskKeys.has(route.taskKey));
        const routeIds = new Set(routes.map((route) => route.id));
        return {
          ...group,
          routes,
          subgroups: group.subgroups
            .map((subgroup) => ({
              ...subgroup,
              routeIds: subgroup.routeIds.filter((routeId) => routeIds.has(routeId)),
            }))
            .filter((subgroup) => subgroup.routeIds.length > 0),
        };
      })
      .filter((group) => group.routes.length > 0),
  };
}
