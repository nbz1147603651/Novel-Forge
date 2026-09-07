#!/usr/bin/env python3
"""Translate ZH→EN prompt templates using MiniMax API with concurrency.

Uses asyncio + aiohttp for parallel API calls to speed up translation.
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

try:
    import aiohttp
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False

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

CONCURRENCY = 3  # Number of parallel API calls

def has_chinese(text: str) -> bool:
    return bool(re.search(r'[\u4e00-\u9fff]', text))

def count_chinese(text: str) -> int:
    return sum(1 for line in text.split("\n") if has_chinese(line))

def strip_code_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        return "\n".join(lines)
    return text

async def translate_one(session: aiohttp.ClientSession, zh_path: Path, en_path: Path, semaphore: asyncio.Semaphore) -> tuple[str, bool]:
    rel = str(zh_path.relative_to(ZH))
    content = zh_path.read_text(encoding="utf-8")

    if not has_chinese(content):
        en_path.parent.mkdir(parents=True, exist_ok=True)
        en_path.write_text(content, encoding="utf-8")
        return rel, True

    # Split large files
    if len(content) > 10000:
        return await translate_large(session, zh_path, en_path, semaphore, content, rel)

    async with semaphore:
        for attempt in range(3):
            try:
                payload = json.dumps({
                    "model": "MiniMax-M2.5",
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": f"Translate this Jinja2 template from Chinese to English. Output ONLY the translated content:\n\n{content}"}
                    ],
                    "temperature": 0.05,
                    "max_tokens": 16000,
                    "stream": False
                })
                async with session.post(
                    API_URL,
                    headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
                    data=payload,
                    timeout=aiohttp.ClientTimeout(total=180)
                ) as resp:
                    data = await resp.json()

                result = strip_code_fences(data["choices"][0]["message"]["content"])
                en_path.parent.mkdir(parents=True, exist_ok=True)
                en_path.write_text(result, encoding="utf-8")
                remaining = count_chinese(result)
                return rel, remaining <= 5
            except Exception:
                if attempt < 2:
                    await asyncio.sleep(2 ** (attempt + 1))
                else:
                    return rel, False

async def translate_large(session: aiohttp.ClientSession, zh_path: Path, en_path: Path, semaphore: asyncio.Semaphore, content: str, rel: str) -> tuple[str, bool]:
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

    async def translate_chunk(chunk_lines: list[str]) -> str:
        chunk_text = "\n".join(chunk_lines)
        if not has_chinese(chunk_text):
            return chunk_text
        async with semaphore:
            for attempt in range(3):
                try:
                    payload = json.dumps({
                        "model": "MiniMax-M2.5",
                        "messages": [
                            {"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": f"Translate this Jinja2 template from Chinese to English. Output ONLY the translated content:\n\n{chunk_text}"}
                        ],
                        "temperature": 0.05,
                        "max_tokens": 16000,
                        "stream": False
                    })
                    async with session.post(
                        API_URL,
                        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
                        data=payload,
                        timeout=aiohttp.ClientTimeout(total=180)
                    ) as resp:
                        data = await resp.json()
                    return strip_code_fences(data["choices"][0]["message"]["content"])
                except Exception:
                    if attempt < 2:
                        await asyncio.sleep(2 ** (attempt + 1))
                    else:
                        return chunk_text

    tasks = [translate_chunk(chunk) for chunk in chunks]
    results = await asyncio.gather(*tasks)
    result = "\n".join(results)
    en_path.parent.mkdir(parents=True, exist_ok=True)
    en_path.write_text(result, encoding="utf-8")
    remaining = count_chinese(result)
    return rel, remaining <= 10


async def main() -> None:
    zh_files = sorted(ZH.rglob("*.j2"))
    print(f"Translating {len(zh_files)} files with concurrency={CONCURRENCY}...")

    semaphore = asyncio.Semaphore(CONCURRENCY)
    connector = aiohttp.TCPConnector(limit=CONCURRENCY)

    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [translate_one(session, zh_path, EN / zh_path.relative_to(ZH), semaphore) for zh_path in zh_files]
        results = await asyncio.gather(*tasks)

    success = sum(1 for _, ok in results if ok)
    failed = sum(1 for _, ok in results if not ok)
    print(f"\nResults: {success} OK, {failed} failed")

    total_remaining = 0
    for en_path in sorted(EN.rglob("*.j2")):
        content = en_path.read_text(encoding="utf-8")
        total_remaining += count_chinese(content)
    print(f"Total remaining Chinese lines: {total_remaining}")


if __name__ == "__main__":
    if not HAS_AIOHTTP:
        print("ERROR: aiohttp not installed. Run: pip install aiohttp")
        sys.exit(1)
    asyncio.run(main())
