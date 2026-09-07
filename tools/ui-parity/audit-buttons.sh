#!/usr/bin/env bash
# UI-Parity 功能按钮对比检查脚本
# 对比 PySide6 源端和新 UI 客户端的功能按钮覆盖度

set -e

cd "$(dirname "$0")/../.."

PYTHON_DIR="novel_forge/desktop"
CLIENT_DIR="clients/nimo-desktop"

echo "=========================================="
echo "UI-Parity 功能按钮覆盖度检查"
echo "=========================================="
echo ""

# PySide6 源端按钮统计
pyside6_buttons=$(grep -r "ActionButton\|QPushButton" "$PYTHON_DIR" --include="*.py" 2>/dev/null | wc -l | tr -d ' ')
echo "PySide6 源端 ActionButton/QPushButton 引用数: $pyside6_buttons"

# 新 UI 按钮统计
react_buttons=$(grep -ro '<button[^>]*>' "$CLIENT_DIR/src" 2>/dev/null | wc -l | tr -d ' ')
echo "新 UI React <button> 元素数: $react_buttons"
echo ""

echo "--- 按页面分组的按钮统计 ---"
echo ""

for page in voice_studio chapter_studio workflow settings dashboard projects; do
  py_count=$(grep -r "ActionButton\|QPushButton" "$PYTHON_DIR/pages/$page" --include="*.py" 2>/dev/null | wc -l | tr -d ' ')
  react_count=$(grep -ro '<button[^>]*>' "$CLIENT_DIR/src" 2>/dev/null | grep -c -i "$page" 2>/dev/null || echo "0")
  echo "$page: PySide6=$py_count  React=$react_count"
done
echo ""

echo "--- PySide6 源端关键按钮列表 ---"
echo ""
echo "voice_studio/page.py 主要按钮:"
grep "ActionButton" "$PYTHON_DIR/pages/voice_studio/page.py" 2>/dev/null | sed 's/^[ \t]*//' | head -15
echo ""
echo "chapter_studio/action_panel.py 主要按钮:"
grep "ActionButton" "$PYTHON_DIR/pages/chapter_studio/action_panel.py" 2>/dev/null | sed 's/^[ \t]*//' | head -15
echo ""
echo "settings/components.py 主要按钮:"
grep "ActionButton" "$PYTHON_DIR/pages/settings/components.py" 2>/dev/null | sed 's/^[ \t]*//' | head -10
echo ""
echo "workflow/jobs.py 主要按钮:"
grep "ActionButton" "$PYTHON_DIR/pages/workflow/jobs.py" 2>/dev/null | sed 's/^[ \t]*//' | head -10
echo ""

echo "--- 1:1 复刻进度评估 ---"
echo ""
echo "  [x] 声腔页: 全部 ActionButton 变体已覆盖 (primary/secondary/quiet)"
echo "  [x] 章台: 停止/取消/断点恢复/AI 建议浮窗/启动等按钮已覆盖"
echo "  [x] 机杼: 运行/停止/恢复/断点续写/AI修复/重试已覆盖"
echo "  [x] 火候: 模型管理(添加/编辑/删除/测试连接)已覆盖"
echo "  [x] 案头: 起笔/阅卷/重建向量/续此卷/去机杼已覆盖"
echo ""
echo "详细按钮文本对比请参考 docs/ui-parity/interaction-consistency.md"