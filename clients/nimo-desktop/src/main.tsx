import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import { RuntimeRecoveryBoundary, RuntimeRecoverySurface } from "./components/RuntimeRecoveryBoundary";
import { ToastProvider } from "./components/Toast";
import { loadEngineClients } from "./lib/engine-client-factory";
import "./styles/global.css";
import "./styles/app-shell.css";
import "./styles/theme-system.css";
import "./styles/dashboard-page.css";
import "./styles/projects-primary.css";
import "./styles/settings-page.css";
import "./styles/creative-temperature.css";
import "./styles/creation-parameter-sections.css";
import "./styles/vector-rebuild.css";
import "./styles/interactions.css";
import "./styles/motion-system.css";
import "./styles/narrative-tools.css";
import "./styles/operation-progress.css";
import "./styles/model-routing.css";
import "./styles/model-connection-status.css";
import "./styles/engine-runtime.css";
import "./styles/chapter-dialog.css";
import "./styles/workflow-error-log.css";
import "./styles/narrative-visualization.css";
import "./styles/floating-stream.css";
import "./styles/workflow-preset.css";
import "./styles/workflow-primary.css";
import "./styles/workflow-run-actions.css";
import "./styles/chapter-run-insights.css";
import "./styles/workflow-cancel.css";
import "./styles/workflow-artifact.css";
import "./styles/workflow-long-init.css";
import "./styles/chapter-operations.css";
import "./styles/voice-rebuild.css";
import "./styles/voice-clone.css";
import "./styles/voice-delivery.css";
import "./styles/voice-design.css";
import "./styles/voice-script-editor.css";
import "./styles/voice-studio-main.css";
import "./styles/film-studio.css";
import "./styles/document-renderer.css";
import "./styles/document-rich.css";
import "./styles/settings-loading.css";
import "./styles/surface-elevation-parity.css";
import "./styles/source-shell-parity.css";
import "./styles/source-primitives-parity.css";
import "./styles/source-message-dialog.css";
import "./styles/chapter-studio-parity.css";
import "./styles/design-tokens.css";
import "./styles/pyside-page-parity.css";
import "./styles/toast.css";
import "./styles/pet-companion.css";
import "./styles/task-focus-panel.css";
import "./styles/dashboard-bookshelf.css";
import "./styles/reader-folio.css";
import "./styles/typography-system.css";

const rootElement = document.getElementById("root");

if (rootElement === null) {
  throw new Error("NIMO UI parity root element is missing");
}
const appRootElement = rootElement;

async function bootstrap(): Promise<void> {
  const clients = await loadEngineClients();
  createRoot(appRootElement).render(
    <StrictMode>
      <ToastProvider>
        <RuntimeRecoveryBoundary>
          <App
            engineClient={clients.engineClient}
            engineCommandClient={clients.engineCommandClient}
            runtimeConfig={clients.runtimeConfig}
          />
        </RuntimeRecoveryBoundary>
      </ToastProvider>
    </StrictMode>,
  );
}

void bootstrap().catch((error: unknown) => {
  console.error("NIMO bootstrap failed", error);
  createRoot(appRootElement).render(
    <RuntimeRecoverySurface
      detail={error instanceof Error
        ? `无法初始化桌面界面：${error.message}`
        : "无法初始化引擎客户端。请恢复界面后重试。"}
      onRecover={() => window.location.reload()}
      title="桌面启动未完成"
    />,
  );
});
