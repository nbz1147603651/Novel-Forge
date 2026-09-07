import { useCallback, useEffect, useRef, useState } from "react";

import type {
  CharacterDetailView,
  CharacterProfileView,
  EngineCommandClient,
  NarrativeCharacterProfileInput,
  NarrativeToolsView,
  RelationshipLinkView,
} from "@nimo/engine-contracts";

import { NarrativeTimelineRenderer } from "./NarrativeVisualization";
import { useAuthoring } from "./AuthoringWorkspace";
import { CharacterWorkbench } from "./narrative-tools/CharacterWorkbench";
import { SourceCharacterGraphWorkbench } from "./narrative-tools/GraphWorkbench";
import { HumanizeWorkbench } from "./narrative-tools/HumanizeWorkbench";
import { OutlineWorkbench, SubplotWorkbench } from "./narrative-tools/OutlineSubplotWorkbench";
import { RelationshipWorkbench, SourceCharacterRelationshipWorkbench } from "./narrative-tools/RelationshipWorkbench";
import { RevisionWorkbench } from "./narrative-tools/RevisionWorkbench";
import { createNarrativeToolsSession, type NarrativeToolsSession } from "../lib/narrative-tools-session";
import type { ReaderUnsavedSessionChange } from "../lib/reader-unsaved-session";
import { action, tabLabels, type NarrativeToolsAction, type NarrativeToolTab } from "./narrative-tools/types";
export { action, asRelationshipRows, tabLabels } from "./narrative-tools/types";
export type { NarrativeToolsAction, NarrativeToolTab, RelationshipFilter, RelationshipViewMode, CharacterSessionDetail, EditableCharacterField } from "./narrative-tools/types";

type NarrativeSessionMutation =
  | { readonly kind: "create_character"; readonly character: CharacterProfileView; readonly detail?: CharacterDetailView }
  | { readonly kind: "save_character"; readonly character: CharacterProfileView; readonly detail: CharacterDetailView }
  | { readonly kind: "retire_character"; readonly characterId: string }
  | { readonly kind: "save_relationship"; readonly relationship: RelationshipLinkView }
  | { readonly kind: "remove_relationship"; readonly relationship: RelationshipLinkView };

const canonicalRole = (value: string): string => {
  const labels: Readonly<Record<string, string>> = {
    "主角": "protagonist",
    "重要配角": "deuteragonist",
    "对手": "antagonist",
    "配角": "supporting",
    "次要角色": "minor",
  };
  return labels[value] ?? value;
};

const canonicalTimeLayer = (value: string): string => {
  const labels: Readonly<Record<string, string>> = {
    "当前线": "default",
    "历史线": "past",
    "跨时空": "cross_temporal",
    "回忆线": "memory_only",
  };
  return labels[value] ?? "default";
};

function profileFromSession(
  character: CharacterProfileView,
  detail: CharacterDetailView | undefined,
  create: boolean,
): NarrativeCharacterProfileInput {
  if (create) {
    return {
      name: character.name,
      role: canonicalRole(character.role),
      age: detail?.ageLabel ?? "",
      gender: detail?.genderLabel ?? "",
      status: "active",
      timeLayer: "default",
    };
  }
  return {
    age: detail?.ageLabel ?? "",
    gender: detail?.genderLabel ?? "",
    timeLayer: canonicalTimeLayer(detail?.timelineLabel ?? "default"),
    socialStatus: detail?.occupation ?? "",
    personality: detail?.personality ?? "",
    backstory: detail?.backstory ?? "",
    abilities: detail?.abilities ?? "",
    appearance: detail?.appearance ?? "",
    arc: detail?.arc ?? character.arc,
    voice: detail?.voice ?? "",
    notes: detail?.notes ?? "",
  };
}

export function findNarrativeSessionMutation(
  current: NarrativeToolsSession,
  next: NarrativeToolsSession,
): NarrativeSessionMutation | null {
  const currentCharacters = new Map(current.characters.map((character) => [character.id, character]));
  const nextDetails = new Map(next.characterDetails.map((detail) => [detail.characterId, detail]));
  const added = next.characters.find((character) => !currentCharacters.has(character.id));
  if (added !== undefined) {
    const detail = nextDetails.get(added.id);
    return detail === undefined
      ? { kind: "create_character", character: added }
      : { kind: "create_character", character: added, detail };
  }
  const retired = next.characters.find((character) => currentCharacters.get(character.id)?.statusLabel !== "退场" && character.statusLabel === "退场");
  if (retired !== undefined) return { kind: "retire_character", characterId: retired.id };

  const currentDetails = new Map(current.characterDetails.map((detail) => [detail.characterId, detail]));
  const changedDetail = next.characterDetails.find((detail) => JSON.stringify(currentDetails.get(detail.characterId)) !== JSON.stringify(detail));
  if (changedDetail !== undefined) {
    const character = next.characters.find((candidate) => candidate.id === changedDetail.characterId);
    if (character !== undefined) return { kind: "save_character", character, detail: changedDetail };
  }

  const currentRelationships = new Map(current.relationships.map((relationship) => [relationship.id, relationship]));
  const savedRelationship = next.relationships.find((relationship) => JSON.stringify(currentRelationships.get(relationship.id)) !== JSON.stringify(relationship));
  if (savedRelationship !== undefined) return { kind: "save_relationship", relationship: savedRelationship };
  const removedRelationship = current.relationships.find((relationship) => !next.relationships.some((candidate) => candidate.id === relationship.id));
  return removedRelationship === undefined ? null : { kind: "remove_relationship", relationship: removedRelationship };
}

interface NarrativeToolsWorkbenchProps {
  readonly commandClient?: EngineCommandClient | undefined;
  readonly tools: NarrativeToolsView;
  readonly onAction?: (action: NarrativeToolsAction) => void;
  readonly onUnsavedSessionChange?: ReaderUnsavedSessionChange;
  readonly hideTabs?: boolean;
  readonly initialTab?: NarrativeToolTab;
  readonly labels?: Partial<Record<NarrativeToolTab, string>>;
  readonly visibleTabs?: readonly NarrativeToolTab[];
  readonly sourceCharacterEditor?: boolean;
  readonly relationshipFocusedCharacterId?: string | null;
  readonly showNotice?: boolean;
  readonly projectId?: string | undefined;
  readonly onNarrativeToolsRefresh?: (() => void) | undefined;
}

/**
 * 叙事工具工作台（角色/关系/图谱/大纲/支线/拟人化/修订）。
 * 各 tab 工作台拆分在 narrative-tools/ 子目录，本组件仅负责 tab 编排
 * 与共享会话状态。
 */
export function NarrativeToolsWorkbench({ commandClient, hideTabs = false, initialTab = "characters", labels, onAction, onNarrativeToolsRefresh, onUnsavedSessionChange, projectId, relationshipFocusedCharacterId, showNotice = true, sourceCharacterEditor = false, tools, visibleTabs }: NarrativeToolsWorkbenchProps) {
  const authoring = useAuthoring();
  const [tab, setTab] = useState<NarrativeToolTab>(initialTab);
  const [requestedCharacterId, setRequestedCharacterId] = useState<string | null>(null);
  const [sourceSelectedCharacterId, setSourceSelectedCharacterId] = useState(tools.characters[0]?.id ?? "");
  const [notice, setNotice] = useState("选择一项工作台工具以查看当前项目的只读视图。");
  const [session, setSession] = useState<NarrativeToolsSession>(() => createNarrativeToolsSession(tools));
  const characterRevisionRef = useRef(tools.characterRevision ?? "");
  const characterMutationPendingRef = useRef(false);
  const awaitingCharacterRefreshRef = useRef(false);
  const tabs = visibleTabs ?? (Object.keys(tabLabels) as NarrativeToolTab[]);
  const resolvedLabels = { ...tabLabels, ...labels };
  const relationshipFilterProp = relationshipFocusedCharacterId === undefined ? {} : { externalFocusedCharacterId: relationshipFocusedCharacterId };
  const optionalUnsavedSession = onUnsavedSessionChange === undefined ? {} : { onUnsavedSessionChange };
  const report = useCallback((nextAction: NarrativeToolsAction) => {
    setNotice(nextAction.message);
    onAction?.(nextAction);
  }, [onAction]);

  useEffect(() => {
    setSession(createNarrativeToolsSession(tools));
    characterRevisionRef.current = tools.characterRevision ?? "";
    awaitingCharacterRefreshRef.current = false;
  }, [tools]);
  useEffect(() => {
    setSourceSelectedCharacterId((current) => tools.characters.some((character) => character.id === current)
      ? current
      : tools.characters[0]?.id ?? "");
  }, [tools.characters]);

  const persistNarrativeSession = useCallback((next: NarrativeToolsSession) => {
    const mutation = findNarrativeSessionMutation(session, next);
    if (mutation === null) {
      setSession(next);
      return;
    }
    if (commandClient === undefined || projectId === undefined || characterRevisionRef.current.length === 0) {
      report(action("character-local-saved", "当前 Engine 未提供角色产物版本，已拒绝写入；请刷新项目后重试。"));
      return;
    }
    if (characterMutationPendingRef.current || awaitingCharacterRefreshRef.current) {
      report(action("character-local-saved", "上一项角色变更仍在同步权威读模型，请稍候。"));
      return;
    }

    setSession(next);
    characterMutationPendingRef.current = true;
    const expectedRevision = characterRevisionRef.current;
    const request = async () => {
      switch (mutation.kind) {
        case "create_character":
          return commandClient.saveNarrativeCharacter({
            kind: "save_narrative_character",
            projectId,
            profile: profileFromSession(mutation.character, mutation.detail, true),
            expectedRevision,
          });
        case "save_character":
          return commandClient.saveNarrativeCharacter({
            kind: "save_narrative_character",
            projectId,
            characterId: mutation.character.id,
            profile: profileFromSession(mutation.character, mutation.detail, false),
            expectedRevision,
          });
        case "retire_character":
          return commandClient.retireNarrativeCharacter({
            kind: "retire_narrative_character",
            projectId,
            characterId: mutation.characterId,
            expectedRevision,
          });
        case "save_relationship":
          return commandClient.saveNarrativeRelationship({
            kind: "save_narrative_relationship",
            projectId,
            sourceCharacterId: mutation.relationship.fromCharacterId,
            targetCharacterId: mutation.relationship.toCharacterId,
            relationType: mutation.relationship.typeLabel,
            description: mutation.relationship.evidence,
            expectedRevision,
          });
        case "remove_relationship":
          return commandClient.removeNarrativeRelationship({
            kind: "remove_narrative_relationship",
            projectId,
            sourceCharacterId: mutation.relationship.fromCharacterId,
            targetCharacterId: mutation.relationship.toCharacterId,
            expectedRevision,
          });
      }
    };
    void request().then((result) => {
      if (result.status === "candidate") {
        setSession(session);
        report(action("character-local-saved", result.message));
        authoring?.open();
        return;
      }
      if (result.status !== "saved") {
        setSession(session);
        report(action("character-local-saved", result.message));
        return;
      }
      characterRevisionRef.current = result.characterRevision ?? expectedRevision;
      awaitingCharacterRefreshRef.current = true;
      report(action("character-local-saved", result.message));
      onNarrativeToolsRefresh?.();
    }).catch((error: unknown) => {
      setSession(session);
      report(action("character-local-saved", error instanceof Error ? error.message : "角色变更提交失败。"));
    }).finally(() => {
      characterMutationPendingRef.current = false;
    });
  }, [authoring, commandClient, onNarrativeToolsRefresh, projectId, report, session]);

  return (
    <div className="narrative-tools">
      {!hideTabs && <div aria-label="叙事工具" className="narrative-tools-tabs" role="tablist">
        {tabs.map((key) => (
          <button
            aria-selected={tab === key}
            className={tab === key ? "is-active" : ""}
            key={key}
            onClick={() => setTab(key)}
            role="tab"
            type="button"
          >
            {resolvedLabels[key]}
          </button>
        ))}
      </div>}

      {tab === "timeline" && <NarrativeTimelineRenderer visualization={tools.visualization} />}
      {tab === "characters" && <CharacterWorkbench onAction={report} onSessionChange={persistNarrativeSession} requestedCharacterId={sourceCharacterEditor ? sourceSelectedCharacterId : requestedCharacterId} session={session} {...(sourceCharacterEditor ? { onSourceCharacterSelected: setSourceSelectedCharacterId } : {})} {...optionalUnsavedSession} />}
      {tab === "relationships" && (sourceCharacterEditor
        ? <SourceCharacterRelationshipWorkbench onAction={report} onEditCharacter={(character) => {
          setRequestedCharacterId(character.id);
          setSourceSelectedCharacterId(character.id);
          setTab("characters");
        }} onSessionChange={persistNarrativeSession} onSelectCharacter={setSourceSelectedCharacterId} selectedCharacterId={sourceSelectedCharacterId} session={session} />
        : <RelationshipWorkbench onAction={report} onSessionChange={persistNarrativeSession} session={session} {...relationshipFilterProp} />)}
      {tab === "graph" && <SourceCharacterGraphWorkbench onAction={report} onEditCharacter={(character) => {
        setRequestedCharacterId(character.id);
        setSourceSelectedCharacterId(character.id);
        setTab("characters");
      }} onSelectCharacter={setSourceSelectedCharacterId} onSessionChange={persistNarrativeSession} selectedCharacterId={sourceSelectedCharacterId} session={session} />}
      {tab === "outline" && <OutlineWorkbench commandClient={commandClient} onAction={report} onRefresh={onNarrativeToolsRefresh} outline={tools.outline} projectId={projectId} />}
      {tab === "subplots" && <SubplotWorkbench
        blueprintRevision={tools.blueprintRevision}
        commandClient={commandClient}
        onAction={report}
        onRefresh={onNarrativeToolsRefresh}
        projectId={projectId}
        sourceSubplots={tools.subplots}
        totalChapters={tools.visualization.totalChapters}
        visualization={tools.visualization}
      />}
      {tab === "humanize" && <HumanizeWorkbench
        commandClient={commandClient}
        humanizeLibraryRevision={tools.humanizeLibraryRevision}
        onAction={report}
        onRefresh={onNarrativeToolsRefresh}
        patterns={tools.humanizePatterns}
        projectId={projectId}
      />}
      {tab === "revision" && <RevisionWorkbench candidates={tools.revisionCandidates} onAction={report} />}

      {showNotice && <p aria-live="polite" className="narrative-tools-notice">{notice}</p>}
    </div>
  );
}
