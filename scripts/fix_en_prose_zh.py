#!/usr/bin/env python3
"""Re-translate specific EN template files that still have Chinese prose using MiniMax API."""
from __future__ import annotations

import json
import re
import time
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parents[1] / "novel_forge/prompts/packs"
ZH = BASE / "zh" / "templates"
EN = BASE / "en" / "templates"

def get_api_key() -> str:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("NOVEL_FORGE_MINIMAX_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("MiniMax API key not found")

API_KEY = get_api_key()
API_URL = "https://api.minimax.chat/v1/text/chatcompletion_v2"

SYSTEM_PROMPT = """You are a professional translator for AI prompt engineering templates.

TASK: The following is a Jinja2 template that was partially translated from Chinese to English. Some Chinese words were left in the English text. Your job is to produce a fully English version.

CRITICAL RULES:
1. PRESERVE all Jinja2 syntax EXACTLY: {% %}, {{ }}, {# #}, {%- -%}, macros, imports, loops, conditionals
2. PRESERVE all variable names and field names (snake_case)
3. PRESERVE all markdown formatting
4. Translate ALL remaining Chinese text to professional English
5. Replace Chinese punctuation with English equivalents
6. Output ONLY the complete translated file — no explanations

This is a novel-writing AI system prompt template."""


def has_chinese(text: str) -> bool:
    return bool(re.search(r'[\u4e00-\u9fff]', text))


def count_prose_chinese(text: str) -> int:
    comment_re = re.compile(r'^\s*\{#.*#\}\s*$')
    zh_re = re.compile(r'[\u4e00-\u9fff]')
    return sum(1 for line in text.split("\n") if zh_re.search(line) and not comment_re.match(line))


def call_api(content: str) -> str:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Here is the partially-translated Jinja2 template. Produce the fully English version:\n\n{content}"}
    ]
    payload = json.dumps({
        "model": "MiniMax-M2.5",
        "messages": messages,
        "temperature": 0.05,
        "max_tokens": 16000,
        "stream": False
    }).encode()

    for attempt in range(3):
        try:
            req = urllib.request.Request(
                API_URL, data=payload,
                headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=180) as resp:
                data = json.loads(resp.read())
            result = data["choices"][0]["message"]["content"].strip()
            if result.startswith("```"):
                lines = result.split("\n")
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].strip().startswith("```"):
                    lines = lines[:-1]
                result = "\n".join(lines)
            return result
        except Exception:
            if attempt < 2:
                time.sleep(2 ** (attempt + 1))
            else:
                raise


def main():
    # Files sorted by prose Chinese line count (descending)
    target_files = [
        "checking/causal_validate.j2",
        "writing/draft_chapter.j2",
        "planning/plan_outline.j2",
        "_base/_quality_standards.j2",
        "writing/evaluate_draft.j2",
        "summary/summarize_scene.j2",
        "planning/plan_chapter.j2",
        "checking/extract_init_coherence_claims.j2",
        "planning/validate_scene_plan.j2",
        "planning/short_blueprint.j2",
        "kernel/extract_motifs.j2",
        "initialization/init_story_bible.j2",
        "checking/repair_reading_power.j2",
        "checking/adjudicate_contract_coherence.j2",
        "beats/beats_to_draft.j2",
        "writing/wave_chapter.j2",
        "initialization/init_character_profile_batch.j2",
        "initialization/enrich_character.j2",
        "_base/_render_element_focus.j2",
        "writing/patch_chapter.j2",
        "summary/volume_audit.j2",
        "planning/plan_outline_batch.j2",
        "planning/plan_chapter_scenes.j2",
        "planning/plan_chapter_contracts.j2",
        "initialization/init_character_bible.j2",
        "checking/evaluate_reading_power.j2",
        "checking/_causal_core.j2",
        "beats/spec_to_beats.j2",
        "_base/_render_canon_characters.j2",
        "_base/_artifact_source_contract.j2",
    ]

    for i, rel in enumerate(target_files):
        zh_path = ZH / rel
        en_path = EN / rel
        if not zh_path.exists():
            print(f"  SKIP {rel} (zh source not found)")
            continue

        en_content = en_path.read_text(encoding="utf-8") if en_path.exists() else ""
        before = count_prose_chinese(en_content)
        if before == 0:
            print(f"  [{i+1}/{len(target_files)}] OK {rel} (no prose Chinese)")
            continue

        zh_content = zh_path.read_text(encoding="utf-8")
        print(f"  [{i+1}/{len(target_files)}] {rel} ({before} prose Chinese lines) ...", end=" ", flush=True)

        try:
            result = call_api(zh_content)
            en_path.parent.mkdir(parents=True, exist_ok=True)
            en_path.write_text(result, encoding="utf-8")
            after = count_prose_chinese(result)
            if after <= 3:
                print(f"OK ({after} remaining)")
            else:
                print(f"PARTIAL ({after} remaining)")
        except Exception as e:
            print(f"FAILED: {e}")

        time.sleep(0.5)

    # Summary
    total = 0
    zh_re = re.compile(r'[\u4e00-\u9fff]')
    comment_re = re.compile(r'^\s*\{#.*#\}\s*$')
    for en_path in sorted(EN.rglob("*.j2")):
        content = en_path.read_text(encoding="utf-8")
        total += sum(1 for line in content.split("\n") if zh_re.search(line) and not comment_re.match(line))
    print(f"\nTotal remaining prose Chinese lines: {total}")


if __name__ == "__main__":
    main()
