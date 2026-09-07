/**
 * Layout density hook — 1:1 parity with PySide6 `window/core.py::_apply_window_density`.
 *
 * Source constants (novel_forge/desktop/constants.py):
 *   COMPACT_WIDTH_THRESHOLD = 1360
 *   COMPACT_HEIGHT_THRESHOLD = 820
 *   DENSITY_RESIZE_DEBOUNCE_MS = 500
 *
 * Density effects in source:
 *   - content margin: 14 (compact) / 18 (regular)
 *   - content spacing: 10 / 12
 *   - top bar min-height: 84 / 96
 *   - top layout margins: 18/10 vs 22/14, spacing 8/10
 *   - side rail collapsed in compact
 *   - `density` property set on root widget (QSS restyling)
 */
import { useEffect, useState } from "react";

export const COMPACT_WIDTH_THRESHOLD = 1360;
export const COMPACT_HEIGHT_THRESHOLD = 820;
export const DENSITY_RESIZE_DEBOUNCE_MS = 500;

export type LayoutDensity = "compact" | "regular";

export function useLayoutDensity(): LayoutDensity {
  const [density, setDensity] = useState<LayoutDensity>(() => computeDensity());

  useEffect(() => {
    let timer: number | null = null;
    const onResize = () => {
      if (timer !== null) window.clearTimeout(timer);
      timer = window.setTimeout(() => {
        setDensity(computeDensity());
      }, DENSITY_RESIZE_DEBOUNCE_MS);
    };
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      if (timer !== null) window.clearTimeout(timer);
    };
  }, []);

  // Apply density attribute to document root for CSS variable switching
  useEffect(() => {
    document.documentElement.setAttribute("data-density", density);
  }, [density]);

  return density;
}

function computeDensity(): LayoutDensity {
  return window.innerWidth <= COMPACT_WIDTH_THRESHOLD ||
    window.innerHeight <= COMPACT_HEIGHT_THRESHOLD
    ? "compact"
    : "regular";
}
