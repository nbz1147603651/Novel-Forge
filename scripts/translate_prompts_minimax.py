#!/usr/bin/env python3
"""Translate ZH→EN prompt templates using MiniMax API.

Preserves Jinja2 syntax, variable names, and field names.
Only translates Chinese prose text to professional English.
"""
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

TASK: Translate a Jinja2 template file from Chinese to English.

CRITICAL RULES — follow these EXACTLY:
1. PRESERVE all Jinja2 syntax EXACTLY: {% %}, {{ }}, {# #}, {%- -%}, macros, imports, loops, conditionals. Do NOT modify any Jinja2 code.
2. PRESERVE all variable names and field names (snake_case: scene_intents, pov_character, chapter_contract, etc.)
3. PRESERVE all markdown formatting (## headers, - bullets, **bold**, etc.)
4. TRANSLATE all Chinese prose text to professional, natural English
5. Replace Chinese punctuation (，。；：！？、「」『』（）【】——……) with English (, . ; : ! ? "" '' () [] — ...)
6. Keep technical terms (POV, JSON, scene_intent, etc.) as-is
7. Quality calibration examples should become equivalent English literary examples
8. Output ONLY the translated file content — no explanations, no code fences

This is a novel-writing AI system prompt template. The English must read naturally as instructions for an English-language LLM, using professional literary terminology."""


def call_api(content: str, max_retries: int = 3) -> str:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Translate this Jinja2 template from Chinese to English. Output ONLY the translated content, nothing else:\n\n{content}"}
    ]
    payload = json.dumps({
        "model": "MiniMax-M2.5",
        "messages": messages,
        "temperature": 0.05,
        "max_tokens": 16000,
        "stream": False
    }).encode()

    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(
                API_URL, data=payload,
                headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=180) as resp:
                data = json.loads(resp.read())

            result = data["choices"][0]["message"]["content"]
            result = result.strip()
            # Strip markdown code fences
            if result.startswith("```"):
                lines = result.split("\n")
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].strip().startswith("```"):
                    lines = lines[:-1]
                result = "\n".join(lines)
            return result
        except Exception as e:
            print(f"    Retry {attempt+1}/{max_retries}: {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** (attempt + 1))
    raise RuntimeError(f"API call failed after {max_retries} retries")


def has_chinese(text: str) -> bool:
    return bool(re.search(r'[\u4e00-\u9fff]', text))


def count_chinese(text: str) -> int:
    return sum(1 for line in text.split("\n") if has_chinese(line))


def translate_file(zh_path: Path, en_path: Path) -> bool:
    content = zh_path.read_text(encoding="utf-8")
    if not has_chinese(content):
        en_path.parent.mkdir(parents=True, exist_ok=True)
        en_path.write_text(content, encoding="utf-8")
        return True

    # Split large files
    if len(content) > 10000:
        return translate_large_file(zh_path, en_path)

    try:
        translated = call_api(content)
        en_path.parent.mkdir(parents=True, exist_ok=True)
        en_path.write_text(translated, encoding="utf-8")
        remaining = count_chinese(translated)
        if remaining > 3:
            print(f"    ⚠ {remaining} Chinese lines remain")
        return remaining <= 5
    except Exception as e:
        print(f"    ✗ Failed: {e}")
        return False


def translate_large_file(zh_path: Path, en_path: Path) -> bool:
    content = zh_path.read_text(encoding="utf-8")
    lines = content.split("\n")

    chunks: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        current.append(line)
        if len(current) >= 250 and line.strip() == "":
            chunks.append(current)
            current = []
    if current:
        chunks.append(current)

    translated_parts = []
    for i, chunk in enumerate(chunks):
        chunk_text = "\n".join(chunk)
        if not has_chinese(chunk_text):
            translated_parts.append(chunk_text)
            continue
        try:
            result = call_api(chunk_text)
            translated_parts.append(result)
            time.sleep(0.5)
        except Exception as e:
            print(f"    ✗ Chunk {i+1}/{len(chunks)}: {e}")
            translated_parts.append(chunk_text)

    result = "\n".join(translated_parts)
    en_path.parent.mkdir(parents=True, exist_ok=True)
    en_path.write_text(result, encoding="utf-8")
    remaining = count_chinese(result)
    if remaining > 5:
        print(f"    ⚠ {remaining} Chinese lines remain")
    return remaining <= 10


def main() -> None:
    # Clear existing EN templates
    if EN.exists():
        import shutil
        shutil.rmtree(EN)
    EN.mkdir(parents=True)

    zh_files = sorted(ZH.rglob("*.j2"))
    print(f"Translating {len(zh_files)} files via MiniMax API...")

    success = 0
    failed = 0

    for i, zh_path in enumerate(zh_files):
        rel = zh_path.relative_to(ZH)
        en_path = EN / rel
        print(f"  [{i+1}/{len(zh_files)}] {rel} ...", end=" ", flush=True)
        ok = translate_file(zh_path, en_path)
        if ok:
            success += 1
            print("✓")
        else:
            failed += 1
            print("✗")
        time.sleep(0.2)

    print(f"\n{'='*60}")
    print(f"Results: {success} OK, {failed} failed")

    total_remaining = 0
    for en_path in sorted(EN.rglob("*.j2")):
        content = en_path.read_text(encoding="utf-8")
        total_remaining += count_chinese(content)
    print(f"Total remaining Chinese lines: {total_remaining}")


if __name__ == "__main__":
    main()
