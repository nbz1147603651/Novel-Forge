/** Explicit dev-only entry for the unified repair workbench. */
import { useState } from "react";
import { createRoot } from "react-dom/client";

import { RepairWorkbench } from "../../src/components/RepairWorkbench";
import { LegacyLocalEngineClient } from "../../src/lib/legacy-engine-client";
import { ThemeProvider } from "../../src/theme/ThemeProvider";
import "../../src/styles/global.css";
import "../../src/styles/theme-system.css";
import "../../src/styles/design-tokens.css";
import "../../src/styles/typography-system.css";

const client = new LegacyLocalEngineClient({
  baseUrl: "http://127.0.0.1:18791",
  maxRetries: 0,
});

function Fixture() {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState("未保存草稿必须保留");
  return (
    <ThemeProvider>
      <main style={{ minHeight: "100vh", padding: 24 }}>
        <h1>修复工作台离线验收</h1>
        <p>下层章台保持挂载，用于检查覆盖层关闭与焦点恢复。</p>
        <button onClick={() => setOpen(true)} type="button">打开修复工作台</button>
        <textarea
          aria-label="章台未保存草稿"
          onChange={(event) => setDraft(event.target.value)}
          value={draft}
        />
        {open && (
          <RepairWorkbench
            chapterNumber={1}
            commandClient={client}
            engineClient={client}
            onClose={() => {
              document.body.setAttribute("data-repair-closed", "true");
              setOpen(false);
            }}
            projectId="book"
          />
        )}
      </main>
    </ThemeProvider>
  );
}

if (!import.meta.env.DEV) throw new Error("Repair workbench fixture is development-only");
createRoot(document.getElementById("root")!).render(<Fixture />);
