/** Static Ollama catalogue metadata; operational state always comes from Engine. */

export const OLLAMA_RECOMMENDED_MODELS: readonly { readonly label: string; readonly model: string }[] = [
  { label: "轻量生成", model: "llama3.2" },
  { label: "中文通用", model: "qwen2.5:7b" },
  { label: "长文更稳", model: "qwen2.5:14b" },
  { label: "代码辅助", model: "qwen2.5-coder:7b" },
  { label: "推理实验", model: "deepseek-r1:7b" },
  { label: "经典通用", model: "mistral" },
  { label: "语义嵌入", model: "nomic-embed-text" },
  { label: "高质量嵌入", model: "mxbai-embed-large" },
  { label: "多语嵌入", model: "bge-m3" },
];

export function formatOllamaSize(bytes: number): string {
  if (bytes <= 0) return "";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let size = bytes;
  let index = 0;
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024;
    index += 1;
  }
  return index === 0 ? `${Math.round(size)} ${units[index]}` : `${size.toFixed(1)} ${units[index]}`;
}
