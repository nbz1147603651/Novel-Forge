# 本地开源 TTS Provider 协议

Novel Forge 通过一个轻量 HTTP sidecar 接入本地 TTS。主应用只依赖稳定协议，模型运行时、
GPU 框架和权重版本留在独立进程中，避免 Qwen3-TTS、CosyVoice、Fish Speech、GPT-SoVITS
之间的依赖冲突影响写作主程序。

## 基础接口（必须）

`POST /v1/audio/speech`

请求兼容 OpenAI Speech API：

```json
{
  "model": "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign",
  "input": "需要合成的文本",
  "voice": "stable-character-voice-id",
  "speed": 1.0,
  "response_format": "mp3"
}
```

响应为对应格式的原始音频字节。服务端必须用非 2xx 状态码返回请求错误，不得以空音频表示成功。

## 能力协商（推荐）

`GET /v1/capabilities`

```json
{
  "synthesis": true,
  "voice_clone": true,
  "voice_design": true,
  "system_voice_catalog": true,
  "local_reference_audio": true
}
```

未实现该接口时，主应用自动降级为“仅合成 + 音色目录”，不会盲目展示克隆或设计按钮。

## 可选音色接口

- `GET /v1/voices`：返回 `{"voices": [{"voice_id": "...", "name": "...", "gender": "neutral", "tags": []}]}`；也接受 `data` 作为列表字段。
- `POST /v1/voices/clone`：本地文件使用 multipart，字段为 `file`、`voice_id`、`clone_prompt`；远端引用使用 JSON 字段 `reference_audio`。
- `POST /v1/voices/design`：JSON 字段为 `description`、`preview_text`，返回稳定 `voice_id` 与 base64 编码的 `preview_audio`。

克隆与设计成功响应示例：

```json
{
  "voice_id": "novel-role-c1-v1",
  "preview_audio": "<base64>",
  "preview_audio_format": "wav",
  "message": "ready"
}
```

`voice_id` 必须可跨请求复用。对于 Qwen3-TTS VoiceDesign 这类直接生成音频、但不天然生成长期
音色 ID 的模型，sidecar 应将设计结果转换为可复用提示缓存，或先生成参考音频再通过 Base
模型建立克隆提示；不要让主应用保存 GPU 内存对象。

## 生产约束

- sidecar 建议绑定 `127.0.0.1`；跨机器部署必须使用 TLS、Bearer Token 和网络访问控制。
- 上传音频需限制格式、大小、时长并使用临时目录；日志不得记录 API Key 或原始音频内容。
- 音色缓存键至少包含模型版本、角色特质摘要和参考音频哈希，模型升级后应生成新版本 ID。
- 推荐用独立容器或 Python 环境运行模型。Qwen3-TTS 与其他推理栈可能要求不同的 PyTorch、Transformers 或 CUDA 版本。
- 健康检查应覆盖模型已加载和一次最小推理；仅端口可连接不足以判定可用。

参考实现方向：

- [Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS)
- [CosyVoice](https://github.com/FunAudioLLM/CosyVoice)
