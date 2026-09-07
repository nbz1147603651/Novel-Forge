import { Callout, Divider, Grid, H1, H2, Pill, Stack, Stat, Table, Text } from "qoder/canvas";

export default function VoiceStudioTextSourceFix() {
  return (
    <Stack gap={20}>
      <H1>配音文本源约束修复</H1>
      <Text tone="secondary" size="small">
        确保配音界面只读取最终落盘正文，杜绝中间草稿被用作配音脚本源
      </Text>

      <Grid columns={3} gap={16}>
        <Stat value="3" label="修复任务" />
        <Stat value="1" label="改动文件" />
        <Stat value="26" label="通过测试" tone="success" />
      </Grid>

      <Divider />

      <H2>问题诊断</H2>
      <Callout tone="warning" title="原风险">
        配音界面在找不到定稿正文时会静默回退到 drafts/ 中的最新版本草稿，
        导致中间质量文本被转换为配音脚本并合成语音，浪费 LLM 调用与 TTS 资源。
      </Callout>

      <Divider />

      <H2>修复内容</H2>
      <Table
        headers={["任务", "改动位置", "修复策略"]}
        rows={[
          [
            "_load_chapter_text 移除草稿回退",
            "page.py L2798-2805",
            "删除 draft_dir 回退逻辑，仅读取 chapters/ 目录下的最终落盘文件",
          ],
          [
            "_populate_chapter_combo 排除草稿章节",
            "page.py L2885",
            "移除 drafts_dir 扫描块，下拉列表仅显示已定稿章节",
          ],
          [
            "改善提示文案（2 处）",
            "page.py L4419, L4617",
            "「未找到文本」改为「第 N 章尚未定稿，请先完成章节生成流程后再进行配音」",
          ],
        ]}
      />

      <Divider />

      <H2>验证结果</H2>
      <Stack gap={8}>
        <Pill tone="success">26/26 voice_studio 测试通过</Pill>
        <Pill tone="success">ruff lint 零告警</Pill>
        <Pill tone="success">draft_dir / drafts_dir 引用已清零</Pill>
      </Stack>

      <Text tone="secondary" size="small">
        novel_forge/desktop/pages/voice_studio/page.py
      </Text>
    </Stack>
  );
}
