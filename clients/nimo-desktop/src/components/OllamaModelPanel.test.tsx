import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { EngineClient, EngineCommandClient } from "@nimo/engine-contracts";

import { OllamaModelPanel } from "./OllamaModelPanel";

const engineClient = {
  getOllama: async () => ({}) as never,
} satisfies Pick<EngineClient, "getOllama">;

const commandClient = {} as Pick<
  EngineCommandClient,
  | "cancelJob"
  | "configureOllamaRuntime"
  | "controlOllamaRuntime"
  | "deleteOllamaModel"
  | "pullOllamaModel"
  | "resumeJob"
  | "setOllamaModelRoles"
>;

describe("OllamaModelPanel", () => {
  it("starts from the Engine-owned empty state and never describes browser-local control", () => {
    const html = renderToStaticMarkup(
      <OllamaModelPanel commandClient={commandClient} engineClient={engineClient} />,
    );

    expect(html).toContain("Engine 主机 Ollama");
    expect(html).toContain("尚未读取 Engine 主机上的 Ollama 状态");
    expect(html).not.toContain("本机 Ollama 服务");
    expect(html).not.toContain("/api/tags");
    expect(html).not.toContain("/api/pull");
    expect(html).not.toContain("/api/delete");
  });
});
