import { Popover } from "radix-ui";

import { themeDefinition, type ThemeDefinition, type ThemeId } from "../theme/theme";
import { useTheme } from "../theme/ThemeProvider";

interface QuickTheme {
  readonly alias: string;
  readonly id: ThemeId;
  readonly theme: ThemeDefinition;
}

const quickThemes: readonly QuickTheme[] = [
  { id: "narrative_ember", alias: "暖章", theme: themeDefinition("narrative_ember") },
  { id: "twilight_ink", alias: "曜石", theme: themeDefinition("twilight_ink") },
  { id: "snow_inkstone", alias: "素纸", theme: themeDefinition("snow_inkstone") },
];

export function ThemeSwitcher() {
  const { activeTheme, activeThemeId, preference, setContrast, setMode, setTheme } = useTheme();
  return (
    <Popover.Root>
      <Popover.Trigger asChild>
        <button
          aria-label={`主题：${activeTheme.label}`}
          className="theme-switcher-trigger"
          type="button"
        >
          <span aria-hidden="true" style={{ background: activeTheme.tokens["accent.primary"] }} />
          {activeTheme.label}
          <b aria-hidden="true">⌄</b>
        </button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          align="end"
          collisionPadding={12}
          className="theme-switcher-popover"
          side="bottom"
          sideOffset={8}
        >
          <header><span>工作台主题</span><small>全局同步 · 即时生效</small></header>
          <div className="theme-switcher-mode" role="group" aria-label="主题模式">
            <button className={preference.mode === "manual" ? "is-active" : ""} onClick={() => setMode("manual")} type="button">手动</button>
            <button className={preference.mode === "system" ? "is-active" : ""} onClick={() => setMode("system")} type="button">跟随系统</button>
          </div>
          <div className="theme-switcher-quick" role="radiogroup" aria-label="快速主题">
            {quickThemes.map(({ id, alias, theme }) => (
              <button
                aria-checked={activeThemeId === id}
                className={activeThemeId === id ? "is-active" : ""}
                key={id}
                onClick={() => setTheme(id)}
                role="radio"
                type="button"
              >
                <span><i style={{ background: theme.tokens["bg.sidebar.start"] }} /><i style={{ background: theme.tokens["accent.primary"] }} /><i style={{ background: theme.tokens["bg.surface"] }} /></span>
                <strong>{alias}</strong><small>{theme.label}</small>
              </button>
            ))}
          </div>
          <label className="theme-switcher-contrast">
            <span><strong>高对比</strong><small>加强边界与正文辨识</small></span>
            <input checked={preference.contrast === "high"} onChange={(event) => setContrast(event.target.checked ? "high" : "standard")} type="checkbox" />
          </label>
          <Popover.Arrow className="theme-switcher-arrow" />
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
