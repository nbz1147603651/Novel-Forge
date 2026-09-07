import { Divider, Grid, H1, H2, Stack, Stat, Table, Text, Callout } from 'qoder/canvas';

export default function FixFakeInstallDetection() {
  return (
    <Stack gap={20}>
      <H1>Fix Fake Install Detection</H1>
      <Text tone="secondary">StableAudioModelManager._is_installed() 误判修复完成报告</Text>

      <Grid columns={4} gap={16}>
        <Stat value="3" label="Files Modified" />
        <Stat value="8" label="New Tests Added" tone="success" />
        <Stat value="12" label="Tests Passing" tone="success" />
        <Stat value="0" label="Ruff Warnings" tone="success" />
      </Grid>

      <Divider />

      <H2>问题诊断</H2>

      <Callout tone="warning">
        <Text weight="medium">症状：UI 显示"已安装"但模型实际未下载</Text>
        <Text size="small">
          本地缓存目录仅 28KB（预期 2.1GB），包含 HF Hub 元数据和空目录骨架，
          但 _is_installed() 仅检查 snapshots/ 下是否有文件，对断链 symlink 和极小缓存未做校验。
        </Text>
      </Callout>

      <Stack gap={8}>
        <Text weight="medium">根因分析：</Text>
        <Text size="small">• snapshot_download() 因缺少 HF Token 权限中断，留下部分缓存结构</Text>
        <Text size="small">• snapshots/ 目录存在但内部仅有空子目录或断链 symlink</Text>
        <Text size="small">• blobs/ 包含 2 个小元数据文件（11.8KB + 5.7KB），无模型权重</Text>
        <Text size="small">• 旧逻辑 any(path.is_file() for ...) 对部分状态返回 True，导致 UI 误报</Text>
      </Stack>

      <Divider />

      <H2>修复方案：三层验证</H2>

      <Table
        headers={['检查层', '修复前', '修复后']}
        rows={[
          ['文件存在性', 'any(is_file())', '跳过断链 symlink，只计有效文件'],
          ['最低大小', '无', 'max(100MB, estimated // 20) ≈ 113.5MB'],
          ['descriptor 传递', '不传', 'list_models() 和 model_status() 传入 descriptor'],
        ]}
        rowTone={['warning', 'danger', 'success']}
      />

      <Stack gap={8}>
        <Text weight="medium">核心代码逻辑：</Text>
        <Text size="small">1. 遍历 snapshots/ 下所有条目，跳过 is_symlink() and not exists() 的断链</Text>
        <Text size="small">2. 若找不到任何有效文件 → 返回 False</Text>
        <Text size="small">3. 若有 descriptor 且 estimated_download_bytes &gt; 0，检查 _directory_size() &gt;= min_bytes</Text>
        <Text size="small">4. min_bytes = max(100_000_000, estimated_download_bytes // 20)</Text>
      </Stack>

      <Divider />

      <H2>修改文件清单</H2>

      <Table
        headers={['文件', '修改内容', '类型']}
        rows={[
          ['novel_forge/tts/sound_generation/model_manager.py', '_is_installed() 三层验证 + list_models/model_status 传 descriptor', '核心修复'],
          ['tests/unit/test_stable_audio_is_installed.py', '8 个回归测试覆盖断链/小缓存/正常场景', '新增测试'],
          ['tests/unit/test_stable_audio_model_manager.py', '_write_cached_sfx_model 写 120MB 实际字节', '适配更新'],
        ]}
        rowTone={['success', 'info', 'info']}
      />

      <Divider />

      <H2>测试验证</H2>

      <Grid columns={2} gap={16}>
        <Stack gap={8}>
          <Text weight="medium">新增 8 个回归测试：</Text>
          <Text size="small">• test_no_snapshots_dir → False</Text>
          <Text size="small">• test_empty_snapshots_dir → False</Text>
          <Text size="small">• test_broken_symlink_only → False（假安装场景）</Text>
          <Text size="small">• test_tiny_cache_below_threshold → False（部分下载）</Text>
          <Text size="small">• test_no_descriptor_still_checks_files → False</Text>
          <Text size="small">• test_valid_cache_above_threshold → True</Text>
          <Text size="small">• test_no_descriptor_with_file → True</Text>
          <Text size="small">• test_valid_symlink_to_real_blob → True</Text>
        </Stack>

        <Stack gap={8}>
          <Text weight="medium">既有测试状态：</Text>
          <Text size="small">• test_manager_lists_and_safely_deletes → PASS</Text>
          <Text size="small">• test_manager_download_uses_configured_hf_cache → PASS</Text>
          <Text size="small">• test_manager_rejects_non_stable_audio_entries → PASS</Text>
          <Text size="small">• test_registry_stage_one_bgm_requires_key → PASS</Text>
        </Stack>
      </Grid>

      <Divider />

      <Callout tone="success">
        <Text weight="medium">修复效果</Text>
        <Text size="small">
          当前磁盘上 28KB 的缓存残骸现在被正确识别为"未下载"，UI 不再显示误导性的"已安装"徽章。
          用户需完成 HF 授权、填入 Token 后重新下载完整 2.1GB 模型。
        </Text>
      </Callout>

      <Text tone="secondary" size="small">
        Generated for Qoder Quest - Fix Fake Install Detection Implementation
      </Text>
    </Stack>
  );
}
