#!/usr/bin/env python3
"""
代词一致性批量修复脚本（通用版本）
从 character_bible.json 动态加载角色信息，修复章节中的代词混用问题

使用方法:
    python scripts/fix_pronoun_consistency.py [项目路径] [章节号...]
    
示例:
    python scripts/fix_pronoun_consistency.py data/遗物人生 11 12 13 14
    python scripts/fix_pronoun_consistency.py data/新项目 1 2 3
"""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple


class PronounFixer:
    """通用代词修复器"""
    
    def __init__(self, character_pronouns: Optional[Dict[str, Dict]] = None):
        """
        初始化修复器
        
        Args:
            character_pronouns: 角色代词映射，如 {"林微": {"gender": "女", "correct": "她", "wrong": "他"}}
        """
        self.character_pronouns = character_pronouns or {}
        self.fixes_made = []
        
    @classmethod
    def from_character_bible(cls, bible_path: Path) -> "PronounFixer":
        """
        从 character_bible.json 文件创建修复器
        
        支持多种格式:
        1. {"characters": [{"name": "...", "gender": "..."}, ...]}
        2. {"name": {"gender": "...", ...}, ...}
        """
        if not bible_path.exists():
            print(f"⚠️  character_bible 不存在: {bible_path}")
            return cls({})
        
        try:
            with open(bible_path, 'r', encoding='utf-8') as f:
                bible_data = json.load(f)
        except json.JSONDecodeError as e:
            print(f"⚠️  无法解析 character_bible: {e}")
            return cls({})
        
        # 代词映射表
        pronoun_map = {
            "女": {"correct": "她", "wrong": "他"},
            "男": {"correct": "他", "wrong": "她"},
        }
        
        character_pronouns = {}
        
        # 格式1: {"characters": [...]}
        if "characters" in bible_data and isinstance(bible_data["characters"], list):
            for char in bible_data["characters"]:
                if isinstance(char, dict):
                    name = char.get("name", "")
                    gender = char.get("gender", "")
                    if name and gender in pronoun_map:
                        character_pronouns[name] = {
                            "gender": gender,
                            **pronoun_map[gender]
                        }
        
        # 格式2: {"name": {"gender": "...", ...}}
        for key, value in bible_data.items():
            if isinstance(value, dict) and "gender" in value:
                gender = value.get("gender", "")
                if gender in pronoun_map:
                    character_pronouns[key] = {
                        "gender": gender,
                        **pronoun_map[gender]
                    }
        
        print(f"📚 从 {bible_path.name} 加载了 {len(character_pronouns)} 个角色")
        for name, info in character_pronouns.items():
            print(f"   - {name}: {info['gender']}性，使用'{info['correct']}'")
        
        return cls(character_pronouns)
        
    def is_in_dialogue(self, text: str, pos: int) -> bool:
        """检查指定位置是否在对话引号内"""
        text_before = text[:pos]
        quotes_before = (
            text_before.count('"') + 
            text_before.count('"') + 
            text_before.count('"') +
            text_before.count("'") +
            text_before.count("'")
        )
        return quotes_before % 2 == 1
    
    def find_pronoun_issues(self, text: str, char_name: str, char_info: Dict) -> List[Dict]:
        """
        查找代词问题
        策略：在角色名出现后的一段距离内，检查是否使用了错误代词
        """
        issues = []
        correct = char_info["correct"]
        wrong = char_info["wrong"]
        
        # 模式: 角色名 + 任意内容(0-150字符) + 错误代词 + 标点
        pattern = rf"({char_name}[\s\S]{{0,150}}?){wrong}(?=[\s，。；：、！？])"
        
        for match in re.finditer(pattern, text):
            start, end = match.span()
            
            # 检查是否在对话中（如果是，可能是引用他人话语，跳过）
            if self.is_in_dialogue(text, start):
                continue
            
            # 获取上下文
            context_start = max(0, start - 30)
            context_end = min(len(text), end + 30)
            context = text[context_start:context_end]
            
            issues.append({
                "character": char_name,
                "expected": correct,
                "found": wrong,
                "position": (start, end),
                "match_text": match.group(0),
                "context": context,
            })
        
        return issues
    
    def fix_text(self, text: str, char_name: str, char_info: Dict) -> Tuple[str, List[Dict]]:
        """
        修复文本中的代词问题
        返回: (修复后的文本, 修复记录)
        """
        issues = self.find_pronoun_issues(text, char_name, char_info)
        if not issues:
            return text, []
        
        # 从后往前替换，避免位置偏移
        fixed_text = text
        fixes = []
        
        for issue in sorted(issues, key=lambda x: x["position"][0], reverse=True):
            start, end = issue["position"]
            
            # 构造修复后的文本
            original = fixed_text[start:end]
            fixed = original.replace(issue["found"], issue["expected"], 1)
            
            fixed_text = fixed_text[:start] + fixed + fixed_text[end:]
            
            fixes.append({
                "character": issue["character"],
                "original": original,
                "fixed": fixed,
                "context": issue["context"],
            })
        
        return fixed_text, fixes
    
    def fix_chapter(self, chapter_path: Path, target_chars: Optional[List[str]] = None, 
                    dry_run: bool = False) -> Dict:
        """
        修复单个章节
        
        Args:
            chapter_path: 章节文件路径
            target_chars: 要修复的角色列表，None表示修复所有角色
            dry_run: 如果为True，只检测不修改
        """
        if target_chars is None:
            target_chars = list(self.character_pronouns.keys())
        
        # 过滤出实际存在的角色
        target_chars = [c for c in target_chars if c in self.character_pronouns]
        
        if not target_chars:
            return {
                "chapter": chapter_path.name,
                "fixes_count": 0,
                "fixes": [],
                "message": "没有可修复的角色"
            }
        
        # 读取原文
        with open(chapter_path, 'r', encoding='utf-8') as f:
            original_text = f.read()
        
        fixed_text = original_text
        all_fixes = []
        
        # 对每个角色进行修复
        for char_name in target_chars:
            char_info = self.character_pronouns[char_name]
            fixed_text, fixes = self.fix_text(fixed_text, char_name, char_info)
            all_fixes.extend(fixes)
        
        if not dry_run and all_fixes:
            # 保存备份
            backup_path = chapter_path.with_suffix('.md.backup')
            with open(backup_path, 'w', encoding='utf-8') as f:
                f.write(original_text)
            
            # 写入修复后的内容
            with open(chapter_path, 'w', encoding='utf-8') as f:
                f.write(fixed_text)
        
        return {
            "chapter": chapter_path.name,
            "fixes_count": len(all_fixes),
            "fixes": all_fixes,
            "backup_path": str(chapter_path.with_suffix('.md.backup')) if all_fixes else None,
        }


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description='代词一致性批量修复工具（通用版本）',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s data/遗物人生 11 12 13 14
  %(prog)s data/新项目 --all
  %(prog)s data/遗物人生 11 --dry-run
        """
    )
    parser.add_argument('project_path', help='项目路径（包含character_bible.json的目录）')
    parser.add_argument('chapters', nargs='*', type=int, help='要修复的章节号（如 11 12 13）')
    parser.add_argument('--all', action='store_true', help='修复所有章节')
    parser.add_argument('--dry-run', action='store_true', help='只检测不修改')
    parser.add_argument('--chars', nargs='+', help='指定要修复的角色（默认全部）')
    
    args = parser.parse_args()
    
    project_path = Path(args.project_path)
    chapters_dir = project_path / "chapters"
    bible_path = project_path / "character_bible.json"
    
    if not project_path.exists():
        print(f"❌ 项目路径不存在: {project_path}")
        sys.exit(1)
    
    if not chapters_dir.exists():
        print(f"❌ 章节目录不存在: {chapters_dir}")
        sys.exit(1)
    
    print("=" * 60)
    print("代词一致性批量修复工具（通用版本）")
    print("=" * 60)
    print(f"📁 项目路径: {project_path}")
    print(f"📂 章节目录: {chapters_dir}")
    
    # 从character_bible加载角色信息
    fixer = PronounFixer.from_character_bible(bible_path)
    
    if not fixer.character_pronouns:
        print("❌ 没有加载到任何角色信息，退出")
        sys.exit(1)
    
    # 确定要修复的章节
    if args.all:
        # 自动发现所有章节
        chapter_files = sorted(chapters_dir.glob("chapter_*.md"))
        chapters_to_fix = [int(f.stem.split('_')[1]) for f in chapter_files]
    elif args.chapters:
        chapters_to_fix = args.chapters
    else:
        print("❌ 请指定章节号或使用 --all 修复所有章节")
        parser.print_help()
        sys.exit(1)
    
    print(f"\n📝 将要修复 {len(chapters_to_fix)} 个章节: {chapters_to_fix}")
    if args.dry_run:
        print("🔍 干运行模式：只检测不修改")
    print("=" * 60)
    
    # 执行修复
    results = []
    
    for chapter_num in chapters_to_fix:
        chapter_file = chapters_dir / f"chapter_{chapter_num:03d}.md"
        
        if not chapter_file.exists():
            print(f"\n⚠️  章节文件不存在: {chapter_file}")
            continue
        
        print(f"\n📄 处理: {chapter_file.name}")
        
        result = fixer.fix_chapter(chapter_file, args.chars, dry_run=args.dry_run)
        results.append(result)
        
        if result["fixes_count"] > 0:
            print(f"   ✅ 发现 {result['fixes_count']} 处代词问题")
            for fix in result["fixes"][:3]:  # 只显示前3个
                print(f"      - {fix['character']}: '{fix['original'][:40]}...' → '{fix['fixed'][:40]}...'")
            if len(result["fixes"]) > 3:
                print(f"      ... 还有 {len(result['fixes']) - 3} 处")
            if not args.dry_run:
                print(f"   💾 备份: {result['backup_path']}")
        else:
            print("   ✓ 未发现代词问题")
    
    # 输出统计
    print("\n" + "=" * 60)
    print("修复统计")
    print("=" * 60)
    
    total_fixes = sum(r["fixes_count"] for r in results)
    chapters_with_fixes = len([r for r in results if r["fixes_count"] > 0])
    
    print(f"总计修复: {total_fixes} 处代词问题")
    print(f"涉及章节: {chapters_with_fixes} 个")
    
    # 保存详细报告
    report_path = project_path / "pronoun_fix_report.json"
    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    
    print(f"\n📊 详细报告: {report_path}")
    print("=" * 60)


if __name__ == "__main__":
    main()
