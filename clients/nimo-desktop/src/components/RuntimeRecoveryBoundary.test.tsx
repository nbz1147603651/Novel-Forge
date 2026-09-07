import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { retryRuntimeRecovery, RuntimeRecoverySurface } from "./RuntimeRecoveryBoundary";

describe("RuntimeRecoverySurface", () => {
  it("keeps a renderer failure actionable instead of rendering a blank surface", () => {
    const html = renderToStaticMarkup(
      <RuntimeRecoverySurface onRecover={vi.fn()} onReload={vi.fn()} />,
    );

    expect(html).toContain("界面需要恢复");
    expect(html).toContain("恢复界面");
    expect(html).toContain("重新加载界面");
    expect(html).toContain("你的已保存作品不受影响");
  });

  it("increments the child remount revision for each retry", () => {
    expect(retryRuntimeRecovery({ failed: true, recoveryRevision: 0 })).toEqual({
      failed: false,
      recoveryRevision: 1,
    });
    expect(retryRuntimeRecovery({ failed: true, recoveryRevision: 1 })).toEqual({
      failed: false,
      recoveryRevision: 2,
    });
  });
});
