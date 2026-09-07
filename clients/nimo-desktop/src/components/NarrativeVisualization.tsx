import {
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type MouseEvent as ReactMouseEvent,
  type PointerEvent,
} from "react";
import { createPortal } from "react-dom";

import { AppDialog } from "./AppDialog";

import type {
  CharacterDetailView,
  CharacterProfileView,
  NarrativeSubplotLaneView,
  NarrativeVisualizationView,
  RelationshipLinkView,
} from "@nimo/engine-contracts";

import {
  chapterToTimelineX,
  layoutRelationshipNodes,
  timelineArcGap,
  timelineArcLaneStartY,
  timelineChapterBadgeLayout,
  timelineChapterWidth,
  timelineContentHeight,
  timelineHorizontalLayout,
  timelineLaneGap,
  timelineLaneStartY,
  timelineTickChapters,
  timelineVerticalLayout,
} from "../lib/narrative-visualization-layout";

type RelationshipTone = "ally" | "bond" | "identity" | "neutral" | "tension";

function relationshipTone(label: string): RelationshipTone {
  // Keep the same label-driven mapping as CharacterGraphWidget.  Relationship
  // labels are presentation data in the Engine view; no UI-only category is
  // invented here.
  if (["敌", "仇", "冲突", "对立", "背叛", "威胁", "紧张", "压迫", "利用", "竞争"].some((token) => label.includes(token))) return "tension";
  if (["恋", "爱", "吸引", "暧昧", "亲密", "羁绊", "家人", "血缘", "亲情"].some((token) => label.includes(token))) return "bond";
  if (["同盟", "盟友", "合作", "朋友", "挚友", "师徒", "信任", "保护", "支持", "伙伴"].some((token) => label.includes(token))) return "ally";
  if (["转世", "别名", "身份", "误认"].some((token) => label.includes(token))) return "identity";
  return "neutral";
}

function nodeRole(character: CharacterProfileView): string {
  if (character.role.includes("第二主角")) return "第二主角";
  if (character.role.includes("主视角") || character.role.includes("主角")) return "主角";
  if (character.role.includes("对手")) return "对手";
  if (character.role.includes("重要")) return "重要配角";
  return "配角";
}

function characterTone(character: CharacterProfileView): "protagonist" | "deuteragonist" | "antagonist" | "supporting" | "minor" {
  if (character.role.includes("第二主角")) return "deuteragonist";
  if (character.role.includes("主视角") || character.role.includes("主角")) return "protagonist";
  if (character.role.includes("对手") || character.role.includes("反派")) return "antagonist";
  if (character.role.includes("重要") || character.role.includes("配角")) return "supporting";
  return "minor";
}

function keyActivates(event: KeyboardEvent<SVGElement>, action: () => void) {
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    action();
  }
}

type GraphTooltip =
  | {
    readonly character: CharacterProfileView;
    readonly detail: CharacterDetailView | undefined;
    readonly kind: "character";
    readonly size: GraphTooltipSize;
    readonly relations: readonly { readonly label: string; readonly name: string }[];
    readonly anchorX: number;
    readonly anchorY: number;
    readonly x: number;
    readonly y: number;
  }
  | {
    readonly fromName: string;
    readonly kind: "relationship";
    readonly link: RelationshipLinkView;
    readonly toName: string;
    readonly size: GraphTooltipSize;
    readonly anchorX: number;
    readonly anchorY: number;
    readonly x: number;
    readonly y: number;
  };

interface GraphTooltipSize {
  readonly height: number;
  readonly width: number;
}

interface FloatingTooltipAnchor {
  readonly anchorX: number;
  readonly anchorY: number;
  readonly x: number;
  readonly y: number;
}

// Hover is for orientation, not for reading a complete character dossier.
// Full details stay behind the explicit “查看详情” action so a dense graph is
// never obscured by an oversized floating document.
const characterTooltipSize: GraphTooltipSize = { height: 182, width: 282 };
const relationshipTooltipSize: GraphTooltipSize = { height: 92, width: 232 };

function truncateGraphTooltipText(value: string, limit: number): string {
  if (value.length <= limit) return value;
  return `${value.slice(0, Math.max(0, limit - 1)).trimEnd()}…`;
}

function formatGraphAge(value: string | undefined): string {
  const raw = value?.trim() ?? "";
  if (raw.length === 0) return "";
  return /^\d+(?:岁|歲)?$/u.test(raw) ? `${raw.replace(/[岁歲]/gu, "")}岁` : raw;
}

function compactGraphLabel(value: string, radius: number): string {
  // A node name belongs to the node, not to the page's overflow area.  Keep a
  // short, stable label in the graph and expose the full name through title,
  // aria-label, and the detail dialog.
  return compactTimelineLabel(value, Math.max(3, Math.floor(radius / 7)));
}

function tooltipPosition(
  event: PointerEvent<SVGGElement>,
  size: GraphTooltipSize,
): { readonly anchorX: number; readonly anchorY: number; readonly x: number; readonly y: number } | null {
  if (typeof window === "undefined") return null;
  const { height, width } = size;
  const viewportWidth = document.documentElement.clientWidth || window.innerWidth;
  const viewportHeight = document.documentElement.clientHeight || window.innerHeight;
  const min = 8;
  const anchorX = event.clientX;
  const anchorY = event.clientY;
  let x = anchorX + 12;
  let y = anchorY + 16;
  if (x > viewportWidth - width - min) x = anchorX - width - 12;
  if (y > viewportHeight - height - min) y = anchorY - height - 16;
  return {
    anchorX,
    anchorY,
    x: Math.max(min, Math.min(x, Math.max(min, viewportWidth - width - min))),
    y: Math.max(min, Math.min(y, Math.max(min, viewportHeight - height - min))),
  };
}

function useFloatingTooltipPosition(tooltip: FloatingTooltipAnchor) {
  const tooltipRef = useRef<HTMLElement>(null);
  const [position, setPosition] = useState(() => ({ x: tooltip.x, y: tooltip.y }));

  useLayoutEffect(() => {
    if (typeof document === "undefined") return;
    const element = tooltipRef.current;
    if (element === null) return;
    const { height, width } = element.getBoundingClientRect();
    const viewportWidth = document.documentElement.clientWidth || window.innerWidth;
    const viewportHeight = document.documentElement.clientHeight || window.innerHeight;
    const min = 8;
    let x = tooltip.anchorX + 12;
    let y = tooltip.anchorY + 16;
    if (x > viewportWidth - width - min) x = tooltip.anchorX - width - 12;
    if (y > viewportHeight - height - min) y = tooltip.anchorY - height - 16;
    const next = {
      x: Math.max(min, Math.min(x, Math.max(min, viewportWidth - width - min))),
      y: Math.max(min, Math.min(y, Math.max(min, viewportHeight - height - min))),
    };
    setPosition((current) => current.x === next.x && current.y === next.y ? current : next);
  }, [tooltip]);

  return { position, tooltipRef };
}

function relationshipToneLabel(label: string): string {
  return {
    ally: "同盟/信任",
    bond: "亲缘/情感",
    identity: "身份/指代",
    neutral: "关系",
    tension: "冲突/对抗",
  }[relationshipTone(label)];
}

function GraphTooltipSurface({ tooltip }: { readonly tooltip: GraphTooltip }) {
  const { position, tooltipRef } = useFloatingTooltipPosition(tooltip);

  const surface = tooltip.kind === "character"
    ? <aside aria-live="polite" className="relationship-graph-tooltip is-character" ref={tooltipRef} style={{ left: position.x, top: position.y }}>
      <strong>{tooltip.character.name}</strong>
      <b>{nodeRole(tooltip.character)}{formatGraphAge(tooltip.detail?.ageLabel).length ? ` · ${formatGraphAge(tooltip.detail?.ageLabel)}` : ""}</b>
      {tooltip.detail?.personality.length ? <p><span>性格</span>：{truncateGraphTooltipText(tooltip.detail.personality, 54)}</p> : tooltip.character.summary.length ? <p><span>摘要</span>：{truncateGraphTooltipText(tooltip.character.summary, 54)}</p> : null}
      {tooltip.detail?.arc.length || tooltip.character.arc.length ? <p><span>弧光</span>：{truncateGraphTooltipText(tooltip.detail?.arc || tooltip.character.arc, 54)}</p> : null}
      {tooltip.relations.length > 0 && <div className="relationship-graph-tooltip-relations"><span>关联</span>{tooltip.relations.map((relation) => <p key={`${relation.name}-${relation.label}`}>→ {relation.name}：{truncateGraphTooltipText(relation.label, 28)}</p>)}</div>}
    </aside>
    : <aside aria-live="polite" className="relationship-graph-tooltip is-relationship" ref={tooltipRef} style={{ left: position.x, top: position.y }}>
      <strong>{tooltip.fromName} → {tooltip.toName}</strong>
      <b>{relationshipToneLabel(tooltip.link.typeLabel)} · 图层：relationship</b>
      <p>{truncateGraphTooltipText(tooltip.link.typeLabel, 96)}</p>
    </aside>;

  return typeof document === "undefined" ? surface : createPortal(surface, document.body);
}

const timelineTooltipSize: GraphTooltipSize = { height: 116, width: 304 };

function TimelineTooltipSurface({ tooltip }: { readonly tooltip: TimelineTooltip }) {
  const { position, tooltipRef } = useFloatingTooltipPosition(tooltip);
  const surface = <aside aria-label="叙事节点悬浮信息" aria-live="polite" className="narrative-timeline-tooltip" ref={tooltipRef} style={{ left: position.x, top: position.y }}>
    <strong>{tooltip.title}</strong>
    <span>{tooltip.meta}</span>
    <p>{tooltip.description}</p>
  </aside>;
  return typeof document === "undefined" ? surface : createPortal(surface, document.body);
}

interface RelationshipGraphRendererProps {
  readonly characters: readonly CharacterProfileView[];
  readonly characterDetails?: readonly CharacterDetailView[];
  readonly focusedCharacterId: string | null;
  readonly links: readonly RelationshipLinkView[];
  /** ProjectsPage.CharacterBibleEditor uses an uncluttered graph canvas. */
  readonly sourceFrame?: boolean;
  /** Enables the editable CharacterGraphWidget interaction contract. */
  readonly editable?: boolean;
  readonly onCreateCharacter?: () => void;
  readonly onCreateRelationship?: (source: CharacterProfileView, target: CharacterProfileView) => void;
  readonly onClearFocus?: () => void;
  readonly onEditCharacter?: (character: CharacterProfileView) => void;
  readonly onEditRelationship?: (link: RelationshipLinkView) => void;
  readonly onFocusLink?: (link: RelationshipLinkView) => void;
  readonly onFocusCharacter: (character: CharacterProfileView) => void;
  readonly onRemoveRelationship?: (link: RelationshipLinkView) => void;
  readonly onRetireCharacter?: (character: CharacterProfileView) => void;
  readonly selectedLinkId?: string | null;
}

interface GraphPoint {
  readonly x: number;
  readonly y: number;
}

interface GraphDragState {
  readonly pointer: GraphPoint;
  readonly sourceCharacterId: string;
  readonly targetCharacterId: string | null;
}

type GraphContextMenu =
  | { readonly character: CharacterProfileView; readonly kind: "node"; readonly x: number; readonly y: number }
  | { readonly kind: "edge"; readonly link: RelationshipLinkView; readonly x: number; readonly y: number }
  | { readonly kind: "canvas"; readonly x: number; readonly y: number };

function graphPointFromPointer(svg: SVGSVGElement | null, event: { readonly clientX: number; readonly clientY: number }): GraphPoint | null {
  if (svg === null) return null;
  const rect = svg.getBoundingClientRect();
  const viewBox = svg.viewBox.baseVal;
  if (rect.width < 1 || rect.height < 1 || viewBox.width < 1 || viewBox.height < 1) return null;
  return {
    x: viewBox.x + ((event.clientX - rect.left) / rect.width) * viewBox.width,
    y: viewBox.y + ((event.clientY - rect.top) / rect.height) * viewBox.height,
  };
}

function isNodeEdgeAnchor(point: GraphPoint, node: { readonly radius: number; readonly x: number; readonly y: number }): boolean {
  const distance = Math.hypot(point.x - node.x, point.y - node.y);
  return distance >= node.radius - 8 && distance <= node.radius + 10;
}

function pointNearRelationshipSegment(
  point: GraphPoint,
  from: { readonly x: number; readonly y: number },
  to: { readonly x: number; readonly y: number },
  threshold = 10,
): boolean {
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  const lengthSquared = dx * dx + dy * dy;
  if (lengthSquared < 1) return false;
  const ratio = Math.max(0, Math.min(1, ((point.x - from.x) * dx + (point.y - from.y) * dy) / lengthSquared));
  return Math.hypot(point.x - (from.x + ratio * dx), point.y - (from.y + ratio * dy)) <= threshold;
}

function GraphContextMenuSurface({ menu, onClose, onCreateCharacter, onEditCharacter, onEditRelationship, onRemoveRelationship, onRetireCharacter }: {
  readonly menu: GraphContextMenu;
  readonly onClose: () => void;
  readonly onCreateCharacter?: () => void;
  readonly onEditCharacter?: (character: CharacterProfileView) => void;
  readonly onEditRelationship?: (link: RelationshipLinkView) => void;
  readonly onRemoveRelationship?: (link: RelationshipLinkView) => void;
  readonly onRetireCharacter?: (character: CharacterProfileView) => void;
}) {
  const menuRef = useRef<HTMLDivElement>(null);
  const closeAnd = (action?: () => void) => () => {
    action?.();
    onClose();
  };
  useEffect(() => {
    const dismiss = (event: globalThis.PointerEvent) => {
      if (menuRef.current?.contains(event.target as Node)) return;
      onClose();
    };
    const dismissOnEscape = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("pointerdown", dismiss);
    window.addEventListener("keydown", dismissOnEscape);
    return () => {
      window.removeEventListener("pointerdown", dismiss);
      window.removeEventListener("keydown", dismissOnEscape);
    };
  }, [onClose]);
  if (typeof document === "undefined") return null;
  const left = Math.max(8, Math.min(menu.x, window.innerWidth - 178));
  const top = Math.max(8, Math.min(menu.y, window.innerHeight - (menu.kind === "node" ? 148 : 82)));
  return createPortal(
    <div aria-label="角色图谱操作菜单" className="relationship-graph-context-menu" ref={menuRef} role="menu" style={{ left, top }}>
      {menu.kind === "node" && <>
        <button onClick={closeAnd(() => onEditCharacter?.(menu.character))} role="menuitem" type="button">编辑角色</button>
        <button onClick={closeAnd(() => onRetireCharacter?.(menu.character))} role="menuitem" type="button">标记退场</button>
        <span role="separator" />
        <button onClick={closeAnd(onCreateCharacter)} role="menuitem" type="button">新增角色</button>
      </>}
      {menu.kind === "edge" && <>
        <button onClick={closeAnd(() => onEditRelationship?.(menu.link))} role="menuitem" type="button">编辑关系</button>
        <button onClick={closeAnd(() => onRemoveRelationship?.(menu.link))} role="menuitem" type="button">移除关系</button>
      </>}
      {menu.kind === "canvas" && <button onClick={closeAnd(onCreateCharacter)} role="menuitem" type="button">新增角色</button>}
    </div>,
    document.body,
  );
}

/**
 * An SVG relationship renderer with deterministic geometry.  Its stateful
 * controls stay outside the layout core, so the same visual contract can be
 * served later by local or cloud EngineClient adapters.
 */
export function RelationshipGraphRenderer({ characters, characterDetails = [], editable = false, focusedCharacterId, links, onCreateCharacter, onCreateRelationship, onFocusCharacter, onClearFocus, onEditCharacter, onEditRelationship, onFocusLink, onRemoveRelationship, onRetireCharacter, selectedLinkId = null, sourceFrame = false }: RelationshipGraphRendererProps) {
  // Zoom bounds: practical range for graph visualization (50%–200%)
  const ZOOM_MIN = 0.5;
  const ZOOM_MAX = 2.0;
  const ZOOM_STEP = 0.1;
  // Pan constraint: content must remain at least this fraction visible in viewport
  const PAN_MARGIN_RATIO = 0.25;
  const [zoom, setZoom] = useState(1);
  const [panOffset, setPanOffset] = useState({ x: 0, y: 0 });
  const [isPanning, setIsPanning] = useState(false);
  const panStartRef = useRef<{ clientX: number; clientY: number; panX: number; panY: number } | null>(null);
  const hasAutoFitRef = useRef(false);
  const clampPanRef = useRef<(offset: { x: number; y: number }, z: number) => { x: number; y: number }>((o) => o);
  const [hoveredCharacterId, setHoveredCharacterId] = useState<string | null>(null);
  const [hoveredLinkId, setHoveredLinkId] = useState<string | null>(null);
  const [edgeAnchorCharacterId, setEdgeAnchorCharacterId] = useState<string | null>(null);
  const [dragState, setDragState] = useState<GraphDragState | null>(null);
  const [contextMenu, setContextMenu] = useState<GraphContextMenu | null>(null);
  const [tooltip, setTooltip] = useState<GraphTooltip | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const suppressClickRef = useRef(false);
  const [canvasSize, setCanvasSize] = useState(() => sourceFrame ? { width: 760, height: 500, fontSize: 12 } : { width: 920, height: 540, fontSize: 12 });

  // CharacterGraphWidget recomputes its radial geometry for its actual
  // widget size in every paint event.  Observe the SVG viewport for the same
  // behavior: the graph keeps its proportions in a narrow reader pane rather
  // than shrinking a fixed 900px scene into it.
  useEffect(() => {
    const svg = svgRef.current;
    if (svg === null) return undefined;
    const syncSize = () => {
      const { height, width } = svg.getBoundingClientRect();
      if (width < 1 || height < 1) return;
      const fontSize = Number.parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--nf-font-body")) || 12;
      setCanvasSize((current) => (
        Math.abs(current.width - width) < 0.5
        && Math.abs(current.height - height) < 0.5
        && Math.abs(current.fontSize - fontSize) < 0.1
          ? current
          : { width, height, fontSize }
      ));
    };
    syncSize();
    const resizeObserver = new ResizeObserver(syncSize);
    resizeObserver.observe(svg);
    // Settings applies the type ramp as root CSS variables. Observe that root
    // so a user can resize type without first having to resize the window to
    // get proportionate graph nodes.
    const typographyObserver = new MutationObserver(syncSize);
    typographyObserver.observe(document.documentElement, { attributes: true, attributeFilter: ["style"] });
    return () => {
      resizeObserver.disconnect();
      typographyObserver.disconnect();
    };
  }, [sourceFrame]);

  // The old source floor (600×450) made a short reader panel draw a virtual
  // scene larger than its own canvas.  Keep only a non-zero geometry floor;
  // layoutRelationshipNodes now receives the measured viewport and can fit
  // the visible graph without creating a hidden vertical scroll region.
  const graphWidth = Math.max(1, canvasSize.width);
  const graphHeight = Math.max(1, canvasSize.height);
  const positions = useMemo(
    () => layoutRelationshipNodes(characters, { width: graphWidth, height: graphHeight, fontSize: canvasSize.fontSize }),
    [canvasSize.fontSize, characters, graphHeight, graphWidth],
  );
  const positionById = useMemo(() => new Map(positions.map((position) => [position.characterId, position])), [positions]);
  const characterById = useMemo(() => new Map(characters.map((character) => [character.id, character])), [characters]);
  const detailByCharacterId = useMemo(() => new Map(characterDetails.map((detail) => [detail.characterId, detail])), [characterDetails]);
  const visibleLinks = links.filter((link) => positionById.has(link.fromCharacterId) && positionById.has(link.toCharacterId));
  const viewWidth = graphWidth / zoom;
  const viewHeight = graphHeight / zoom;
  const viewBox = `${graphWidth / 2 - viewWidth / 2 - panOffset.x} ${graphHeight / 2 - viewHeight / 2 - panOffset.y} ${viewWidth} ${viewHeight}`;
  const activeCharacterId = focusedCharacterId ?? hoveredCharacterId;
  const activeLinkId = selectedLinkId ?? hoveredLinkId;
  const hasFocus = activeCharacterId !== null || activeLinkId !== null;

  // Content bounding box (memoized) — used for pan clamping
  const contentBounds = useMemo(() => {
    if (positions.length === 0) return { minX: 0, maxX: graphWidth, minY: 0, maxY: graphHeight };
    const pad = 40;
    return {
      minX: Math.min(...positions.map((p) => p.x - p.radius)) - pad,
      maxX: Math.max(...positions.map((p) => p.x + p.radius)) + pad,
      minY: Math.min(...positions.map((p) => p.y - p.radius)) - pad,
      maxY: Math.max(...positions.map((p) => p.y + p.radius)) + pad,
    };
  }, [positions, graphWidth, graphHeight]);

  /**
   * Clamp pan offset so content bounding box always overlaps the visible
   * viewport by at least PAN_MARGIN_RATIO of the viewport dimension.
   * Inspired by d3-zoom translateExtent and react-flow viewport constraints.
   */
  const clampPan = (offset: { x: number; y: number }, currentZoom: number): { x: number; y: number } => {
    const vw = graphWidth / currentZoom;
    const vh = graphHeight / currentZoom;
    // The visible viewport center in graph coords:
    //   vcx = graphWidth/2 - offset.x,  vcy = graphHeight/2 - offset.y
    // Visible viewport edges:
    //   vLeft = vcx - vw/2,  vRight = vcx + vw/2
    // Constraint: content must overlap viewport by at least margin
    const marginX = vw * PAN_MARGIN_RATIO;
    const marginY = vh * PAN_MARGIN_RATIO;
    // vRight >= contentMinX + marginX  →  offset.x <= graphWidth/2 - contentMinX - marginX + vw/2 - vw/2
    // Simplified: offset.x <= graphWidth/2 - contentBounds.minX + vw/2 - marginX
    //             offset.x >= graphWidth/2 - contentBounds.maxX - vw/2 + marginX
    const maxPanX = graphWidth / 2 - contentBounds.minX + vw / 2 - marginX;
    const minPanX = graphWidth / 2 - contentBounds.maxX - vw / 2 + marginX;
    const maxPanY = graphHeight / 2 - contentBounds.minY + vh / 2 - marginY;
    const minPanY = graphHeight / 2 - contentBounds.maxY - vh / 2 + marginY;
    // If content is smaller than viewport, center it (min > max means clamp to center)
    const clampedX = minPanX > maxPanX
      ? (graphWidth / 2 - (contentBounds.minX + contentBounds.maxX) / 2)
      : Math.min(maxPanX, Math.max(minPanX, offset.x));
    const clampedY = minPanY > maxPanY
      ? (graphHeight / 2 - (contentBounds.minY + contentBounds.maxY) / 2)
      : Math.min(maxPanY, Math.max(minPanY, offset.y));
    return { x: clampedX, y: clampedY };
  };
  // Keep ref in sync so imperative wheel handler always uses latest closure
  clampPanRef.current = clampPan;

  // Zoom / pan helpers
  const zoomIn = () => setZoom((v) => {
    const next = Math.min(ZOOM_MAX, Number((v + ZOOM_STEP).toFixed(2)));
    setPanOffset((p) => clampPan(p, next));
    return next;
  });
  const zoomOut = () => setZoom((v) => {
    const next = Math.max(ZOOM_MIN, Number((v - ZOOM_STEP).toFixed(2)));
    setPanOffset((p) => clampPan(p, next));
    return next;
  });
  const resetView = () => { setZoom(1); setPanOffset({ x: 0, y: 0 }); };
  const fitAll = () => {
    if (positions.length === 0) return;
    const padding = 50;
    const minX = Math.min(...positions.map((p) => p.x - p.radius)) - padding;
    const maxX = Math.max(...positions.map((p) => p.x + p.radius)) + padding;
    const minY = Math.min(...positions.map((p) => p.y - p.radius)) - padding;
    const maxY = Math.max(...positions.map((p) => p.y + p.radius)) + padding;
    const contentW = maxX - minX;
    const contentH = maxY - minY;
    if (contentW < 1 || contentH < 1) return;
    const scaleX = graphWidth / contentW;
    const scaleY = graphHeight / contentH;
    const newZoom = Number(Math.min(scaleX, scaleY, ZOOM_MAX).toFixed(2));
    const clampedZoom = Math.max(ZOOM_MIN, newZoom);
    setZoom(clampedZoom);
    const centerX = (minX + maxX) / 2;
    const centerY = (minY + maxY) / 2;
    setPanOffset(clampPan({ x: graphWidth / 2 - centerX, y: graphHeight / 2 - centerY }, clampedZoom));
  };

  // Auto-fit on first render in sourceFrame mode
  useEffect(() => {
    if (sourceFrame && positions.length > 0 && !hasAutoFitRef.current) {
      hasAutoFitRef.current = true;
      fitAll();
    }
  }, [sourceFrame, positions.length > 0]);

  // Wheel zoom handler (imperative to allow preventDefault on passive listeners)
  useEffect(() => {
    const svg = svgRef.current;
    if (svg === null) return undefined;
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      const factor = event.deltaY > 0 ? 0.92 : 1.08;
      setZoom((v) => {
        const next = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, Number((v * factor).toFixed(2))));
        setPanOffset((p) => clampPanRef.current(p, next));
        return next;
      });
    };
    svg.addEventListener("wheel", onWheel, { passive: false });
    return () => svg.removeEventListener("wheel", onWheel);
  }, []);

  // Pan handlers with boundary clamping
  const startPan = (event: React.PointerEvent<SVGSVGElement>) => {
    panStartRef.current = { clientX: event.clientX, clientY: event.clientY, panX: panOffset.x, panY: panOffset.y };
    setIsPanning(true);
    svgRef.current?.setPointerCapture(event.pointerId);
  };
  const movePan = (event: React.PointerEvent<SVGSVGElement>) => {
    if (!isPanning || panStartRef.current === null) return;
    const svg = svgRef.current;
    if (svg === null) return;
    const rect = svg.getBoundingClientRect();
    const vb = svg.viewBox.baseVal;
    if (rect.width < 1 || rect.height < 1) return;
    const dx = ((event.clientX - panStartRef.current.clientX) / rect.width) * vb.width;
    const dy = ((event.clientY - panStartRef.current.clientY) / rect.height) * vb.height;
    setPanOffset(clampPan({ x: panStartRef.current.panX + dx, y: panStartRef.current.panY + dy }, zoom));
  };
  const endPan = (event: React.PointerEvent<SVGSVGElement>) => {
    if (!isPanning) return;
    setIsPanning(false);
    panStartRef.current = null;
    if (svgRef.current?.hasPointerCapture(event.pointerId)) svgRef.current.releasePointerCapture(event.pointerId);
  };

  const showCharacterTooltip = (character: CharacterProfileView, event: PointerEvent<SVGGElement>) => {
    if (!sourceFrame) return;
    const detail = detailByCharacterId.get(character.id);
    const position = tooltipPosition(event, characterTooltipSize);
    if (position === null) return;
    const relations = links
      .filter((link) => link.fromCharacterId === character.id || link.toCharacterId === character.id)
      .slice(0, 2)
      .map((link) => {
        const relatedId = link.fromCharacterId === character.id ? link.toCharacterId : link.fromCharacterId;
        return { label: link.typeLabel, name: characterById.get(relatedId)?.name ?? relatedId };
      });
    setTooltip({ character, detail, kind: "character", relations, size: characterTooltipSize, ...position });
  };

  const showRelationshipTooltip = (link: RelationshipLinkView, fromName: string, toName: string, event: PointerEvent<SVGGElement>) => {
    if (!sourceFrame) return;
    const position = tooltipPosition(event, relationshipTooltipSize);
    if (position === null) return;
    setTooltip({ fromName, kind: "relationship", link, size: relationshipTooltipSize, toName, ...position });
  };

  const dragTargetFor = (point: GraphPoint, sourceCharacterId: string): string | null => {
    let closest: { readonly distance: number; readonly id: string } | null = null;
    for (const position of positions) {
      if (position.characterId === sourceCharacterId) continue;
      const distance = Math.hypot(point.x - position.x, point.y - position.y);
      if (distance > Math.max(30, position.radius * 0.85)) continue;
      if (closest === null || distance < closest.distance) closest = { distance, id: position.characterId };
    }
    return closest?.id ?? null;
  };

  const startRelationshipDrag = (character: CharacterProfileView, point: GraphPoint, pointerId: number) => {
    const svg = svgRef.current;
    if (!editable || onCreateRelationship === undefined) return;
    svg?.setPointerCapture(pointerId);
    suppressClickRef.current = true;
    setContextMenu(null);
    setTooltip(null);
    setHoveredLinkId(null);
    setEdgeAnchorCharacterId(null);
    setDragState({ pointer: point, sourceCharacterId: character.id, targetCharacterId: null });
  };

  const finishRelationshipDrag = (pointerId: number) => {
    if (dragState === null) return;
    const source = characterById.get(dragState.sourceCharacterId);
    const target = dragState.targetCharacterId === null ? undefined : characterById.get(dragState.targetCharacterId);
    if (svgRef.current?.hasPointerCapture(pointerId)) svgRef.current.releasePointerCapture(pointerId);
    setDragState(null);
    if (source !== undefined && target !== undefined) onCreateRelationship?.(source, target);
  };

  const openContextMenu = (event: ReactMouseEvent<SVGSVGElement>) => {
    if (!editable) return;
    event.preventDefault();
    const point = graphPointFromPointer(svgRef.current, event);
    if (point === null) return;
    const character = characters.find((candidate) => {
      const position = positionById.get(candidate.id);
      return position !== undefined && Math.hypot(point.x - position.x, point.y - position.y) <= position.radius * 1.15;
    });
    if (character !== undefined) {
      setContextMenu({ character, kind: "node", x: event.clientX, y: event.clientY });
      return;
    }
    const link = visibleLinks.find((candidate) => {
      const from = positionById.get(candidate.fromCharacterId);
      const to = positionById.get(candidate.toCharacterId);
      return from !== undefined && to !== undefined && pointNearRelationshipSegment(point, from, to);
    });
    setContextMenu(link === undefined
      ? { kind: "canvas", x: event.clientX, y: event.clientY }
      : { kind: "edge", link, x: event.clientX, y: event.clientY });
  };

  const dragSource = dragState === null ? undefined : positionById.get(dragState.sourceCharacterId);
  const dragTarget = dragState?.targetCharacterId === null || dragState === null
    ? undefined
    : positionById.get(dragState.targetCharacterId);

  return (
    <section className={`relationship-graph-renderer${sourceFrame ? " is-source-frame" : ""}`} aria-label="角色关系图谱">
      {!sourceFrame && <header><span>关系图谱</span><div><button aria-label="缩小关系图" disabled={zoom <= ZOOM_MIN} onClick={zoomOut} type="button">−</button><button aria-label="放大关系图" disabled={zoom >= ZOOM_MAX} onClick={zoomIn} type="button">+</button><button onClick={fitAll} type="button">适应</button><button onClick={resetView} type="button">复位</button></div></header>}
      {sourceFrame && <div className="graph-zoom-controls"><button aria-label="缩小关系图" disabled={zoom <= ZOOM_MIN} onClick={zoomOut} type="button">−</button><span>{Math.round(zoom * 100)}%</span><button aria-label="放大关系图" disabled={zoom >= ZOOM_MAX} onClick={zoomIn} type="button">+</button><button aria-label="适应全部节点" onClick={fitAll} type="button">⊡</button><button aria-label="复位视图" onClick={resetView} type="button">↺</button></div>}
      <svg aria-label="可聚焦角色节点的关系网络" className={editable ? "is-relationship-editable" : undefined} onClick={(event) => {
        if (suppressClickRef.current) {
          suppressClickRef.current = false;
          return;
        }
        if (event.target === event.currentTarget) onClearFocus?.();
      }} onContextMenu={openContextMenu} onPointerDown={(event) => {
        // Middle button or left button on blank area starts panning
        if (event.button === 1 || (event.button === 0 && event.target === event.currentTarget)) {
          event.preventDefault();
          startPan(event);
        }
      }} onPointerCancel={(event) => {
        if (isPanning) { endPan(event); return; }
        if (dragState === null) return;
        if (svgRef.current?.hasPointerCapture(event.pointerId)) svgRef.current.releasePointerCapture(event.pointerId);
        setDragState(null);
      }} onPointerLeave={() => {
        if (dragState !== null) return;
        setHoveredCharacterId(null);
        setHoveredLinkId(null);
        setEdgeAnchorCharacterId(null);
        setTooltip(null);
      }} onPointerMove={(event) => {
        if (isPanning) { movePan(event); return; }
        if (dragState === null) return;
        const point = graphPointFromPointer(svgRef.current, event);
        if (point === null) return;
        setDragState((current) => current === null ? current : {
          ...current,
          pointer: point,
          targetCharacterId: dragTargetFor(point, current.sourceCharacterId),
        });
      }} onPointerUp={(event) => {
        if (isPanning) { endPan(event); return; }
        finishRelationshipDrag(event.pointerId);
      }} preserveAspectRatio="xMidYMid meet" ref={svgRef} role="img" style={isPanning ? { cursor: "grabbing" } : undefined} viewBox={viewBox}>
        <title>角色关系图谱</title>
        <defs>
          <pattern height="30" id="relationship-graph-grid" patternUnits="userSpaceOnUse" width="30"><path d="M 30 0 L 0 0 0 30" fill="none" stroke="currentColor" strokeOpacity=".055" strokeWidth="1" /></pattern>
          <radialGradient id="relationship-graph-glow"><stop offset="0" stopColor="currentColor" stopOpacity=".12" /><stop offset="1" stopColor="currentColor" stopOpacity="0" /></radialGradient>
        </defs>
        <rect className="relationship-graph-grid" fill="url(#relationship-graph-grid)" height={graphHeight} width={graphWidth} />
        <circle className="relationship-graph-center-glow" cx={graphWidth / 2} cy={graphHeight / 2} fill="url(#relationship-graph-glow)" r={Math.min(graphWidth, graphHeight) * .42} />
        {visibleLinks.map((link) => {
          const from = positionById.get(link.fromCharacterId)!;
          const to = positionById.get(link.toCharacterId)!;
          const dx = to.x - from.x;
          const dy = to.y - from.y;
          const distance = Math.hypot(dx, dy);
          if (distance < 1) return null;
          const ux = dx / distance;
          const uy = dy / distance;
          const startX = from.x + ux * from.radius;
          const startY = from.y + uy * from.radius;
          const endX = to.x - ux * to.radius;
          const endY = to.y - uy * to.radius;
          const isActive = activeLinkId === link.id || (activeLinkId === null && activeCharacterId !== null && (link.fromCharacterId === activeCharacterId || link.toCharacterId === activeCharacterId));
          const isSelected = link.id === selectedLinkId;
          const isMuted = hasFocus && !isActive;
          const className = `relationship-edge is-${relationshipTone(link.typeLabel)}${isSelected ? " is-selected" : ""}${isActive ? " is-active" : ""}${isMuted ? " is-muted" : ""}`;
          const fromName = characterById.get(link.fromCharacterId)?.name ?? link.fromCharacterId;
          const toName = characterById.get(link.toCharacterId)?.name ?? link.toCharacterId;
          return <g aria-label={`查看 ${link.typeLabel}：${fromName} 到 ${toName}`} className={className} key={link.id} onClick={(event) => {
            if (suppressClickRef.current) return;
            event.stopPropagation();
            onFocusLink?.(link);
          }} onKeyDown={(event) => keyActivates(event, () => onFocusLink?.(link))} onPointerEnter={(event) => {
            if (dragState !== null) return;
            setHoveredLinkId(link.id);
            showRelationshipTooltip(link, fromName, toName, event);
          }} onPointerLeave={() => {
            if (dragState !== null) return;
            setHoveredLinkId(null);
            setTooltip(null);
          }} onPointerMove={(event) => {
            if (dragState === null) showRelationshipTooltip(link, fromName, toName, event);
          }} role={onFocusLink === undefined ? undefined : "button"} tabIndex={onFocusLink === undefined ? undefined : 0}>
            <title>{`${link.typeLabel}：${fromName} 与 ${toName}`}</title>
            <line x1={startX} x2={endX} y1={startY} y2={endY} />
            {isActive && <circle className="relationship-edge-pulse" r="3.8"><animateMotion dur="1.15s" path={`M ${startX} ${startY} L ${endX} ${endY}`} repeatCount="indefinite" /></circle>}
          </g>;
        })}
        {dragState !== null && dragSource !== undefined && (() => {
          const target = dragTarget ?? dragState.pointer;
          const dx = target.x - dragSource.x;
          const dy = target.y - dragSource.y;
          const distance = Math.hypot(dx, dy);
          if (distance < 1) return null;
          const ux = dx / distance;
          const uy = dy / distance;
          const startX = dragSource.x + ux * dragSource.radius;
          const startY = dragSource.y + uy * dragSource.radius;
          const endX = dragTarget === undefined ? target.x : target.x - ux * dragTarget.radius;
          const endY = dragTarget === undefined ? target.y : target.y - uy * dragTarget.radius;
          return <line className={`relationship-drag-edge${dragTarget === undefined ? "" : " is-snapped"}`} pointerEvents="none" x1={startX} x2={endX} y1={startY} y2={endY} />;
        })()}
        {characters.map((character) => {
          const point = positionById.get(character.id);
          if (point === undefined) return null;
          const isEndpointOfActiveLink = activeLinkId !== null && visibleLinks.some((link) => link.id === activeLinkId && (link.fromCharacterId === character.id || link.toCharacterId === character.id));
          const isNeighbor = activeCharacterId !== null && visibleLinks.some((link) => (link.fromCharacterId === activeCharacterId && link.toCharacterId === character.id) || (link.toCharacterId === activeCharacterId && link.fromCharacterId === character.id));
          const selected = focusedCharacterId === character.id;
          const hovered = hoveredCharacterId === character.id;
          const isMuted = hasFocus && !selected && !hovered && !isNeighbor && !isEndpointOfActiveLink;
          const focusLevel = selected || isEndpointOfActiveLink ? "selected" : hovered ? "hovered" : isNeighbor ? "neighbor" : "normal";
          const scale = focusLevel === "selected" ? 1.12 : focusLevel === "hovered" ? 1.08 : focusLevel === "neighbor" ? 1.03 : 1;
          const radius = point.radius * scale;
          const anchorActive = editable && edgeAnchorCharacterId === character.id;
          const isDragTarget = dragState?.targetCharacterId === character.id;
          return <g aria-label={`聚焦 ${character.name}`} className={`relationship-node is-${characterTone(character)} is-${focusLevel}${isMuted ? " is-muted" : ""}${anchorActive ? " is-edge-anchor" : ""}${isDragTarget ? " is-drag-target" : ""}`} key={character.id} onClick={(event) => {
            if (suppressClickRef.current) return;
            event.stopPropagation();
            onFocusCharacter(character);
          }} onKeyDown={(event) => keyActivates(event, () => onFocusCharacter(character))} onPointerEnter={(event) => {
            if (dragState !== null) return;
            setHoveredCharacterId(character.id);
            showCharacterTooltip(character, event);
          }} onPointerLeave={() => {
            if (dragState !== null) return;
            setHoveredCharacterId(null);
            setEdgeAnchorCharacterId(null);
            setTooltip(null);
          }} onPointerDown={(event) => {
            if (event.button !== 0 || !editable || dragState !== null) return;
            const localPoint = graphPointFromPointer(svgRef.current, event);
            if (localPoint === null || !isNodeEdgeAnchor(localPoint, point)) return;
            event.preventDefault();
            event.stopPropagation();
            startRelationshipDrag(character, localPoint, event.pointerId);
          }} onPointerMove={(event) => {
            if (dragState !== null) return;
            const localPoint = graphPointFromPointer(svgRef.current, event);
            setEdgeAnchorCharacterId(editable && localPoint !== null && isNodeEdgeAnchor(localPoint, point) ? character.id : null);
            showCharacterTooltip(character, event);
          }} role="button" tabIndex={0} transform={`translate(${point.x} ${point.y})`}>
            <title>{`${character.name} · ${nodeRole(character)} · ${character.statusLabel}`}</title>
            {(selected || hovered) && <circle className="relationship-node-halo" r={radius + 11} />}
            <circle className="relationship-node-core" r={radius} />
            <circle className="relationship-node-status" cx={radius * .54} cy={-radius * .54} r={Math.max(3, radius * .12)} />
            <text className="relationship-node-name" textAnchor="middle" y="5">{compactGraphLabel(character.name, radius)}</text>
            <text className="relationship-node-role" textAnchor="middle" y={radius + 14}>{compactGraphLabel(nodeRole(character), radius * 1.2)}</text>
          </g>;
        })}
      </svg>
      {tooltip !== null && <GraphTooltipSurface tooltip={tooltip} />}
      {contextMenu !== null && <GraphContextMenuSurface menu={contextMenu} onClose={() => setContextMenu(null)} {...(onCreateCharacter === undefined ? {} : { onCreateCharacter })} {...(onEditCharacter === undefined ? {} : { onEditCharacter })} {...(onEditRelationship === undefined ? {} : { onEditRelationship })} {...(onRemoveRelationship === undefined ? {} : { onRemoveRelationship })} {...(onRetireCharacter === undefined ? {} : { onRetireCharacter })} />}
      {!sourceFrame && <p>点击节点聚焦关联；缩放、筛选和聚焦仅改变当前视图，不会修改故事状态。</p>}
    </section>
  );
}

function laneColor(tone: NarrativeSubplotLaneView["tone"]): string {
  return { jade: "jade", blue: "blue", red: "red", violet: "violet" }[tone];
}

function weaveTone(type: NarrativeVisualizationView["weaveLinks"][number]["type"]): string {
  return { feed_main: "blue", reveal_key: "violet", theme_echo: "jade", trigger_start: "jade", trigger_turn: "red" }[type];
}

interface TimelineDetail {
  readonly id: string;
  readonly meta: string;
  readonly title: string;
  readonly description: string;
}

interface TimelineTooltip extends TimelineDetail, FloatingTooltipAnchor {
  readonly size: GraphTooltipSize;
}

function compactTimelineLabel(value: string, limit: number): string {
  if (value.length <= limit) return value;
  return `${value.slice(0, Math.max(1, limit - 1)).trimEnd()}…`;
}

export function timelineLabelLines(value: string, width = 10): readonly string[] {
  const characters = Array.from(value);
  const lineWidth = Math.max(1, width);
  return Array.from({ length: Math.ceil(characters.length / lineWidth) }, (_, index) => characters.slice(index * lineWidth, (index + 1) * lineWidth).join(""));
}

function timelineNodeDescription(label: string, description: string | undefined): string {
  const fullDescription = description?.trim() ?? "";
  return fullDescription.length > 0 ? fullDescription : label;
}

export function timelineDetailFor(visualization: NarrativeVisualizationView, selectedId: string | null): TimelineDetail | null {
  if (selectedId === null) return null;
  const phase = visualization.phases.find((item) => item.id === selectedId);
  if (phase !== undefined) {
    return {
      id: phase.id,
      title: phase.label,
      meta: `叙事阶段 · Ch.${phase.chapterStart}–${phase.chapterEnd} · ${phase.tensionLabel}`,
      description: phase.summary,
    };
  }
  const milestone = visualization.milestones.find((item) => item.id === selectedId);
  if (milestone !== undefined) {
    return {
      id: milestone.id,
      title: milestone.label,
      meta: `主线转折 · 第 ${milestone.chapter} 章`,
      description: timelineNodeDescription(milestone.label, milestone.description),
    };
  }
  for (const lane of visualization.subplotLanes) {
    if (lane.id === selectedId) {
      return {
        id: lane.id,
        title: lane.label,
        meta: `支线计划 · ${lane.events.length} 个章节节点`,
        description: lane.events.map((event) => `第 ${event.chapter} 章 · ${timelineNodeDescription(event.label, event.description)}`).join("\n\n") || "本支线尚未提供章节节点。",
      };
    }
    const event = lane.events.find((item) => item.id === selectedId);
    if (event !== undefined) {
      return {
        id: event.id,
        title: event.label,
        meta: `支线「${lane.label}」· 第 ${event.chapter} 章`,
        description: timelineNodeDescription(event.label, event.description),
      };
    }
  }
  const weave = visualization.weaveLinks.find((item) => item.id === selectedId);
  if (weave !== undefined) {
    return { id: weave.id, title: weave.label, meta: `支线汇入 · 第 ${weave.chapter} 章`, description: weave.description };
  }
  for (const arc of visualization.characterArcs ?? []) {
    if (selectedId === arc.id) return { id: arc.id, title: arc.character, meta: "角色弧光", description: arc.arcSummary };
    const index = arc.milestones.findIndex((_, milestoneIndex) => `${arc.id}-ms-${milestoneIndex}` === selectedId);
    const milestone = arc.milestones[index];
    if (milestone !== undefined) return { id: selectedId, title: `${arc.character} · 角色弧光`, meta: `Ch.${milestone.chapterStart}–${milestone.chapterEnd}`, description: milestone.description || arc.arcSummary };
  }
  return null;
}

/** A source-shaped chapter timeline with phase, milestone, and lane tracks. */
export function NarrativeTimelineRenderer({ visualization }: { readonly visualization: NarrativeVisualizationView }) {
  const [selectedId, setSelectedId] = useState<string | null>(
    () => visualization.phases[0]?.id ?? visualization.milestones[0]?.id ?? null,
  );
  const [detailOpen, setDetailOpen] = useState(false);
  const [timelineTooltip, setTimelineTooltip] = useState<TimelineTooltip | null>(null);
  const [trackMode, setTrackMode] = useState<"subplots" | "arcs" | "all">(() => visualization.subplotLanes.length > 0 ? "subplots" : "arcs");
  const [focusedLaneId, setFocusedLaneId] = useState("");
  const visibleLanes = trackMode === "arcs" ? [] : visualization.subplotLanes.filter((lane) => !focusedLaneId || lane.id === focusedLaneId);
  const laneGap = Math.max(timelineLaneGap, ...visibleLanes.map((lane) => timelineLabelLines(lane.label).length * 20 + 8));
  const laneOffset = (laneGap - timelineLaneGap) / 2;
  const ticks = timelineTickChapters(visualization.totalChapters);
  const { feedbackBusY, milestoneY } = timelineVerticalLayout;
  const arcLanes = trackMode === "subplots" ? [] : visualization.characterArcs ?? [];
  const contentHeight = timelineContentHeight(visibleLanes.length, arcLanes.length) + (laneGap - timelineLaneGap) * visibleLanes.length;
  const chapterBadgeX = timelineHorizontalLayout.right - timelineChapterBadgeLayout.width;
  const chapterBadgeTextX = chapterBadgeX + timelineChapterBadgeLayout.width / 2;
  // Preserve the source-canvas ratio without forcing a wide nested viewport.
  // The compact floor prevents flex shrink while still fitting the full
  // blueprint into a conventional desktop folio.
  const minimumRenderedHeight = Math.ceil(contentHeight * (960 / 1400));
  const gridBottomY = contentHeight - 22;
  const laneYById = new Map(visibleLanes.map((lane, index) => [lane.id, timelineLaneStartY + laneOffset + index * laneGap]));
  const arcLaneTopY = timelineArcLaneStartY(visibleLanes.length) + (laneGap - timelineLaneGap) * visibleLanes.length;
  const selectedDetail = timelineDetailFor(visualization, selectedId);
  const selectedSummary = selectedDetail?.description
    ?? "选择阶段、转折或节点查看详情；画布只保留结构与章节坐标，避免长文本彼此遮挡。";
  const showTimelineTooltip = (detail: TimelineDetail, event: PointerEvent<SVGGElement>) => {
    const position = tooltipPosition(event, timelineTooltipSize);
    if (position === null) return;
    setTimelineTooltip({ ...detail, size: timelineTooltipSize, ...position });
  };
  const hasTimelineNodes = visualization.totalChapters > 0 && (
    visualization.phases.length > 0
    || visualization.milestones.length > 0
    || visualization.weaveLinks.length > 0
    || visualization.subplotLanes.some((lane) => lane.events.length > 0)
    || (visualization.characterArcs ?? []).some((arc) => arc.milestones.length > 0)
  );

  useEffect(() => {
    if (timelineDetailFor(visualization, selectedId) === null) {
      setSelectedId(visualization.phases[0]?.id ?? visualization.milestones[0]?.id ?? null);
      setDetailOpen(false);
    }
  }, [selectedId, visualization]);

  useEffect(() => {
    if (focusedLaneId && !visualization.subplotLanes.some((lane) => lane.id === focusedLaneId)) setFocusedLaneId("");
  }, [focusedLaneId, visualization.subplotLanes]);

  const changeTrackMode = (mode: "subplots" | "arcs" | "all") => {
    setTrackMode(mode);
    setFocusedLaneId("");
    setTimelineTooltip(null);
    setDetailOpen(false);
    setSelectedId(visualization.phases[0]?.id ?? visualization.milestones[0]?.id ?? null);
  };

  if (!hasTimelineNodes) {
    return (
      <section className="narrative-timeline-renderer" aria-label="叙事时间线">
        <div className="narrative-timeline-empty-state" role="status">
          <h3>叙事蓝图尚未形成可绘制节点</h3>
          <p>{visualization.totalChapters > 0
            ? `当前项目规划为 ${visualization.totalChapters} 章；完成叙事阶段、关键转折或支线规划后，这里会自动生成时间线。`
            : "当前项目尚无可用的蓝图或章节大纲，因此不会显示空白时间轴。"}</p>
        </div>
      </section>
    );
  }

  return (
    <section className="narrative-timeline-renderer" aria-label="叙事时间线">
      <div className="timeline-controls">
        <div role="group" aria-label="蓝图图层">
          <button aria-pressed={trackMode === "subplots"} onClick={() => changeTrackMode("subplots")} type="button">支线计划 · {visualization.subplotLanes.length}</button>
          <button aria-pressed={trackMode === "arcs"} onClick={() => changeTrackMode("arcs")} type="button">角色弧光 · {visualization.characterArcs?.length ?? 0}</button>
          <button aria-pressed={trackMode === "all"} onClick={() => changeTrackMode("all")} type="button">全部轨道</button>
        </div>
        {trackMode !== "arcs" && visualization.subplotLanes.length > 0 && <label>聚焦支线<select aria-label="聚焦支线" value={focusedLaneId} onChange={(event) => { setFocusedLaneId(event.target.value); setSelectedId(event.target.value || visualization.phases[0]?.id || null); setTimelineTooltip(null); }}>{<option value="">全部支线</option>}{visualization.subplotLanes.map((lane) => <option key={lane.id} value={lane.id}>{lane.label}</option>)}</select></label>}
      </div>
      <div className="narrative-timeline-scroll">
        <svg aria-label="可选择叙事节点的章节时间线" role="img" style={{ aspectRatio: `1400 / ${contentHeight}`, minHeight: `${minimumRenderedHeight}px` }} viewBox={`0 0 1400 ${contentHeight}`}>
          <g aria-label={`共 ${visualization.totalChapters} 章`} className="timeline-total-chapters">
            <rect height={timelineChapterBadgeLayout.height} rx={timelineChapterBadgeLayout.height / 2} width={timelineChapterBadgeLayout.width} x={chapterBadgeX} y={timelineChapterBadgeLayout.topY} />
            <text textAnchor="middle" x={chapterBadgeTextX} y={timelineChapterBadgeLayout.topY + 15}>共 {visualization.totalChapters} 章</text>
          </g>
          <g className="timeline-grid">{ticks.map((chapter) => <g key={chapter}><line x1={chapterToTimelineX(chapter, visualization.totalChapters)} x2={chapterToTimelineX(chapter, visualization.totalChapters)} y1={timelineVerticalLayout.gridTopY} y2={gridBottomY} /><text textAnchor="middle" x={chapterToTimelineX(chapter, visualization.totalChapters)} y={timelineVerticalLayout.tickLabelY}>{chapter}</text></g>)}</g>
          <text className="timeline-section-label" x={timelineHorizontalLayout.sectionLabelX} y={timelineVerticalLayout.phaseTextY}>叙事阶段</text>
          {visualization.phases.map((phase) => {
            const chapterWidth = timelineChapterWidth(visualization.totalChapters);
            const start = chapterToTimelineX(phase.chapterStart, visualization.totalChapters) - chapterWidth / 2;
            const end = chapterToTimelineX(phase.chapterEnd, visualization.totalChapters) + chapterWidth / 2;
            const compactLabel = compactTimelineLabel(phase.label, Math.max(6, Math.floor((end - start) / 17)));
            const detail = { id: phase.id, title: phase.label, meta: `叙事阶段 · Ch.${phase.chapterStart}–${phase.chapterEnd} · ${phase.tensionLabel}`, description: phase.summary };
            return <g aria-label={`查看阶段：${phase.label}`} className={`timeline-phase is-${phase.tone}${selectedId === phase.id ? " is-selected" : ""}`} key={phase.id} onClick={() => setSelectedId(phase.id)} onKeyDown={(event) => keyActivates(event, () => setSelectedId(phase.id))} onPointerEnter={(event) => showTimelineTooltip(detail, event)} onPointerLeave={() => setTimelineTooltip(null)} onPointerMove={(event) => showTimelineTooltip(detail, event)} role="button" tabIndex={0}><rect height={timelineVerticalLayout.phaseHeight} rx="8" width={end - start} x={start} y={timelineVerticalLayout.phaseTopY} /><text x={start + 12} y={timelineVerticalLayout.phaseTextY}>{compactLabel}</text><text className="timeline-phase-summary" x={start + 12} y={timelineVerticalLayout.phaseSummaryY}>Ch.{phase.chapterStart}–{phase.chapterEnd}</text></g>;
          })}
          <text className="timeline-section-label" x={timelineHorizontalLayout.sectionLabelX} y={timelineVerticalLayout.mainlineSectionLabelY}>主线骨架</text>
          <text className="timeline-mainline-label" x={timelineHorizontalLayout.left} y={timelineVerticalLayout.mainlineLabelY}>主线推进：关键转折与阶段收束</text>
          <line className="timeline-mainline" x1={chapterToTimelineX(1, visualization.totalChapters)} x2={chapterToTimelineX(visualization.totalChapters, visualization.totalChapters)} y1={milestoneY} y2={milestoneY} />
          {visualization.milestones.map((milestone, index) => {
            const x = chapterToTimelineX(milestone.chapter, visualization.totalChapters);
            const detail = {
              id: milestone.id,
              title: milestone.label,
              meta: `主线转折 · 第 ${milestone.chapter} 章`,
              description: timelineNodeDescription(milestone.label, milestone.description),
            };
            return <g aria-label={`查看第 ${milestone.chapter} 章转折：${milestone.label}`} className={`timeline-milestone${selectedId === milestone.id ? " is-selected" : ""}`} key={milestone.id} onClick={() => setSelectedId(milestone.id)} onKeyDown={(event) => keyActivates(event, () => setSelectedId(milestone.id))} onPointerEnter={(event) => showTimelineTooltip(detail, event)} onPointerLeave={() => setTimelineTooltip(null)} onPointerMove={(event) => showTimelineTooltip(detail, event)} role="button" tabIndex={0}><polygon points={`${x},${milestoneY - 11} ${x + 11},${milestoneY} ${x},${milestoneY + 11} ${x - 11},${milestoneY}`} /><text className="timeline-milestone-index" textAnchor="middle" x={x} y={milestoneY + timelineVerticalLayout.milestoneIndexOffset}>M{index + 1}</text></g>;
          })}
          {visibleLanes.length > 0 && <>
          <text className="timeline-section-label" x={timelineHorizontalLayout.sectionLabelX} y={timelineVerticalLayout.subplotSectionLabelY}>支线计划</text>
          <line className="timeline-feedback-bus" x1={chapterToTimelineX(1, visualization.totalChapters)} x2={chapterToTimelineX(visualization.totalChapters, visualization.totalChapters)} y1={feedbackBusY} y2={feedbackBusY} />
          <text className="timeline-feedback-label" x={timelineHorizontalLayout.left} y={feedbackBusY - 11}>支线反哺 / 主题回响汇入主线</text>
          </>}
          {visualization.weaveLinks.map((link) => {
            const sourceY = laneYById.get(link.sourceLaneId);
            if (sourceY === undefined || link.target !== "mainline") return null;
            const x = chapterToTimelineX(link.chapter, visualization.totalChapters);
            const detail = { id: link.id, title: link.label, meta: `支线汇入 · 第 ${link.chapter} 章`, description: link.description };
            return <g aria-label={`查看第 ${link.chapter} 章汇入：${link.label}`} className={`timeline-weave is-${weaveTone(link.type)}${selectedId === link.id ? " is-selected" : ""}`} key={link.id} onClick={() => setSelectedId(link.id)} onKeyDown={(event) => keyActivates(event, () => setSelectedId(link.id))} onPointerEnter={(event) => showTimelineTooltip(detail, event)} onPointerLeave={() => setTimelineTooltip(null)} onPointerMove={(event) => showTimelineTooltip(detail, event)} role="button" tabIndex={0}><line x1={x} x2={x} y1={sourceY} y2={feedbackBusY} /><circle cx={x} cy={feedbackBusY} r="5" /></g>;
          })}
          {visibleLanes.map((lane) => {
            const y = laneYById.get(lane.id)!;
            const labelLines = timelineLabelLines(lane.label);
            return <g className={`timeline-lane is-${laneColor(lane.tone)}`} key={lane.id}><g className="timeline-lane-name" role="button" tabIndex={0} aria-label={`查看支线：${lane.label}`} onClick={() => setSelectedId(lane.id)} onKeyDown={(event) => keyActivates(event, () => setSelectedId(lane.id))}><text className="timeline-lane-label" x={timelineHorizontalLayout.labelX} textAnchor="end">{labelLines.map((line, index) => <tspan key={index} x={timelineHorizontalLayout.labelX} y={y + 5 + (index - (labelLines.length - 1) / 2) * 20}>{line}</tspan>)}</text></g><line x1={chapterToTimelineX(1, visualization.totalChapters)} x2={chapterToTimelineX(visualization.totalChapters, visualization.totalChapters)} y1={y} y2={y} />{lane.events.map((event) => { const x = chapterToTimelineX(event.chapter, visualization.totalChapters); const detail = { id: event.id, title: event.label, meta: `支线「${lane.label}」· 第 ${event.chapter} 章`, description: timelineNodeDescription(event.label, event.description) }; return <g aria-label={`查看支线 ${lane.label} 第 ${event.chapter} 章节点：${event.label}`} className={`timeline-lane-event is-${laneColor(lane.tone)} is-${event.emphasis ?? "normal"}${selectedId === event.id ? " is-selected" : ""}`} key={event.id} onClick={() => setSelectedId(event.id)} onKeyDown={(keyboardEvent) => keyActivates(keyboardEvent, () => setSelectedId(event.id))} onPointerEnter={(pointerEvent) => showTimelineTooltip(detail, pointerEvent)} onPointerLeave={() => setTimelineTooltip(null)} onPointerMove={(pointerEvent) => showTimelineTooltip(detail, pointerEvent)} role="button" tabIndex={0}><circle cx={x} cy={y} r={event.emphasis === "normal" ? 5 : 8} />{event.emphasis !== "normal" && <line x1={x} x2={x} y1={milestoneY + 11} y2={y - 8} />}</g>; })}</g>;
          })}
          {arcLanes.length > 0 && <>
            <text className="timeline-section-label" x={timelineHorizontalLayout.sectionLabelX} y={arcLaneTopY - 17}>角色弧光</text>
            {arcLanes.map((arc, arcIndex) => {
              const y = arcLaneTopY + arcIndex * timelineArcGap;
              const spanChapters = arc.milestones.flatMap((ms) => [ms.chapterStart, ms.chapterEnd]).filter((chapter) => chapter > 0);
              const spanStart = spanChapters.length > 0 ? Math.min(...spanChapters) : 1;
              const spanEnd = spanChapters.length > 0 ? Math.max(...spanChapters) : visualization.totalChapters;
              const x1 = chapterToTimelineX(spanStart, visualization.totalChapters);
              const x2 = chapterToTimelineX(Math.min(spanEnd, visualization.totalChapters), visualization.totalChapters);
              return <g className="timeline-arc" key={arc.id}>
                <text className="timeline-arc-label" textAnchor="end" x={timelineHorizontalLayout.labelX} y={y + 4}>{arc.character}</text>
                <line className="timeline-arc-span" x1={x1} x2={x2} y1={y} y2={y} />
                {arc.milestones.map((ms, msIndex) => {
                  const midChapter = Math.max(1, Math.round((ms.chapterStart + ms.chapterEnd) / 2));
                  const mx = chapterToTimelineX(midChapter, visualization.totalChapters);
                  const detail = { id: `${arc.id}-ms-${msIndex}`, title: `${arc.character} · 角色弧光`, meta: `Ch.${ms.chapterStart}–${ms.chapterEnd}`, description: ms.description || arc.arcSummary };
                  return <g aria-label={`查看${arc.character}在 Ch.${ms.chapterStart}–${ms.chapterEnd} 的角色弧光`} className="timeline-arc-milestone" key={`${arc.id}-ms-${msIndex}`} role="button" tabIndex={0} onClick={() => setSelectedId(detail.id)} onKeyDown={(event) => keyActivates(event, () => setSelectedId(detail.id))} onPointerEnter={(event) => showTimelineTooltip(detail, event)} onPointerLeave={() => setTimelineTooltip(null)} onPointerMove={(event) => showTimelineTooltip(detail, event)}>
                    <circle cx={mx} cy={y} r="4" />
                  </g>;
                })}
              </g>;
            })}
          </>}
        </svg>
      </div>
      {timelineTooltip !== null && <TimelineTooltipSurface tooltip={timelineTooltip} />}
      <footer aria-live="polite"><div><strong>{selectedDetail?.title ?? "节点详情"}</strong><small>{selectedDetail?.meta}</small><p>{selectedSummary}</p></div>{selectedDetail !== null && <button className="button button-secondary" onClick={() => setDetailOpen(true)} type="button">展开详情</button>}</footer>
      {detailOpen && selectedDetail !== null && <AppDialog className="narrative-detail-dialog" confirmLabel="完成" description={selectedDetail.meta} onClose={() => setDetailOpen(false)} title={selectedDetail.title}><section className="narrative-detail-dialog-copy"><span>当前选中节点</span><p>{selectedDetail.description}</p><small>图中仅保留章节坐标与结构标记，完整描述在此处展开，避免多条信息重叠。</small></section></AppDialog>}
    </section>
  );
}
