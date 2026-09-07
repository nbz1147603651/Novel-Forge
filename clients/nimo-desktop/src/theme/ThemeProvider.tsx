import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useMemo,
  useState,
} from "react";

import {
  applyTheme,
  legacyThemeStorageKey,
  loadStoredTheme,
  normalizeThemeId,
  themeDefinition,
  type ThemeDefinition,
  type ThemeId,
} from "./theme";

export type ThemeMode = "manual" | "system";
export type ThemeContrast = "standard" | "high";

export interface ThemePreference {
  readonly mode: ThemeMode;
  readonly themeId: ThemeId;
  readonly lightThemeId: ThemeId;
  readonly darkThemeId: ThemeId;
  readonly contrast: ThemeContrast;
}

interface ThemeContextValue {
  readonly activeTheme: ThemeDefinition;
  readonly activeThemeId: ThemeId;
  readonly preference: ThemePreference;
  readonly setContrast: (contrast: ThemeContrast) => void;
  readonly setMode: (mode: ThemeMode) => void;
  readonly setTheme: (themeId: ThemeId) => void;
}

export const themePreferenceStorageKey = "nimo.theme.preference.v2";

const defaultPreference: ThemePreference = {
  mode: "manual",
  themeId: "narrative_ember",
  lightThemeId: "snow_inkstone",
  darkThemeId: "twilight_ink",
  contrast: "standard",
};

const ThemeContext = createContext<ThemeContextValue | null>(null);

function readPreference(): ThemePreference {
  if (typeof window === "undefined") return defaultPreference;
  try {
    const raw = window.localStorage.getItem(themePreferenceStorageKey);
    if (raw === null) {
      return { ...defaultPreference, themeId: loadStoredTheme() };
    }
    const parsed = JSON.parse(raw) as Partial<ThemePreference>;
    return {
      mode: parsed.mode === "system" ? "system" : "manual",
      themeId: normalizeThemeId(parsed.themeId),
      lightThemeId: normalizeThemeId(parsed.lightThemeId ?? defaultPreference.lightThemeId),
      darkThemeId: normalizeThemeId(parsed.darkThemeId ?? defaultPreference.darkThemeId),
      contrast: parsed.contrast === "high" ? "high" : "standard",
    };
  } catch {
    return { ...defaultPreference, themeId: loadStoredTheme() };
  }
}

function prefersDarkTheme(): boolean {
  return typeof window !== "undefined"
    && typeof window.matchMedia === "function"
    && window.matchMedia("(prefers-color-scheme: dark)").matches;
}

export function ThemeProvider({ children }: { readonly children: ReactNode }) {
  const [preference, setPreference] = useState<ThemePreference>(readPreference);
  const [systemDark, setSystemDark] = useState(prefersDarkTheme);
  const activeThemeId = preference.mode === "system"
    ? systemDark ? preference.darkThemeId : preference.lightThemeId
    : preference.themeId;
  const activeTheme = useMemo(() => themeDefinition(activeThemeId), [activeThemeId]);

  // Apply visual state before paint so neither first load nor a night-theme
  // switch flashes the previous palette. applyTheme deduplicates token writes,
  // so changing high-contrast mode only updates the root attributes.
  useLayoutEffect(() => {
    applyTheme(activeThemeId, { contrast: preference.contrast, persistLegacy: false });
  }, [activeThemeId, preference.contrast]);

  useEffect(() => {
    try {
      window.localStorage.setItem(themePreferenceStorageKey, JSON.stringify(preference));
      window.localStorage.setItem(legacyThemeStorageKey, activeThemeId);
    } catch {
      // Theme application must stay available when storage is blocked.
    }
  }, [activeThemeId, preference]);

  useEffect(() => {
    if (typeof window.matchMedia !== "function") return undefined;
    const query = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = (event: MediaQueryListEvent) => setSystemDark(event.matches);
    query.addEventListener?.("change", onChange);
    return () => query.removeEventListener?.("change", onChange);
  }, []);

  useEffect(() => {
    const onStorage = (event: StorageEvent) => {
      if (event.key === themePreferenceStorageKey) setPreference(readPreference());
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  const setTheme = useCallback((themeId: ThemeId) => {
    setPreference((current) => current.mode === "manual" && current.themeId === themeId
      ? current
      : { ...current, mode: "manual", themeId });
  }, []);
  const setMode = useCallback((mode: ThemeMode) => {
    setPreference((current) => current.mode === mode ? current : { ...current, mode });
  }, []);
  const setContrast = useCallback((contrast: ThemeContrast) => {
    setPreference((current) => current.contrast === contrast ? current : { ...current, contrast });
  }, []);

  const value = useMemo<ThemeContextValue>(() => ({
    activeTheme,
    activeThemeId,
    preference,
    setContrast,
    setMode,
    setTheme,
  }), [activeTheme, activeThemeId, preference, setContrast, setMode, setTheme]);

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeContextValue {
  const context = useContext(ThemeContext);
  if (context === null) throw new Error("useTheme must be used inside ThemeProvider");
  return context;
}
