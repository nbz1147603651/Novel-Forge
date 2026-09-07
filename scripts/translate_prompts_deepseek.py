#!/usr/bin/env python3
"""Translate ZH→EN prompt templates using DeepSeek API.

Preserves Jinja2 syntax, variable names, and field names.
Only translates Chinese prose text to professional English.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

try:
    import httpx
except ImportError:
    import urllib.request
    httpx = None

BASE = Path(__file__).resolve().parents[1] / "novel_forge/prompts/packs"
ZH = BASE / "zh" / "templates"
EN = BASE / "en" / "templates"

# Read API key from .env
def get_api_key() -> str:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("NOVEL_FORGE_DEEPSEEK_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("DeepSeek API key not found")

API_KEY = get_api_key()
API_URL = "https://api.deepseek.com/chat/completions"

SYSTEM_PROMPT = """You are a professional translator specializing in software prompt engineering templates.

Your task: Translate a Jinja2 template file from Chinese to English.

CRITICAL RULES:
1. PRESERVE all Jinja2 syntax EXACTLY: {% %}, {{ }}, {# #}, {%- -%}, macros, imports, loops, conditionals
2. PRESERVE all variable names, field names, and identifiers (snake_case like scene_intents, pov_character, etc.)
3. PRESERVE all markdown formatting (## headers, - bullets, **bold**, etc.)
4. TRANSLATE all Chinese prose text to professional, natural English
5. For Chinese punctuation (，。；：！？、「」『』（）【】——……), replace with English equivalents (, . ; : ! ? "" '' () [] — ...)
6. Keep code-like constructs intact (JSON examples, field:value pairs)
7. Technical terms like POV, JSON, scene_intent, etc. stay as-is
8. Quality examples should be translated to equivalent English literary examples
9. Output ONLY the translated file content, nothing else

This is a novel-writing AI system's prompt template. The English should read naturally as instructions for an English-language LLM."""

def call_api(content: str, max_retries: int = 3) -> str:
    """Call DeepSeek API to translate content."""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Translate the following Jinja2 template from Chinese to English. Output only the translated content:\n\n{content}"}
    ]
    
    payload = json.dumps({
        "model": "deepseek-chat",
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": 16000,
        "stream": False
    })
    
    for attempt in range(max_retries):
        try:
            if httpx:
                resp = httpx.post(
                    API_URL,
                    headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
                    content=payload,
                    timeout=120
                )
                data = resp.json()
            else:
                req = urllib.request.Request(
                    API_URL,
                    data=payload.encode("utf-8"),
                    headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
                    method="POST"
                )
                with urllib.request.urlopen(req, timeout=120) as resp:
                    data = json.loads(resp.read())
            
            result = data["choices"][0]["message"]["content"]
            # Strip markdown code fences if present
            result = result.strip()
            if result.startswith("```"):
                # Remove first and last line if they are fences
                lines = result.split("\n")
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].startswith("```"):
                    lines = lines[:-1]
                result = "\n".join(lines)
            return result
        except Exception as e:
            print(f"    Retry {attempt+1}/{max_retries}: {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"API call failed after {max_retries} retries")


def has_chinese(text: str) -> bool:
    return bool(re.search(r'[\u4e00-\u9fff]', text))


def count_chinese(text: str) -> int:
    return sum(1 for line in text.split("\n") if has_chinese(line))


def translate_file(zh_path: Path, en_path: Path) -> bool:
    """Translate a single file via API."""
    content = zh_path.read_text(encoding="utf-8")
    
    # Skip if no Chinese
    if not has_chinese(content):
        en_path.parent.mkdir(parents=True, exist_ok=True)
        en_path.write_text(content, encoding="utf-8")
        return True
    
    # Skip very large files by splitting if needed
    if len(content) > 12000:
        # For very large files, split into chunks and translate each
        return translate_large_file(zh_path, en_path)
    
    try:
        translated = call_api(content)
        en_path.parent.mkdir(parents=True, exist_ok=True)
        en_path.write_text(translated, encoding="utf-8")
        
        remaining = count_chinese(translated)
        if remaining > 5:
            print(f"    ⚠ {remaining} Chinese lines remain after translation")
            return False
        return True
    except Exception as e:
        print(f"    ✗ Translation failed: {e}")
        return False


def translate_large_file(zh_path: Path, en_path: Path) -> bool:
    """Translate large files by splitting into sections."""
    content = zh_path.read_text(encoding="utf-8")
    lines = content.split("\n")
    
    # Split into chunks of ~300 lines at blank line boundaries
    chunks: list[list[str]] = []
    current_chunk: list[str] = []
    for line in lines:
        current_chunk.append(line)
        if len(current_chunk) >= 200 and line.strip() == "":
            chunks.append(current_chunk)
            current_chunk = []
    if current_chunk:
        chunks.append(current_chunk)
    
    translated_parts = []
    for i, chunk in enumerate(chunks):
        chunk_text = "\n".join(chunk)
        if not has_chinese(chunk_text):
            translated_parts.append(chunk_text)
            continue
        try:
            translated = call_api(chunk_text)
            translated_parts.append(translated)
            time.sleep(0.5)  # Rate limiting
        except Exception as e:
            print(f"    ✗ Chunk {i+1}/{len(chunks)} failed: {e}")
            translated_parts.append(chunk_text)
    
    result = "\n".join(translated_parts)
    en_path.parent.mkdir(parents=True, exist_ok=True)
    en_path.write_text(result, encoding="utf-8")
    
    remaining = count_chinese(result)
    if remaining > 10:
        print(f"    ⚠ {remaining} Chinese lines remain in large file")
        return False
    return True


def main() -> None:
    zh_files = sorted(ZH.rglob("*.j2"))
    print(f"Found {len(zh_files)} ZH template files")
    
    success = 0
    failed = 0
    skipped = 0
    
    for zh_path in zh_files:
        rel = zh_path.relative_to(ZH)
        en_path = EN / rel
        
        # Check if already exists and has minimal Chinese
        if en_path.exists():
            existing = en_path.read_text(encoding="utf-8")
            remaining = count_chinese(existing)
            if remaining <= 3:
                skipped += 1
                print(f"  ⊘ {rel} (already done)")
                continue
        
        print(f"  → {rel} ...", end=" ", flush=True)
        ok = translate_file(zh_path, en_path)
        if ok:
            success += 1
            print("✓")
        else:
            failed += 1
            print("✗")
        
        # Rate limiting between files
        time.sleep(0.3)
    
    print(f"\n{'='*60}")
    print(f"Results: {success} translated, {skipped} skipped, {failed} failed")
    
    # Final count
    total_remaining = 0
    for en_path in sorted(EN.rglob("*.j2")):
        content = en_path.read_text(encoding="utf-8")
        total_remaining += count_chinese(content)
    print(f"Total remaining Chinese lines: {total_remaining}")


if __name__ == "__main__":
    main()
