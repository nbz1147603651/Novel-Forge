/**
 * Unit tests for LoadingSurface. The error-state branch is the user-visible
 * fix for "章台卡在加载骨架" — these tests guard the contract that an
 * `error` prop flips the surface to a retry-able error view (instead of
 * silently staying on the skeleton).
 */

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { LoadingSurface } from "./LoadingSurface";

describe("LoadingSurface", () => {
  it("renders the loading skeleton when no error is provided", () => {
    const html = renderToStaticMarkup(<LoadingSurface />);

    expect(html).toContain("loading-surface");
    expect(html).not.toContain("connection-error-surface");
    expect(html).not.toContain("重试");
  });

  it("renders a retry-able error surface with the 章台 default title when error is provided", () => {
    const onRetry = vi.fn();
    const html = renderToStaticMarkup(
      <LoadingSurface error="章台快照加载失败：HTTP 404" onRetry={onRetry} />,
    );

    expect(html).toContain("connection-error-surface");
    expect(html).toContain("章台数据加载失败");
    expect(html).toContain("章台快照加载失败：HTTP 404");
    expect(html).toContain("重试");
  });

  it("renders a custom error title when provided", () => {
    const html = renderToStaticMarkup(
      <LoadingSurface error="无网络" title="连接中断" />,
    );

    expect(html).toContain("连接中断");
    expect(html).not.toContain("章台数据加载失败");
  });

  it("renders the error surface without a retry button when no onRetry is given", () => {
    const html = renderToStaticMarkup(<LoadingSurface error="无网络" />);

    expect(html).toContain("connection-error-surface");
    expect(html).toContain("无网络");
    expect(html).not.toContain("重试");
  });
});
