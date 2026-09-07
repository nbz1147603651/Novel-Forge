"""Quick smoke test for character intro detection logic."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from novel_forge.core.schemas.bible import CharacterBible
from novel_forge.core.schemas.outline import StoryOutline
from novel_forge.pipeline.long.stages.character_intro import _collect_chapter_character_candidates

project = Path("data/遗物人生")

bible = CharacterBible.model_validate(
    json.loads((project / "character_bible.json").read_text())
)
existing = {c.name for c in bible.characters}
print("现有角色:", existing)

outline = StoryOutline.model_validate(
    json.loads((project / "outline.json").read_text())
)

class FakeBundle:
    pass

for ch_num in [3, 4, 5]:
    ch = next((c for c in outline.chapters if c.chapter_number == ch_num), None)
    if ch is None:
        print(f"第{ch_num}章不存在")
        continue
    bundle = FakeBundle()
    bundle.chapter_outline = ch
    bundle.character_bible = bible
    candidates = _collect_chapter_character_candidates(bundle, None, ch_num)
    new_chars = candidates - existing
    print(f"第{ch_num}章 POV={ch.pov_character!r} | 候选={candidates} | 新人物={new_chars}")

print("OK")
