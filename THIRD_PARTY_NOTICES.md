# 第三方开源声明与致谢

NIMO / Novel Forge 的自有代码采用 [MIT License](LICENSE)。本声明简要列出使用的开源生态和参考来源；第三方代码、模型、素材仍适用其原许可证，项目的 MIT 许可不替代它们。

## 参考项目

| 来源 | 用途 | 许可与声明 |
| --- | --- | --- |
| [Humanizer · Siqi Chen](https://github.com/blader/humanizer) | AI 写作痕迹识别与编辑思路参考 | MIT；[原始版权与许可](third_party/licenses/humanizer.txt) |
| [Humanizer-zh · 歸藏](https://github.com/op7418/Humanizer-zh) | 中文写作模式与提示词参考 | MIT；[原始版权与许可](third_party/licenses/humanizer-zh.txt) |
| [Stop Slop · Hardik Pandya](https://github.com/hardikpandya/stop-slop) | Humanizer-zh 注明的上游写作规则参考 | [上游 MIT 许可](https://github.com/hardikpandya/stop-slop/blob/main/LICENSE) |
| [short-drama · 0xsline](https://github.com/0xsline/short-drama) | 短剧策划与影视工作流参考 | MIT；[原始版权与许可](third_party/licenses/short-drama.txt) |

参考目录 `Reference/` 不随仓库分发。上述引用说明来源，不表示直接打包上游完整程序。Humanizer 的上游还引用了 [Wikipedia: Signs of AI writing](https://en.wikipedia.org/wiki/Wikipedia:Signs_of_AI_writing)；若复制其原文，须另行遵循该页面的署名及共享许可，不能用 MIT 覆盖。

## 软件依赖

| 生态 | 主要项目 | 许可概况 |
| --- | --- | --- |
| Python 服务与配置 | FastAPI、Pydantic、Typer、Rich、SQLAlchemy、HTTPX、Uvicorn、Jinja2 | MIT / BSD 等，以安装版本为准 |
| 模型与检索 | OpenAI SDK、Anthropic SDK、Hugging Face Hub、Zvec、NumPy、jieba、OpenCC | MIT / Apache-2.0 / BSD 等，SDK 许可不代表云服务许可 |
| 前端 | React、Radix UI、TanStack Table、XYFlow、Vite、TypeScript | MIT / Apache-2.0 等 |
| 桌面运行时 | Tauri、Tauri plugins、Serde | MIT 或 Apache-2.0 等 |
| 音频与时间线 | pydub、imageio-ffmpeg、OpenTimelineIO | MIT / BSD / Apache-2.0；FFmpeg 二进制另有许可 |
| 兼容后备 UI | PySide6 / Qt | LGPL / GPL 或商业许可，具体以所分发组件为准 |

完整直接依赖见 [pyproject.toml](pyproject.toml)、[前端 package.json](clients/nimo-desktop/package.json) 和 [Cargo.toml](clients/nimo-desktop/src-tauri/Cargo.toml)。JavaScript / Rust 的解析版本分别记录于 [pnpm-lock.yaml](pnpm-lock.yaml) 和 [Cargo.lock](clients/nimo-desktop/src-tauri/Cargo.lock)。Python 当前使用依赖范围约束，不能把本机环境视为固定分发版本。

分发打包程序时，须按实际包含的直接及传递依赖保留许可证和 NOTICE。特别是 PySide6 / Qt、FFmpeg 及模型运行时，应根据实际构建产物补齐声明；这份源码说明不是打包产物的完整许可证清单。

## 可选外部工具与模型

- [Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS/blob/main/LICENSE)、[CosyVoice](https://github.com/FunAudioLLM/CosyVoice/blob/main/LICENSE)：上游代码采用 Apache-2.0。
- [OpenVoice](https://github.com/myshell-ai/OpenVoice/blob/main/LICENSE)：上游 MIT 许可。
- [ComfyUI](https://github.com/Comfy-Org/ComfyUI/blob/master/LICENSE)：独立外部工具，采用 GPL-3.0；其代码不随本仓库分发。

模型权重、其他可选音频模型、节点插件和云服务分别遵循模型卡、上游许可证或服务条款。本仓库不包含模型权重、第三方录音或字体文件。项目维护者已确认仓库中的 NIMO Logo 与桌宠可随本项目公开分发；它们作为项目素材纳入本次开源授权。截图来源及核验范围见[发布记录](docs/open-source-release.md)。
