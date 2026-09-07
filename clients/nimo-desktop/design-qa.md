# PySide6 UI parity QA

## Comparison target and evidence

- Source visual truth: `/var/folders/d9/4gvz34hs57b8658m2hpbt6ym0000gn/T/codex-clipboard-31455ef9-428b-497c-8534-97e4408be3c1.png`
  (`2048 × 1185` px, PySide6 `卷帙` report state).
- Supplementary typography reference:
  `/var/folders/d9/4gvz34hs57b8658m2hpbt6ym0000gn/T/codex-clipboard-75445bce-159a-4ea5-8001-ea4997637efc.png`.
- Implementation capture: `/private/tmp/nimo-reader-rail-after.png`
  (`1280 × 720` px, CSS viewport `1280 × 720`, density `1`).
- Local state: `http://127.0.0.1:1420/?__nimo_ui_parity=1&page=projects&reader=long`;
  `卷帙 → 基础设定 → 故事规格`. The fixture supplies a different document
  payload than the source report, so QA compares shell geometry, typography,
  navigation, and usable document width rather than its literal report copy.
- The source and implementation captures were opened together for the final
  visual comparison. A mobile comparison is intentionally not applicable:
  the PySide-equivalent desktop shell enforces a `1180px` minimum viewport.

## Comparison history

1. **P1 — oversized rail navigation and constrained report canvas.** Before
   the fix, the compact rail measured `216px`, navigation used `26px` text
   with `54px` rows, and `.doc-rich` stopped at `88ch` (`739px`) inside a
   `1010px` document pane. This created both the visual imbalance in the left
   rail and the unused right-side report area.
2. **Fix.** Shared rail tokens now use a `198px` compact track and `264px`
   desktop track; functional nav uses the theme's Songti UI stack at `16px`
   compact / `18px` desktop. Branding, gaps, indicator, and footer follow the
   same smaller rhythm. Reader rich documents and structured report tables now
   take the full document width.
3. **Post-fix evidence.** The implementation measured `198px` rail,
   `16px`/`42px` navigation, `1028px` document pane, and `1010px` rich-document
   width. The report tab was also opened and its selected state verified; no
   browser console warnings or errors were recorded.

## Required fidelity surfaces

- **Fonts and typography:** compact controls and long reading text both use
  Songti-first CJK fallbacks. English glyphs resolve to Times New Roman, then
  Arial, without allowing CJK fallback glyphs to take over Western model names
  or metadata.
- **Spacing and layout rhythm:** the rail releases `18px` to the canvas at the
  tested density; shortened rows, smaller logo header, and tighter gaps remove
  the vertically oversized navigation rhythm. Structured reader content fills
  the usable pane rather than reserving a right-side column.
- **Colors and tokens:** no palette or active-state token changed; the existing
  dark rail, warm active border, and light reader surfaces remain intact.
- **Image quality and asset fidelity:** the existing NIMO mark and controls are
  unchanged; no replacement or generated assets were introduced.
- **Copy and content:** document copy is engine/fixture owned and unchanged.

## Findings

No actionable P0, P1, or P2 differences remain for the requested rail and
reader-width refinements. The only residual visual difference is fixture
content versus the user's report payload, which is not a layout or typography
regression.

## Checks

- Browser page identity, non-blank render, framework-overlay check, and console
  health — pass.
- Interaction path `卷帙 → 资料检索 → 基础设定` — pass; both selected states
  rendered as expected.
- `npm run check` — pass.
- `npm test` — pass (`42` files / `218` tests).
- `npm run check:interaction` — pass (`5` checks).
- `npm run build` — pass.
- `narrative-visualization-layout.test.ts` — pass (`5` tests), including a
  compact-canvas node containment check.

## Final result

final result: passed
