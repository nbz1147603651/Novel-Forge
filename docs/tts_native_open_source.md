# OpenVoice V2 与 CosyVoice 3 原生接入

本项目保留 `local` 作为 OpenAI Speech 兼容 sidecar，同时提供两个独立平台：

- `cosyvoice`：直接调用 FunAudioLLM 官方 FastAPI 的 `/inference_*` 接口；
- `openvoice`：进程内调用 OpenVoice V2 的 `ToneColorConverter` 与 MeloTTS。

这两个平台均以“参考音频克隆”为真实声纹来源。角色特质用于 CosyVoice `instruct2`
表达指令、以及语速/音高/音量等演绎控制；它们不能替代授权参考音频，也不会伪造
文本设计音色能力。因此 UI 会显示“上传音频克隆”，并自动隐藏“AI 音色设计”。

## CosyVoice 3

使用官方仓库的 FastAPI 运行时，并推荐 `FunAudioLLM/Fun-CosyVoice3-0.5B-2512`：

```bash
cd runtime/python
docker build -t cosyvoice:v1.0 .
docker run -d --runtime=nvidia -p 50000:50000 cosyvoice:v1.0 \
  /bin/bash -c 'cd /opt/CosyVoice/CosyVoice/runtime/python/fastapi && \
  python3 server.py --port 50000 --model_dir FunAudioLLM/Fun-CosyVoice3-0.5B-2512'
```

配置：

```env
NOVEL_FORGE_TTS_DEFAULT_PROVIDER=cosyvoice
NOVEL_FORGE_TTS_COSYVOICE_BASE_URL=http://127.0.0.1:50000
NOVEL_FORGE_TTS_COSYVOICE_MODE=instruct2
```

`instruct2` 是默认模式：参考音频确定声纹，角色特质简报控制表达。`zero_shot` 模式
要求给出与参考音频逐字一致的转写；不能将角色画像当作转写。官方服务返回 16-bit PCM，
适配器会在落盘前封装为 WAV，避免出现“mp3 扩展名但内容为 PCM”的损坏音频。

## OpenVoice V2

安装 OpenVoice V2、MeloTTS，并下载官方 `checkpoints_v2`。应用仅在选择 `openvoice`
后才导入这些可选依赖：

```env
NOVEL_FORGE_TTS_DEFAULT_PROVIDER=openvoice
NOVEL_FORGE_TTS_OPENVOICE_CHECKPOINT_DIR=./models/openvoice/checkpoints_v2
NOVEL_FORGE_TTS_OPENVOICE_DEVICE=auto
NOVEL_FORGE_TTS_OPENVOICE_LANGUAGE=ZH
```

首次克隆会将授权参考音频复制到 `tts_openvoice_voice_store`，提取并持久化 target
tone-color embedding。后续合成使用稳定角色 `voice_id` 加载 embedding，先由 MeloTTS
生成基础语音，再由 OpenVoice 转换音色。该平台输出 WAV。

## 运维边界

- 参考音频需要取得说话人授权；日志不可记录原始音频内容。
- 模型/权重升级后应清空或切换 `*_VOICE_STORE`，重新建立角色声纹。
- OpenVoice 运行时与主程序共享 Python 环境；如果 CUDA/PyTorch 版本冲突，优先使用
  独立虚拟环境或容器运行桌面应用。
- CosyVoice 的服务地址应只绑定可信网络；跨机器时使用 TLS、鉴权和访问控制。
