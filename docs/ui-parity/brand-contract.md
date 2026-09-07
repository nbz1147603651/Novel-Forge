# NIMO Brand Contract

## Authoritative source

- Vector mark: `novel_forge/desktop/resources/brand/nimo-symbol.svg`
- Bootstrap raster: `novel_forge/desktop/resources/brand/nimo-logo.png`
- Semantic color: `brand.logo.accent` in `desktop/theme/palettes.py`

The web client copies the unmodified source SVG to
`clients/nimo-desktop/src/assets/nimo-symbol.svg`. It is never redrawn in CSS;
the mark is recolored only by replacing the source SVG's black paint token with
the active semantic color. This matches the PySide6 `build_brand_pixmap()`
behavior.

## Theme behavior

`applyTheme()` writes the semantic token set to CSS custom properties. The
sidebar mark receives `--nf-brand-logo-accent`, so it changes together with the
rest of the selected desktop theme.

`syncNativeWindowIcon()` rasterizes that same source SVG to PNG and calls
Tauri's runtime window-icon API. On macOS this is the native application/window
icon path, preventing the Dock from retaining the previous theme color.

The runtime window call is intentionally restricted to Tauri: browser previews
do not request a native capability.

## Window and surface shape

The product window remains a standard decorated, opaque native Tauri window.
That preserves macOS' real rounded external corners, traffic lights, shadow and
accessibility behavior; the parity app must not fake those with a transparent
WebView or a CSS-drawn title bar. Browser screenshots therefore validate the
inner client surface, while native Tauri capture validates the actual window.

Inside the client, `design-tokens.css` owns one compact shape hierarchy:
`window 18px`, `shell 14px`, `surface/dialog 17px`, `card 12px`, and `control
8/10px` (plus a 999px pill token). Shared shells and dialogs consume those
tokens. A source-measured component may choose a documented compact dialog
token, but new one-off radius values are not allowed. This keeps all five
themes polished and rounded without increasing control height or wasting
creation space.

## Bootstrap packaging assets

Before the WebView starts, macOS needs a static `.icns`. Generate it from the
existing raster if the source logo changes:

```bash
cd clients/nimo-desktop
pnpm exec tauri icon ../../novel_forge/desktop/resources/brand/nimo-logo.png
```

Version the three platform bootstrap assets under `src-tauri/icons/`:
`icon.icns`, `icon.ico`, and `128x128.png`. Other generated icon variants,
`src-tauri/target/`, and generated Tauri schemas remain untracked.

## Required checks

1. Switch all five themes in the 火候 page and verify the sidebar mark changes.
2. Build the macOS app with `pnpm --filter @nimo/desktop exec tauri build --bundles app`.
3. Launch the app, switch a theme, then verify the Dock/window icon updates.
