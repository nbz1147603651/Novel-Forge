import type { ReactNode } from "react";

export type IconName =
  | "archive"
  | "book"
  | "chevron"
  | "collapse"
  | "compose"
  | "dashboard"
  | "film"
  | "settings"
  | "studio"
  | "voice"
  | "workflow";

interface IconProps {
  readonly name: IconName;
  readonly size?: number;
  readonly strokeWidth?: number;
}

const paths: Record<IconName, ReactNode> = {
  dashboard: <><rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" /><rect x="3" y="14" width="7" height="7" rx="1" /><rect x="14" y="14" width="7" height="7" rx="1" /></>,
  archive: <><path d="M4 5.5h16v14H4z" /><path d="M4 9h16" /><path d="M9 13h6" /><path d="M7 3h10" /></>,
  workflow: <><path d="M5 5h5v5H5zM14 14h5v5h-5z" /><path d="M10 7.5h4a2 2 0 0 1 2 2V14" /><path d="M14 11.5 16 14l2-2.5" /></>,
  settings: <><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .34 1.88l.06.06-2.48 2.48-.06-.06a1.7 1.7 0 0 0-1.88-.34 1.7 1.7 0 0 0-1.03 1.56v.09h-3.5v-.09A1.7 1.7 0 0 0 9.82 19a1.7 1.7 0 0 0-1.88.34l-.06.06-2.48-2.48.06-.06A1.7 1.7 0 0 0 5.8 15a1.7 1.7 0 0 0-1.56-1.03h-.09v-3.5h.09A1.7 1.7 0 0 0 5.8 9.44a1.7 1.7 0 0 0-.34-1.88l-.06-.06L7.88 5l.06.06a1.7 1.7 0 0 0 1.88.34 1.7 1.7 0 0 0 1.03-1.56v-.09h3.5v.09a1.7 1.7 0 0 0 1.03 1.56 1.7 1.7 0 0 0 1.88-.34l.06-.06 2.48 2.5-.06.06a1.7 1.7 0 0 0-.34 1.88 1.7 1.7 0 0 0 1.56 1.03h.09v3.5h-.09A1.7 1.7 0 0 0 19.4 15Z" /></>,
  studio: <><path d="M5 4h14v16H5z" /><path d="M8 8h8M8 12h8M8 16h5" /><path d="m17 16 3 3" /></>,
  voice: <><path d="M4 10h4l5-4v12l-5-4H4z" /><path d="M16 9.5a4 4 0 0 1 0 5M18.5 7a7.5 7.5 0 0 1 0 10" /></>,
  film: <><rect x="3" y="5" width="18" height="14" rx="2" /><path d="m8 5 2.5-3M14 5l2.5-3M3 10h18" /><path d="m10 12 5 2.5-5 2.5z" /></>,
  compose: <><path d="m4 17.5-.6 3.1 3.1-.6L19 7.5 16.5 5z" /><path d="m14.8 6.7 2.5 2.5" /></>,
  book: <><path d="M4 5.5A3.5 3.5 0 0 1 7.5 2H12v18H7.5A3.5 3.5 0 0 0 4 23Z" /><path d="M20 5.5A3.5 3.5 0 0 0 16.5 2H12v18h4.5a3.5 3.5 0 0 1 3.5 3Z" /></>,
  collapse: <><path d="M15 5 8 12l7 7" /></>,
  chevron: <><path d="m9 5 7 7-7 7" /></>,
};

export function Icon({ name, size = 18, strokeWidth = 1.7 }: IconProps) {
  return (
    <svg aria-hidden="true" fill="none" height={size} stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth={strokeWidth} viewBox="0 0 24 24" width={size}>
      {paths[name]}
    </svg>
  );
}
