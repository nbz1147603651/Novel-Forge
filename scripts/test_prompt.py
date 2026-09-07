#!/usr/bin/env python3
"""Lightweight prompt testing tool.

Usage:
    python scripts/test_prompt.py plan_outline --premise "法医发现每具尸体都留着一封写给未来的信" --genre 悬疑
    python scripts/test_prompt.py profile_style --model tongyi:qwen-turbo --premise "离婚律师发现日记" --genre 言情
    python scripts/test_prompt.py blueprint_element_select --verbose --genre 悬疑
    python scripts/test_prompt.py --list
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from jinja2 import Environment, FileSystemLoader  # noqa: E402

from novel_forge.common.constants import TaskType  # noqa: E402
from novel_forge.core.config import Settings  # noqa: E402
from novel_forge.gateway.factory import ModelRouterBuilder  # noqa: E402
from novel_forge.gateway.router import ModelRouter  # noqa: E402
from novel_forge.gateway.types import ModelRequest, ModelResponse  # noqa: E402
from novel_forge.obs.logger import get_logger  # noqa: E402

_log = get_logger("test_prompt")

TEMPLATE_DIR = project_root / "novel_forge" / "prompts" / "prompts"

DEFAULT_CONTEXT = {
    "spec": {
        "premise": "法医发现每具尸体都留着一封写给未来的信",
        "genre": "悬疑",
        "tone": "冷峻",
        "theme": "探索死者信件背后的秘密",
        "language": "zh-CN",
        "total_chapters": 20,
        "words_per_chapter": 3000,
        "conflict_hint": "死者遗言背后的秘密",
        "characters_hint": "法医主角，神秘死者",
        "world_hint": "现代都市",
    },
    "genre_presets": [],
    "style_profile": {"overrides": {}},
    "outline_tracker_context": None,
    "story_bible": {"world_setting": "现代都市", "core_themes": ["探索", "真相"]},
    "character_bible": {"characters": [{"name": "主角", "role": "法医"}]},
    "blueprint_elements": {"narrative_elements": []},
    "selector_preferences": {"preset_id": None, "manual_override": False, "items": []},
    "required_elements": [],
    "extension_elements": [],
    "mode": "长篇",
    "library_version": "v1.0",
    "batch_start": 1,
    "batch_end": 5,
    "blueprint": {
        "synopsis": "测试 synopsis",
        "narrative_phases": [],
        "key_turning_points": [],
        "subplot_plan": [],
        "character_arcs": [],
    },
    "existing_outline": {"chapters": []},
    "existing_characters": [],
}


def list_templates():
    templates = []
    for j2_file in TEMPLATE_DIR.rglob("*.j2"):
        rel_path = j2_file.relative_to(TEMPLATE_DIR)
        templates.append(str(rel_path))
    templates.sort()
    print("Available templates:")
    for t in templates:
        print(f"  {t}")


def load_template(template_name: str):
    env = Environment(loader=FileSystemLoader(TEMPLATE_DIR))

    try:
        from novel_forge.pipeline.long.services.blueprint.outline_helpers import (
            extract_scoped_weave_links,
        )

        env.globals["extract_scoped_weave_links"] = extract_scoped_weave_links
    except ImportError:
        pass

    template_path = None
    for j2_file in TEMPLATE_DIR.rglob("*.j2"):
        if j2_file.name == template_name or j2_file.stem == template_name:
            template_path = j2_file.relative_to(TEMPLATE_DIR)
            break

    if template_path is None:
        template_path = Path(template_name)
        if not template_path.exists():
            print(f"Error: Template '{template_name}' not found")
            print("Use --list to see available templates")
            sys.exit(1)

    try:
        template = env.get_template(str(template_path))
        return template, str(template_path)
    except Exception as e:
        print(f"Error loading template: {e}")
        sys.exit(1)


def build_context(args):
    context = json.loads(json.dumps(DEFAULT_CONTEXT))

    if args.premise:
        context["spec"]["premise"] = args.premise
    if args.genre:
        context["spec"]["genre"] = args.genre
    if args.tone:
        context["spec"]["tone"] = args.tone
    if args.theme:
        context["spec"]["theme"] = args.theme

    return context


async def call_llm(prompt: str, model_profile: str = None):
    try:
        settings = Settings()
        builder = ModelRouterBuilder(settings)
        router: ModelRouter = builder.build(mock=False)

        if model_profile:
            provider, model_id = model_profile.split(":", 1) if ":" in model_profile else (model_profile, None)
        else:
            profiles_path = project_root / "model_profiles.json"
            if profiles_path.exists():
                with open(profiles_path) as f:
                    profiles = json.load(f)
                default_profile = profiles.get("default_profile_id", "minimax:MiniMax-M2.7-highspeed")
                provider, model_id = default_profile.split(":", 1)
            else:
                provider, model_id = "minimax", "MiniMax-M2.7-highspeed"

        if provider not in router.adapters:
            print(f"❌ Provider '{provider}' not available")
            print(f"   Available providers: {list(router.adapters.keys())}")
            return None

        request = ModelRequest(
            task_type=TaskType.SPEC_ENRICH,
            messages=[{"role": "user", "content": prompt}],
            model_id=model_id,
            temperature=0.7,
            max_tokens=4096,
        )

        _log.info(f"Calling LLM: provider={provider}, model={model_id}")
        print(f"📡 Calling LLM: {provider}/{model_id}")
        print(f"📝 Prompt length: {len(prompt)} chars")

        response: ModelResponse = await router.route(request, provider=provider)

        print("\n✅ Response received:")
        print(f"   Tokens: {response.total_tokens}")
        print(f"   Cost: ${response.cost_usd:.4f}")
        print(f"   Latency: {response.latency_ms}ms")
        print("\n--- Response Content ---")
        print(response.content)
        print("--- End Response ---")

        return response

    except Exception as e:
        print(f"❌ LLM call failed: {e}")
        _log.error(f"LLM call failed: {e}", exc_info=True)
        return None


async def main():
    parser = argparse.ArgumentParser(description="Lightweight prompt testing tool")
    parser.add_argument("template", nargs="?", help="Template name or path")
    parser.add_argument("--list", action="store_true", help="List available templates")
    parser.add_argument("--premise", help="Story premise")
    parser.add_argument("--genre", help="Story genre")
    parser.add_argument("--tone", help="Story tone")
    parser.add_argument("--theme", help="Story theme")
    parser.add_argument("--model", help="Model profile (e.g., minimax:MiniMax-M2.7-highspeed)")
    parser.add_argument("--verbose", action="store_true", help="Show full prompt")
    parser.add_argument("--no-llm", action="store_true", help="Only render prompt, don't call LLM")
    parser.add_argument("--output", help="Save prompt to file")

    args = parser.parse_args()

    if args.list:
        list_templates()
        return

    if not args.template:
        parser.print_help()
        return

    template, template_path = load_template(args.template)
    print(f"📄 Template: {template_path}")

    context = build_context(args)

    try:
        prompt = template.render(**context)
    except Exception as e:
        print(f"❌ Template rendering failed: {e}")
        sys.exit(1)

    print(f"📝 Prompt rendered: {len(prompt)} chars")

    if args.verbose:
        print("\n--- Full Prompt ---")
        print(prompt)
        print("--- End Prompt ---\n")

    if args.output:
        output_path = Path(args.output)
        output_path.write_text(prompt, encoding="utf-8")
        print(f"💾 Prompt saved to: {output_path}")

    if not args.no_llm:
        await call_llm(prompt, args.model)


if __name__ == "__main__":
    asyncio.run(main())
