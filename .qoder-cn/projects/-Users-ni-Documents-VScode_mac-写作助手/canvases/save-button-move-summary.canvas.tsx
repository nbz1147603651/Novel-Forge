import { Divider, H1, H2, Row, Stack, Stat, Table, Text } from 'qoder/canvas';

export default function SaveButtonMoveSummary() {
  return (
    <Stack gap={16}>
      <H1>保存设置按钮迁移 — 完成报告</H1>
      <Text tone="secondary" size="small">
        配音工作室页面 UI 布局调整：将"保存设置"按钮放到 Tab 栏同一行的最右侧（红框位置）
      </Text>

      <Row gap={12}>
        <Stat value="1" label="修改文件" />
        <Stat value="3" label="变更步骤" tone="success" />
        <Stat value="3/3" label="验证通过" tone="success" />
      </Row>

      <Divider />

      <H2>变更详情</H2>
      <Table
        headers={['步骤', '操作', '位置', '说明']}
        rows={[
          [
            'Task 1',
            '回退错误修改',
            '_create_voice_team_tab() 第 987-989 行',
            '删除配音团队 Tab 内容区域中误加的按钮代码，恢复为 right_card 后直接 return',
          ],
          [
            'Task 2',
            'Tab 栏包裹为横向布局',
            '_setup_ui() 第 674-707 行',
            '用 tab_row (QHBoxLayout) 包裹 QTabWidget，addStretch + 按钮推到最右',
          ],
          [
            'Task 3',
            '验证',
            '全局 grep + 代码审查',
            '_save_settings_btn 仅在 _setup_ui 创建一次，回调不变，平台设置 Tab 底部干净',
          ],
        ]}
      />

      <H2>验证结果</H2>
      <Table
        headers={['检查项', '结果']}
        rows={[
          ['_save_settings_btn 仅在 _setup_ui 中创建一次（第 703-705 行）', '通过'],
          ['_on_save_settings 回调逻辑不变', '通过'],
          ['平台设置 Tab 底部无按钮残留（第 1539 行仅 addStretch）', '通过'],
        ]}
        rowTone={['success', 'success', 'success']}
      />

      <H2>修改文件</H2>
      <Text>novel_forge/desktop/pages/voice_studio/page.py</Text>
    </Stack>
  );
}
