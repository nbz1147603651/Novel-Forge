"""Diagnostic: check which must_carry_forward items are matched in a chapter."""
import json
import re
import sys
from pathlib import Path

BASE = Path(__file__).parent.parent
chapter_num = int(sys.argv[1]) if len(sys.argv) > 1 else 2

chapter_path = BASE / f"data/浮京一梦/chapters/chapter_{chapter_num:03d}.md"
state_path = BASE / f"data/浮京一梦/states/chapter_{chapter_num:03d}_state_packet.json"

if not chapter_path.exists():
    print(f"No chapter file: {chapter_path}")
    sys.exit(1)
if not state_path.exists():
    print(f"No state file: {state_path}")
    sys.exit(1)

chapter_text = chapter_path.read_text(encoding="utf-8")
state = json.loads(state_path.read_bytes())
items = [i for i in state.get("must_carry_forward", []) if i]

print(f"章节长度: {len(chapter_text)}  |  must_carry_forward 条数: {len(items)}\n")


def item_present(item, text):
    if item in text:
        return True, "verbatim"
    parts = re.split(r"[，。、；：（）【】——→↔\s]", item)
    for p in parts:
        if len(p) >= 4 and p in text:
            return True, "part:" + p[:12]
    if len(item) >= 8:
        hits = []
        for i in range(len(item) - 3):
            w = item[i : i + 4]
            if w in text:
                hits.append(w)
                if len(hits) >= 2:
                    return True, "sliding:" + str(hits[:2])
    return False, "none"


for it in items:
    present, method = item_present(it, chapter_text)
    mark = "OK  " if present else "MISS"
    print(f"[{mark}] ({method:<30}) {it}")
