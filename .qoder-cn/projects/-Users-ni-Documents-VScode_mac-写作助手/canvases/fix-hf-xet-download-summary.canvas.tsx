import { Divider, Grid, H1, H2, Stack, Stat, Table, Text, Callout } from 'qoder/canvas';

export default function FixHFDownload() {
  return (
    <Stack gap={20}>
      <H1>Fix HuggingFace Xet Download Failure</H1>
      <Text tone="secondary">huggingface_hub 1.23.0 xet 协议导致大文件下载失败修复报告</Text>

      <Grid columns={4} gap={16}>
        <Stat value="2" label="Files Modified" />
        <Stat value="3" label="Root Causes Fixed" tone="success" />
        <Stat value="12" label="Tests Passing" tone="success" />
        <Stat value="0" label="Ruff Warnings" tone="success" />
      </Grid>

      <Divider />

      <H2>问题诊断</H2>

      <Callout tone="warning">
        <Text weight="medium">症状：HF Token 和模型访问权限均有效，但大文件下载失败</Text>
        <Text size="small">
          错误信息 "cannot find the requested files in the local cache" 并非权限问题。
          实际是 huggingface_hub 1.23.0 默认使用的 xet 下载协议在当前环境下无法正常工作。
        </Text>
      </Callout>

      <Stack gap={8}>
        <Text weight="medium">关键证据：第二次下载尝试的实际结果</Text>
        <Text size="small">• 11 个小文件通过标准 HTTP 成功下载（LICENSE, README, config 等，共 96KB）</Text>
        <Text size="small">• 3 个大文件通过 xet 协议下载失败（model.safetensors 2.27GB, t5gemma 1.18GB, tokenizer 34MB）</Text>
        <Text size="small">• trees/*.json 中所有大文件都有 xet_hash 字段，证实使用了 xet 协议</Text>
        <Text size="small">• 所有 snapshots 中的 symlink 都指向有效 blob（非断链），证明小文件下载完好</Text>
      </Stack>

      <Divider />

      <H2>三重修复策略</H2>

      <Table
        headers={['修复', '实现', '作用']}
        rows={[
          ['清理损坏缓存', 'shutil.rmtree(partial_cache)', '删除之前失败下载留下的部分缓存，确保干净起点'],
          ['强制重新下载', 'force_download=True', '防止 snapshot_download 被陈旧缓存元数据混淆'],
          ['禁用 xet 协议', 'HF_HUB_DISABLE_XET=1', '强制使用标准 HTTP 下载大文件，finally 块中恢复环境变量'],
        ]}
        rowTone={['success', 'success', 'success']}
      />

      <Stack gap={8}>
        <Text weight="medium">修复后下载流程：</Text>
        <Text size="small">1. 创建 cache_dir 目录</Text>
        <Text size="small">2. 删除该模型的已有缓存（如有）</Text>
        <Text size="small">3. 设置 HF_HUB_DISABLE_XET=1 环境变量</Text>
        <Text size="small">4. 调用 snapshot_download(force_download=True, token=...)</Text>
        <Text size="small">5. finally 块恢复 HF_HUB_DISABLE_XET 原始值</Text>
        <Text size="small">6. 验证下载完整性（_is_installed 三层检查）</Text>
      </Stack>

      <Divider />

      <H2>修改文件清单</H2>

      <Table
        headers={['文件', '修改内容', '类型']}
        rows={[
          ['novel_forge/tts/sound_generation/model_manager.py', 'download() 三重修复 + import os', '核心修复'],
          ['tests/unit/test_stable_audio_model_manager.py', 'mock 签名更新 + force_download 断言 + env 恢复验证', '测试适配'],
        ]}
        rowTone={['success', 'info']}
      />

      <Divider />

      <H2>验证结果</H2>

      <Grid columns={2} gap={16}>
        <Stack gap={8}>
          <Text weight="medium">测试覆盖：</Text>
          <Text size="small">• test_manager_download_uses_configured_hf_cache → PASS</Text>
          <Text size="small">• test_manager_lists_and_safely_deletes → PASS</Text>
          <Text size="small">• test_manager_rejects_non_stable_audio_entries → PASS</Text>
          <Text size="small">• test_registry_stage_one_bgm_requires_key → PASS</Text>
          <Text size="small">• 8 个 _is_installed 回归测试 → 全部 PASS</Text>
        </Stack>

        <Stack gap={8}>
          <Text weight="medium">环境变量安全性：</Text>
          <Text size="small">• 下载前保存 HF_HUB_DISABLE_XET 原始值</Text>
          <Text size="small">• finally 块中无条件恢复</Text>
          <Text size="small">• 测试中断言恢复后值为 None</Text>
          <Text size="small">• 不影响其他进程或后续操作</Text>
        </Stack>
      </Grid>

      <Divider />

      <Callout tone="success">
        <Text weight="medium">预期效果</Text>
        <Text size="small">
          用户重新点击下载后，系统将：清理 96KB 的损坏缓存，禁用 xet 协议，
          通过标准 HTTP 从头下载完整 3.5GB 模型文件。
          Token 和访问权限已验证有效，下载应能顺利完成。
        </Text>
      </Callout>

      <Text tone="secondary" size="small">
        Generated for Qoder Quest - Fix HuggingFace Xet Download Failure
      </Text>
    </Stack>
  );
}
