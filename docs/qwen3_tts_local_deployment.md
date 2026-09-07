# Qwen3-TTS 本地部署与配音分工

本项目通过独立的 `qwen3-tts-sidecar` 运行官方 `qwen-tts` 包。桌面端和 API 只访问
`/v1` HTTP 合约，因此主程序不加载 PyTorch、CUDA 或 FlashAttention；升级 Qwen 运行时也
不会影响写作与桌面依赖。

## 固定模型分工

| 配音工作 | 模型 | 实现规则 |
| --- | --- | --- |
| 正式人声 | `Qwen3-TTS-12Hz-1.7B-CustomVoice` | 章节合成的默认 CustomVoice；角色系统音色使用官方九个预置 speaker。 |
| 快速试听 | `Qwen3-TTS-12Hz-0.6B-CustomVoice` | 只用于系统 CustomVoice 的“试听”按钮，成品合成不会降级。 |
| 品牌/角色音色设计 | `Qwen3-TTS-12Hz-1.7B-VoiceDesign` | 先生成短参考音频，再自动交给 Base 建立可复用提示。 |
| 授权音色克隆 | `Qwen3-TTS-12Hz-1.7B-Base` | 保存参考音频、逐字转写和可重建元数据；GPU 内存对象不落盘。 |
| 短 SFX | Stable Audio 3 Small-SFX（本地） | 仅用于敲门、脚步、器物碰撞等短促前景效果。 |
| 长环境音/音乐 | MiniMax Music 云端 API | 生成可循环的环境底床与无歌词 BGM；资产默认进入待审核库。 |

VoiceDesign 的结果不是把一次性 GPU 对象交给主程序。sidecar 会保存设计生成的参考 WAV，
再用 Base 的 `create_voice_clone_prompt` 建立提示。进程重启后可从该 WAV 和参考文本重建，
这与 Qwen 官方推荐的“Voice Design then Clone”流程一致。

已设计或已克隆的稳定角色 ID 在正式合成和试听时都会优先走 Base，而不会被 0.6B 试听模型
替换；这是一项有意的身份一致性保护。0.6B 只服务于尚未固化为专属声纹的 CustomVoice 快速试听。

## 安装 sidecar

建议使用单独 Python 3.12 环境，避免与 Novel Forge 主程序的 Torch/CUDA 依赖互相污染：

```bash
conda create -n qwen3-tts python=3.12 -y
conda activate qwen3-tts
pip install -U qwen-tts "uvicorn[standard]" fastapi numpy python-multipart
# NVIDIA GPU 且环境兼容时可选；不兼容时去掉 --flash-attention 即可。
pip install -U flash-attn --no-build-isolation
```

安装 Novel Forge 后启动：

```bash
qwen3-tts-sidecar \
  --host 127.0.0.1 --port 8011 \
  --device cuda:0 --dtype bfloat16 \
  --voice-store <应用数据目录>/assets/tts-voices/qwen3 \
  --flash-attention
```

首次请求会按官方模型 ID 自动下载权重。若需预下载，可使用官方 ModelScope 或
Hugging Face CLI；将模型 ID 替换为本表对应的四个模型即可。sidecar 默认只绑定
`127.0.0.1`。跨机器部署时必须配置 `--api-key`、TLS 和网络访问控制，且 Base 克隆的
浏览器采集场景应使用受信任 HTTPS。

## Novel Forge 设置

在“配音工作室 → 平台设置”选择 **Qwen3-TTS（本地）**，填写 sidecar 地址；也可在 `.env`
设置：

```env
NOVEL_FORGE_TTS_DEFAULT_PROVIDER=qwen3
NOVEL_FORGE_TTS_QWEN3_BASE_URL=http://127.0.0.1:8011/v1
NOVEL_FORGE_TTS_QWEN3_FORMAL_MODEL=Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice
NOVEL_FORGE_TTS_QWEN3_PREVIEW_MODEL=Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice
NOVEL_FORGE_TTS_QWEN3_DESIGN_MODEL=Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign
NOVEL_FORGE_TTS_QWEN3_CLONE_MODEL=Qwen/Qwen3-TTS-12Hz-1.7B-Base

NOVEL_FORGE_SOUND_GENERATION_ENABLED=true
NOVEL_FORGE_SOUND_GENERATION_AUTO_GENERATE=true
NOVEL_FORGE_SOUND_GENERATION_AUTO_APPROVE=false
NOVEL_FORGE_SOUND_GENERATION_MINIMAX_API_KEY=your_minimax_key
```

`GET /v1/health` 会加载正式 CustomVoice 并完成一次极短推理；只有模型真正可用时才返回
健康。`GET /v1/capabilities` 和 `GET /v1/voices` 供桌面端安全地展示功能和官方九个预置
speaker。

## 授权克隆质量与安全

桌面端会在 Qwen3-TTS 克隆前要求明确确认授权。建议上传干净、单人、无背景音乐的参考片段，
并填写参考音频的逐字转写。缺少转写时系统必须显式选择 `x_vector_only_mode`，可以运行但
官方说明其克隆质量可能降低。参考音频和重建提示属于敏感创作资产，应限制项目目录和备份的
访问权限。

生成音效、环境声和音乐仍采用“先写入声音资源库、审核后混音”的流程。不要把未审核的云端
生成资产自动发布；商业使用前需按实际 MiniMax、Stable Audio 权利条款进行复核。
