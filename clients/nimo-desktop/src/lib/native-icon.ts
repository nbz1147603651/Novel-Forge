import { brandSymbolSvg } from "../brand/symbol";

const NATIVE_ICON_SIZE = 256;

function isTauriRuntime(): boolean {
  return "__TAURI_INTERNALS__" in window;
}

async function rasterizeBrandIcon(color: string): Promise<Uint8Array> {
  const blob = new Blob([brandSymbolSvg(color)], { type: "image/svg+xml" });
  const imageUrl = URL.createObjectURL(blob);
  const image = new Image();
  try {
    await new Promise<void>((resolve, reject) => {
      image.onload = () => resolve();
      image.onerror = () => reject(new Error("NIMO symbol could not be rasterized"));
      image.src = imageUrl;
    });
    const canvas = document.createElement("canvas");
    canvas.width = NATIVE_ICON_SIZE;
    canvas.height = NATIVE_ICON_SIZE;
    const context = canvas.getContext("2d");
    if (context === null) {
      throw new Error("Canvas 2D context is unavailable");
    }
    context.clearRect(0, 0, NATIVE_ICON_SIZE, NATIVE_ICON_SIZE);
    context.drawImage(image, 0, 0, NATIVE_ICON_SIZE, NATIVE_ICON_SIZE);
    const png = await new Promise<Blob>((resolve, reject) => canvas.toBlob((value) => value === null ? reject(new Error("NIMO icon PNG encoding failed")) : resolve(value), "image/png"));
    return new Uint8Array(await png.arrayBuffer());
  } finally {
    URL.revokeObjectURL(imageUrl);
  }
}

/**
 * Uses Tauri's supported runtime window-icon API. On macOS that API is the
 * native app/window icon path, so the Dock never keeps a stale theme color.
 */
export async function syncNativeWindowIcon(color: string): Promise<void> {
  if (!isTauriRuntime()) {
    return;
  }
  try {
    const [{ getCurrentWindow }, png] = await Promise.all([
      import("@tauri-apps/api/window"),
      rasterizeBrandIcon(color),
    ]);
    await getCurrentWindow().setIcon(png);
  } catch {
    // A web preview has no native window. Native capability failures must not
    // block a visual theme change in the editor surface.
  }
}
