from __future__ import annotations

from pathlib import Path


def test_prompt_readmes_list_all_public_templates() -> None:
    """Ensure prompt README indexes stay in sync with public templates."""
    base = Path("novel_forge/prompts/prompts")
    problems: list[str] = []

    for folder in sorted(p for p in base.rglob("*") if p.is_dir()):
        readme_path = folder / "README.md"
        if not readme_path.exists():
            continue
        public_templates = sorted(
            p.name
            for p in folder.glob("*.j2")
            if not p.name.startswith("_")
        )
        if not public_templates:
            continue
        readme = readme_path.read_text(encoding="utf-8")
        missing = [name for name in public_templates if f"`{name}`" not in readme]
        if missing:
            problems.append(f"{readme_path}: missing {missing}")

    assert not problems, " ; ".join(problems)
