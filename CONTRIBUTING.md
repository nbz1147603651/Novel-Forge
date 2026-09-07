# 参与贡献

感谢帮助改进 NIMO / Novel Forge。问题和功能建议通过 [Issues](https://github.com/nbz1147603651/Novel-Forge/issues) 提交；安全问题请先阅读 [SECURITY.md](SECURITY.md)。

## 开发准备

按 [README](README.md) 安装 Python 和前端依赖，然后安装开发工具：

```bash
python -m pip install -e ".[dev,pyside]"
pnpm install --frozen-lockfile
```

本地 `.env`、模型配置、作品、参考材料及日志不应进入提交。测试使用合成数据和 MockAdapter，不要求贡献者提供真实密钥或付费调用。

## 修改与验证

1. 阅读根目录和修改目录下的 `AGENTS.md`。涉及创作流程时，同步阅读并更新[当前工作流](docs/novel-workflow-current.md)与[共创设计](docs/novel-authoring-control-design.md)。
2. 聚焦一个可复现的问题。NIMO 是新 UI 的唯一目标；共享授权、安全和 Engine 契约的修改仍需覆盖受影响的 CLI / API / PySide 后备端。
3. 用对应测试验证行为和恢复边界。不要用删除门禁、改写用户数据或降低断言强度使测试通过。
4. 检查差异中是否含密钥、正文、录音、个人路径或不明来源素材。

```bash
python scripts/check_public_release.py
ruff check novel_forge tests
python scripts/check_agents_md_drift.py
pytest tests/unit/<对应测试文件>.py -q
pnpm ui:check
pnpm ui:test
```

上面的测试路径是占位说明，需换成实际文件。完整矩阵及必需检查以 [CI](.github/workflows/ci.yml) 为准；前端未改动时无需每次执行全部 UI 测试。串行 Qt 场景按对应测试说明运行。

## 提交 PR

说明触发问题、修改后的行为、验证结果和剩余限制。界面变更附合成数据截图；不要上传原始模型调用日志或真实小说片段。

新增依赖、复制或改编第三方代码、提示词和素材时，同步更新 [第三方声明](THIRD_PARTY_NOTICES.md)，保留适用的版权、许可证及 NOTICE。不能把“公开可下载”当作授权。贡献的自有部分按仓库 MIT 许可提交，第三方部分仍适用原许可。

请尊重不同写作风格和贡献者，围绕具体问题讨论。模型评分和去 AI 味建议不能覆盖作者明确意图。
