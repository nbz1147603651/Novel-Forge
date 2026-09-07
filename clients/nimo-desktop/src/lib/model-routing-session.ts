import type { ModelProfileView, SettingsView, TaskRouteFallbackView, TaskRouteView } from "@nimo/engine-contracts";

/** The desktop runtime honours at most three ordered automatic fallbacks. */
export const MAX_ACTIVE_FALLBACK_ROUTES = 3;

export interface RouteFallbackDraft {
  readonly profileId: string;
  readonly thinkingEnabled: boolean;
  readonly multiTurnEnabled: boolean;
}

/**
 * Credential-free, editable projection of one task route.
 *
 * The list retains the complete candidate ordering because the legacy popup
 * displays every profile.  Only the first three routeable candidates are
 * produced as active fallbacks by the future Engine command.
 */
export interface RouteDraft {
  readonly primaryProfileId: string;
  readonly fallbackRoutes: readonly RouteFallbackDraft[];
  readonly thinkingEnabled: boolean;
  readonly multiTurnEnabled: boolean;
  readonly temperature: number | null;
}

/**
 * The complete, credential-free routing draft owned by SettingsPage.
 *
 * Keeping this projection above individual workbench mounts is important:
 * model management, route editing and the collapsed settings card must all
 * operate on exactly the same draft until a future Engine command accepts it.
 */
export interface ModelRoutingSessionState {
  readonly defaultProfileId: string;
  readonly profiles: readonly ModelProfileView[];
  /**
   * Session-only secret mutations. They are submitted directly to the local
   * engine on save and are never returned by SettingsView or browser storage.
   */
  readonly connectionDrafts: Readonly<Record<string, ModelProfileConnectionState>>;
  readonly drafts: Readonly<Record<string, RouteDraft>>;
  /** Last accepted group/subgroup bulk drafts, keyed by stable UI scope id. */
  readonly bulkDrafts: Readonly<Record<string, RouteDraft>>;
}

export interface ModelProfileConnectionState {
  readonly previousId?: string | undefined;
  readonly apiKey: string;
  readonly apiKeyAction: "preserve" | "replace" | "clear";
  readonly baseUrl: string;
}

export interface ModelProfileDraft {
  readonly label: string;
  readonly provider: string;
  readonly model: string;
  readonly tierLabel: string;
  readonly supportsThinking: boolean;
  readonly supportsMultiTurn: boolean;
}

export function isRouteableProfile(profile: ModelProfileView): boolean {
  return profile.statusLabel === "已配置" || profile.statusLabel.includes("通过") || profile.statusLabel === "本地草案";
}

/** Mirrors the PySide rule: embeddings are selectable only for vector retrieval. */
export function isGenerationProfile(profile: ModelProfileView): boolean {
  return profile.isEmbedding !== true;
}

export function isGenerationRouteableProfile(profile: ModelProfileView): boolean {
  return isGenerationProfile(profile) && isRouteableProfile(profile);
}

function fallbackDraftsFromView(fallbackRoutes: readonly TaskRouteFallbackView[]): readonly RouteFallbackDraft[] {
  return fallbackRoutes.map((route) => ({
    profileId: route.profileId,
    thinkingEnabled: route.thinkingEnabled,
    multiTurnEnabled: route.multiTurnEnabled,
  }));
}

function routeDraftFromView(route: TaskRouteView): RouteDraft {
  return {
    primaryProfileId: route.primaryProfileId,
    fallbackRoutes: fallbackDraftsFromView(route.fallbackRoutes),
    thinkingEnabled: route.thinkingEnabled,
    multiTurnEnabled: route.multiTurnEnabled,
    temperature: route.temperature,
  };
}

export function routeDraftsFromSettings(settings: SettingsView): Readonly<Record<string, RouteDraft>> {
  return Object.fromEntries(settings.routingGroups.flatMap((group) => group.routes.map((route) => [
    route.id,
    sanitizeRouteDraft(routeDraftFromView(route), settings.modelProfiles, route.supportsMultiTurn),
  ]))) as Readonly<Record<string, RouteDraft>>;
}

export function modelRoutingSessionFromSettings(settings: SettingsView): ModelRoutingSessionState {
  return {
    defaultProfileId: normalizeDefaultProfileId(settings.defaultProfileId ?? settings.defaultProvider, settings.modelProfiles),
    profiles: settings.modelProfiles,
    connectionDrafts: Object.fromEntries(settings.modelProfiles.map((profile) => [
      profile.id,
      {
        apiKey: "",
        apiKeyAction: "preserve",
        baseUrl: profile.baseUrl ?? "",
      },
    ])),
    drafts: routeDraftsFromSettings(settings),
    bulkDrafts: {},
  };
}

/** Resolve the profile the runtime will actually receive for a task. */
export function effectivePrimaryProfileId(draft: RouteDraft, defaultProfileId: string): string {
  return draft.primaryProfileId || defaultProfileId;
}

export function normalizeDefaultProfileId(
  defaultProfileId: string,
  profiles: readonly ModelProfileView[],
): string {
  const generationProfiles = profiles.filter(isGenerationProfile);
  if (generationProfiles.some((profile) => profile.id === defaultProfileId)) return defaultProfileId;
  return generationProfiles.find(isRouteableProfile)?.id ?? generationProfiles[0]?.id ?? "";
}

export function setSessionDefaultProfile(
  session: ModelRoutingSessionState,
  defaultProfileId: string,
): ModelRoutingSessionState {
  return {
    ...session,
    defaultProfileId: normalizeDefaultProfileId(defaultProfileId, session.profiles),
  };
}

export function setSessionRouteDraft(
  session: ModelRoutingSessionState,
  routeId: string,
  draft: RouteDraft,
  supportsMultiTurn: boolean,
): ModelRoutingSessionState {
  return {
    ...session,
    drafts: {
      ...session.drafts,
      [routeId]: sanitizeRouteDraft(draft, session.profiles, supportsMultiTurn),
    },
  };
}

export function setSessionBulkDraft(
  session: ModelRoutingSessionState,
  bulkKey: string,
  draft: RouteDraft,
  supportsMultiTurn: boolean,
): ModelRoutingSessionState {
  return {
    ...session,
    bulkDrafts: {
      ...session.bulkDrafts,
      [bulkKey]: sanitizeRouteDraft(draft, session.profiles, supportsMultiTurn),
    },
  };
}

export function applySessionGroupRouteDraft(
  session: ModelRoutingSessionState,
  routeIds: readonly string[],
  draft: RouteDraft,
  routeSupportsMultiTurnById: Readonly<Record<string, boolean>> = {},
  bulkKey?: string,
): ModelRoutingSessionState {
  const next = {
    ...session,
    drafts: applyGroupRouteDraft(
      session.drafts,
      routeIds,
      draft,
      session.profiles,
      routeSupportsMultiTurnById,
    ),
  };
  if (bulkKey === undefined) return next;
  const supportsMultiTurn = routeIds.some((routeId) => routeSupportsMultiTurnById[routeId] ?? true);
  return setSessionBulkDraft(next, bulkKey, draft, supportsMultiTurn);
}

/** Clear explicit routes in a group so those tasks inherit the default model. */
export function clearSessionGroupRouteOverrides(
  session: ModelRoutingSessionState,
  routeIds: readonly string[],
  routeSupportsMultiTurnById: Readonly<Record<string, boolean>> = {},
): ModelRoutingSessionState {
  const routeIdSet = new Set(routeIds);
  return {
    ...session,
    drafts: Object.fromEntries(Object.entries(session.drafts).map(([routeId, draft]) => {
      if (!routeIdSet.has(routeId)) return [routeId, draft];
      return [routeId, sanitizeRouteDraft({
        ...draft,
        primaryProfileId: "",
        fallbackRoutes: [],
        thinkingEnabled: false,
        multiTurnEnabled: false,
      }, session.profiles, routeSupportsMultiTurnById[routeId] ?? true)];
    })),
  };
}

export function profileDraftFromProfile(profile: ModelProfileView): ModelProfileDraft {
  return {
    label: profile.label,
    provider: profile.provider,
    model: profile.model,
    tierLabel: profile.tierLabel,
    supportsThinking: profile.supportsThinking,
    supportsMultiTurn: profile.supportsMultiTurn,
  };
}

export function profileIdForDraft(draft: Pick<ModelProfileDraft, "provider" | "model">): string {
  return `${draft.provider.trim()}:${draft.model.trim()}`.toLowerCase();
}

/**
 * Maintain source-compatible text-generation candidate ordering: each visible
 * generation profile appears once, current primary is excluded, and capability
 * toggles are scoped to the candidate model rather than the primary model.
 */
export function sanitizeRouteDraft(
  draft: RouteDraft,
  profiles: readonly ModelProfileView[],
  supportsMultiTurn = true,
): RouteDraft {
  const generationProfiles = profiles.filter(isGenerationProfile);
  const primary = generationProfiles.find((profile) => profile.id === draft.primaryProfileId);
  const knownProfiles = new Map(generationProfiles.map((profile) => [profile.id, profile]));
  const seen = new Set<string>();
  const fallbackRoutes: RouteFallbackDraft[] = [];

  for (const candidate of draft.fallbackRoutes) {
    const profile = knownProfiles.get(candidate.profileId);
    if (profile === undefined || candidate.profileId === draft.primaryProfileId || seen.has(candidate.profileId)) continue;
    seen.add(candidate.profileId);
    fallbackRoutes.push({
      profileId: candidate.profileId,
      thinkingEnabled: isRouteableProfile(profile) && profile.supportsThinking ? candidate.thinkingEnabled : false,
      multiTurnEnabled: supportsMultiTurn && isRouteableProfile(profile) && profile.supportsMultiTurn ? candidate.multiTurnEnabled : false,
    });
  }

  for (const profile of generationProfiles) {
    if (profile.id === draft.primaryProfileId || seen.has(profile.id)) continue;
    seen.add(profile.id);
    fallbackRoutes.push({
      profileId: profile.id,
      thinkingEnabled: false,
      multiTurnEnabled: false,
    });
  }

  return {
    primaryProfileId: primary?.id ?? "",
    fallbackRoutes,
    thinkingEnabled: primary?.supportsThinking ? draft.thinkingEnabled : false,
    multiTurnEnabled: supportsMultiTurn && primary?.supportsMultiTurn ? draft.multiTurnEnabled : false,
    temperature: draft.temperature === null ? null : Math.min(2, Math.max(0, Number.isFinite(draft.temperature) ? draft.temperature : 0)),
  };
}

export function reorderFallbackRoute(
  draft: RouteDraft,
  profileId: string,
  direction: "up" | "down",
  profiles: readonly ModelProfileView[],
  supportsMultiTurn = true,
): RouteDraft {
  const normalized = sanitizeRouteDraft(draft, profiles, supportsMultiTurn);
  const currentIndex = normalized.fallbackRoutes.findIndex((route) => route.profileId === profileId);
  const nextIndex = direction === "up" ? currentIndex - 1 : currentIndex + 1;
  if (currentIndex < 0 || nextIndex < 0 || nextIndex >= normalized.fallbackRoutes.length) return normalized;
  const fallbackRoutes = [...normalized.fallbackRoutes];
  const [candidate] = fallbackRoutes.splice(currentIndex, 1);
  fallbackRoutes.splice(nextIndex, 0, candidate!);
  return { ...normalized, fallbackRoutes };
}

export function saveLocalModelProfile(
  profiles: readonly ModelProfileView[],
  draft: ModelProfileDraft,
  editingProfileId?: string,
): { readonly profiles: readonly ModelProfileView[]; readonly profile: ModelProfileView; readonly previousId?: string } {
  const id = profileIdForDraft(draft);
  if (!draft.provider.trim() || !draft.model.trim()) {
    throw new Error("请填写供应商与模型 ID。");
  }
  if (profiles.some((profile) => profile.id === id && profile.id !== editingProfileId)) {
    throw new Error(`模型 ${id} 已在当前会话中。`);
  }
  const profile: ModelProfileView = {
    id,
    label: draft.label.trim() || `${draft.provider.trim()} · ${draft.model.trim()}`,
    provider: draft.provider.trim(),
    model: draft.model.trim(),
    tierLabel: draft.tierLabel,
    statusLabel: "本地草案",
    supportsThinking: draft.supportsThinking,
    supportsMultiTurn: draft.supportsMultiTurn,
    isEmbedding: /(?:^|[-_/])(?:embedding|embed)(?:[-_/]|$)/i.test(draft.model),
  };
  const nextProfiles = editingProfileId === undefined
    ? [...profiles, profile]
    : profiles.map((current) => current.id === editingProfileId ? profile : current);
  return editingProfileId === undefined
    ? { profiles: nextProfiles, profile }
    : { profiles: nextProfiles, profile, previousId: editingProfileId };
}

export function replaceProfileInRouteDrafts(
  drafts: Readonly<Record<string, RouteDraft>>,
  previousId: string,
  profile: ModelProfileView,
  profiles: readonly ModelProfileView[],
  routeSupportsMultiTurnById: Readonly<Record<string, boolean>> = {},
): Readonly<Record<string, RouteDraft>> {
  return Object.fromEntries(Object.entries(drafts).map(([routeId, draft]) => [routeId, sanitizeRouteDraft({
    ...draft,
    primaryProfileId: draft.primaryProfileId === previousId ? profile.id : draft.primaryProfileId,
    fallbackRoutes: draft.fallbackRoutes.map((candidate) => candidate.profileId === previousId ? { ...candidate, profileId: profile.id } : candidate),
  }, profiles, routeSupportsMultiTurnById[routeId] ?? true)]));
}

export function removeLocalModelProfile(
  profiles: readonly ModelProfileView[],
  drafts: Readonly<Record<string, RouteDraft>>,
  profileId: string,
  routeSupportsMultiTurnById: Readonly<Record<string, boolean>> = {},
  defaultProfileId = "",
): { readonly profiles: readonly ModelProfileView[]; readonly drafts: Readonly<Record<string, RouteDraft>>; readonly defaultProfileId: string } {
  const nextProfiles = profiles.filter((profile) => profile.id !== profileId);
  const replacement = nextProfiles.find(isRouteableProfile) ?? nextProfiles[0];
  const nextDrafts = Object.fromEntries(Object.entries(drafts).map(([routeId, draft]) => {
    const next = {
      ...draft,
      primaryProfileId: draft.primaryProfileId === profileId ? replacement?.id ?? "" : draft.primaryProfileId,
      fallbackRoutes: draft.fallbackRoutes.filter((candidate) => candidate.profileId !== profileId),
    };
    return [routeId, sanitizeRouteDraft(next, nextProfiles, routeSupportsMultiTurnById[routeId] ?? true)];
  }));
  return {
    profiles: nextProfiles,
    drafts: nextDrafts,
    defaultProfileId: normalizeDefaultProfileId(
      defaultProfileId === profileId ? replacement?.id ?? "" : defaultProfileId,
      nextProfiles,
    ),
  };
}

/**
 * Apply explicitly selected route fields and an optional shared temperature.
 * A blank primary route is a temperature-only batch edit, so existing task
 * routing remains intact. Routes without temperature support stay unchanged.
 */
export function applyGroupRouteDraft(
  drafts: Readonly<Record<string, RouteDraft>>,
  routeIds: readonly string[],
  draft: RouteDraft,
  profiles: readonly ModelProfileView[],
  routeSupportsMultiTurnById: Readonly<Record<string, boolean>> = {},
): Readonly<Record<string, RouteDraft>> {
  const routeIdSet = new Set(routeIds);
  const appliesRouting = draft.primaryProfileId.length > 0;
  return Object.fromEntries(Object.entries(drafts).map(([routeId, current]) => [
    routeId,
    !routeIdSet.has(routeId)
      ? current
      : sanitizeRouteDraft({
        ...(appliesRouting ? draft : current),
        temperature: draft.temperature === null || current.temperature === null
          ? current.temperature
          : draft.temperature,
      }, profiles, routeSupportsMultiTurnById[routeId] ?? true),
  ]));
}
