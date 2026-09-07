#!/usr/bin/env python3
"""Create English Humanize Library SQLite database with AI-ism detection rules.

Uses the same pattern_ids as the Chinese library (HUMANIZE_PATTERN_IDS) but with
English-specific detection patterns and example phrases.
"""
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = str(Path(__file__).resolve().parents[1] / "data/_global/humanize_library/humanize_library_en.db")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

if os.path.exists(DB_PATH):
    os.remove(DB_PATH)

conn = sqlite3.connect(DB_PATH)
c = conn.cursor()

c.execute("""CREATE TABLE entries (
    pattern_id TEXT PRIMARY KEY,
    pattern_name TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT '',
    severity TEXT NOT NULL DEFAULT 'medium',
    example_phrases TEXT NOT NULL DEFAULT '[]',
    example_template TEXT NOT NULL DEFAULT '',
    detection_method TEXT NOT NULL DEFAULT 'regex',
    detection_config TEXT NOT NULL DEFAULT '{}',
    keywords TEXT NOT NULL DEFAULT '[]',
    source TEXT NOT NULL DEFAULT 'user',
    project_id TEXT,
    notes TEXT NOT NULL DEFAULT '',
    hit_count INTEGER NOT NULL DEFAULT 0,
    first_seen_at TEXT,
    last_seen_at TEXT,
    last_hit_chapter INTEGER,
    enabled INTEGER NOT NULL DEFAULT 1,
    embedding_signature TEXT,
    vector_stale INTEGER NOT NULL DEFAULT 0,
    schema_version TEXT NOT NULL DEFAULT '2.0',
    created_at TEXT NOT NULL DEFAULT ''
)""")

c.execute("""CREATE VIRTUAL TABLE entries_fts USING fts5(
    pattern_id, pattern_name, category, keywords, notes,
    content='', tokenize='unicode61'
)""")

c.execute("""CREATE TABLE meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
)""")

now = datetime.utcnow().isoformat()

def cfg(pattern: str, **extra) -> str:
    d = {"pattern": pattern}
    d.update(extra)
    return json.dumps(d)

# pattern_id must match HUMANIZE_PATTERN_IDS exactly
entries = [
    ("significance_inflation", "Significance Inflation", "Narrative Weight", "high",
     json.dumps(["little did they know", "what happened next would change everything", "this moment would define", "a pivotal moment", "a turning point", "profound implications"]),
     "", "regex",
     cfg(r"\b(little\s+did\s+they\s+know|what\s+happened\s+next\s+would\s+change\s+everything|this\s+moment\s+would\s+define|a\s+pivotal\s+moment|a\s+turning\s+point|profound\s+implications)\b"),
     json.dumps(["significance", "inflation", "AI"]), "builtin", None, "Inflating narrative significance with tell-not-show declarations.", 0, now, now, None, 1, None, 0, "2.0", now),

    ("promotional_language", "Promotional Language", "Promotional Description", "medium",
     json.dumps(["stunning", "breathtaking", "awe-inspiring", "magnificent", "spectacular", "extraordinary", "unprecedented"]),
     "", "regex",
     cfg(r"\b(stunning|breathtaking|awe-inspiring|magnificent|spectacular|extraordinary|unprecedented)\b"),
     json.dumps(["promotional", "language", "AI"]), "builtin", None, "Promotional language that reads like marketing copy.", 0, now, now, None, 1, None, 0, "2.0", now),

    ("ai_vocabulary", "AI Vocabulary", "AI Vocabulary", "medium",
     json.dumps(["delve into", "testament to", "tapestry of", "multifaceted", "nuanced", "intricate", "profound", "captivating", "compelling", "a myriad of", "plethora of"]),
     "", "regex",
     cfg(r"\b(delve\s+into|testament\s+to|tapestry\s+of|multifaceted|nuanced|intricate|profound|captivating|compelling|a\s+myriad\s+of|plethora\s+of)\b"),
     json.dumps(["AI", "vocabulary", "cliche"]), "builtin", None, "Common AI-generated vocabulary.", 0, now, now, None, 1, None, 0, "2.0", now),

    ("negative_parallelism", "Negative Parallelism", "Template Patterns", "high",
     json.dumps(["not X, but Y", "not only X, but Y", "wasn't X — it was Y"]),
     "", "regex",
     cfg(r"\b(not\s+\w+,\s+but|not\s+only\s+.+,\s+but)\b"),
     json.dumps(["negative", "parallelism", "AI"]), "builtin", None, "Negative parallelism pattern overused by AI.", 0, now, now, None, 1, None, 0, "2.0", now),

    ("rule_of_three", "Rule of Three Inflation", "Template Patterns", "medium",
     json.dumps(["X, Y, and Z", "not only X, but also Y, and even Z"]),
     "", "regex",
     cfg(r"(\w+),\s+(\w+),\s+and\s+(\w+)"),
     json.dumps(["rule of three", "triplet", "AI pattern"]), "builtin", None, "Overuse of the rule-of-three triplet pattern.", 0, now, now, None, 1, None, 0, "2.0", now),

    ("synonym_cycling", "Synonym Cycling", "Repetition Patterns", "medium",
     json.dumps(["rotating synonyms for the same concept within paragraphs"]),
     "", "regex",
     cfg("synonym_cycling_detection"),
     json.dumps(["synonym", "cycling", "AI"]), "builtin", None, "Rotating synonyms instead of committing to one term.", 0, now, now, None, 0, None, 0, "2.0", now),

    ("false_ranges", "False Ranges", "Template Patterns", "medium",
     json.dumps(["from X to Y and everything in between", "whether... or..."]),
     "", "regex",
     cfg(r"\b(from\s+.+\s+to\s+.+\s+and\s+everything\s+in\s+between)\b"),
     json.dumps(["false range", "AI pattern"]), "builtin", None, "False range expressions.", 0, now, now, None, 1, None, 0, "2.0", now),

    ("filler_phrases", "Filler Phrases", "Filler Phrases", "high",
     json.dumps(["in today's world", "at the end of the day", "needless to say", "it goes without saying", "the fact of the matter is", "the bottom line is"]),
     "", "regex",
     cfg(r"\b(in\s+today'?s\s+world|at\s+the\s+end\s+of\s+the\s+day|needless\s+to\s+say|it\s+goes\s+without\s+saying|the\s+fact\s+of\s+the\s+matter\s+is|the\s+bottom\s+line\s+is)\b"),
     json.dumps(["filler", "cliche", "AI"]), "builtin", None, "Filler phrases that add no narrative content.", 0, now, now, None, 1, None, 0, "2.0", now),

    ("excessive_hedging", "Excessive Hedging", "Meta Language", "medium",
     json.dumps(["perhaps", "maybe", "possibly", "potentially", "somewhat", "rather", "quite", "fairly"]),
     "", "regex",
     cfg(r"\b(perhaps|maybe|possibly|potentially|somewhat|rather|quite|fairly)\b", max_per_paragraph=2),
     json.dumps(["hedging", "excessive", "AI"]), "builtin", None, "Excessive hedging adverbs.", 0, now, now, None, 0, None, 0, "2.0", now),

    ("generic_conclusions", "Generic Conclusions", "Template Endings", "high",
     json.dumps(["In conclusion", "Ultimately", "As we have seen", "In the end", "All things considered"]),
     "", "regex",
     cfg(r"\b(In\s+conclusion|Ultimately|As\s+we\s+have\s+seen|In\s+the\s+end|All\s+things\s+considered)\b"),
     json.dumps(["conclusion", "generic", "AI"]), "builtin", None, "Generic conclusion phrases.", 0, now, now, None, 1, None, 0, "2.0", now),

    ("hollow_aspect_marker", "Hollow Progressive Aspect", "Action Blurring", "medium",
     json.dumps(["was beginning to", "was starting to", "was about to", "was going to", "was in the process of"]),
     "", "regex",
     cfg(r"\b(was\s+beginning\s+to|was\s+starting\s+to|was\s+about\s+to|was\s+going\s+to|was\s+in\s+the\s+process\s+of)\b"),
     json.dumps(["progressive", "hollow", "AI"]), "builtin", None, "Hollow progressive aspect.", 0, now, now, None, 1, None, 0, "2.0", now),

    ("em_dash_overuse", "Em Dash Overuse", "Punctuation Habits", "high",
     json.dumps(["\u2014", "\u2014"]),
     "", "regex",
     cfg(r"\u2014.{0,30}\u2014", max_per_paragraph=1),
     json.dumps(["em dash", "punctuation", "AI"]), "builtin", None, "Excessive em-dash parentheticals.", 0, now, now, None, 1, None, 0, "2.0", now),

    ("passive_subjectless", "Passive/Subjectless", "Syntax Patterns", "high",
     json.dumps(["was done", "were made", "has been said", "it was decided", "it was found"]),
     "", "regex",
     cfg(r"\b(was\s+\w+ed|were\s+\w+ed|has\s+been\s+\w+ed|it\s+was\s+\w+ed|it\s+was\s+decided|it\s+was\s+found)\b"),
     json.dumps(["passive", "voice", "AI"]), "builtin", None, "Overuse of passive voice without agent.", 0, now, now, None, 1, None, 0, "2.0", now),

    ("persuasive_authority", "Persuasive Authority", "Authoritative Tone", "high",
     json.dumps(["Studies show", "Experts agree", "It is well known that", "Research has shown"]),
     "", "regex",
     cfg(r"\b(Studies\s+show|Experts\s+agree|It\s+is\s+well\s+known\s+that|Research\s+has\s+shown)\b"),
     json.dumps(["authority", "persuasive", "AI"]), "builtin", None, "Appeals to unspecified authority.", 0, now, now, None, 1, None, 0, "2.0", now),

    ("vague_attribution", "Vague Attribution", "Vague Evidence", "medium",
     json.dumps(["some say", "it is said", "people believe", "many think", "it is believed"]),
     "", "regex",
     cfg(r"\b(some\s+say|it\s+is\s+said|people\s+believe|many\s+think|it\s+is\s+believed)\b"),
     json.dumps(["vague", "attribution", "AI"]), "builtin", None, "Vague attribution without specific sources.", 0, now, now, None, 1, None, 0, "2.0", now),

    ("collaborative_artifact", "Collaborative Artifact", "Chat Residue", "critical",
     json.dumps(["As an AI", "I'd be happy to help", "I can help with that", "Let me know if"]),
     "", "regex",
     cfg(r"\b(As\s+an\s+AI|I'?d\s+be\s+happy\s+to\s+help|I\s+can\s+help\s+with\s+that|Let\s+me\s+know\s+if)\b"),
     json.dumps(["AI", "chat", "residue"]), "builtin", None, "Chat assistant residue.", 0, now, now, None, 1, None, 0, "2.0", now),

    ("sycophantic_tone", "Sycophantic Tone", "Chat Residue", "high",
     json.dumps(["Great question!", "That's a fascinating topic", "Excellent point"]),
     "", "regex",
     cfg(r"\b(Great\s+question|That'?s\s+a\s+fascinating|Excellent\s+point)\b"),
     json.dumps(["sycophantic", "AI", "chat"]), "builtin", None, "Sycophantic tone.", 0, now, now, None, 1, None, 0, "2.0", now),

    ("markdown_formatting_residue", "Markdown/Format Residue", "Format Residue", "high",
     json.dumps(["**", "##", "###", "```"]),
     "", "regex",
     cfg(r"(\*\*|#{1,6}\s|```)"),
     json.dumps(["markdown", "format", "residue"]), "builtin", None, "Unintended markdown formatting.", 0, now, now, None, 1, None, 0, "2.0", now),

    ("monotone_rhythm", "Monotone Rhythm", "Syntax Patterns", "medium",
     json.dumps(["uniform sentence length patterns"]),
     "", "regex",
     cfg("monotone_rhythm_detection"),
     json.dumps(["rhythm", "monotone", "AI"]), "builtin", None, "Uniform sentence length patterns.", 0, now, now, None, 0, None, 0, "2.0", now),

    ("cross_chapter_template", "Cross-Chapter Template", "Structural Template", "medium",
     json.dumps(["Meanwhile, back at", "Back at the", "At the same time", "Elsewhere"]),
     "", "regex",
     cfg(r"\b(Meanwhile,\s+back\s+at|Back\s+at\s+the|At\s+the\s+same\s+time|Elsewhere)\b"),
     json.dumps(["cross-chapter", "template", "AI"]), "builtin", None, "Formulaic cross-chapter transitions.", 0, now, now, None, 1, None, 0, "2.0", now),

    ("weak_verb_stacking", "Weak Verb Stacking", "Narrative Weight", "medium",
     json.dumps(["seemed to", "appeared to", "felt like", "looked like", "sounded like"]),
     "", "regex",
     cfg(r"\b(seemed\s+to|appeared\s+to|felt\s+like|looked\s+like|sounded\s+like)\b"),
     json.dumps(["weak", "verb", "stacking"]), "builtin", None, "Stacking weak perception verbs.", 0, now, now, None, 1, None, 0, "2.0", now),

    ("tautology_marker", "Abstract Triple Tautology", "Template Patterns", "medium",
     json.dumps(["X, Y, and Z all", "the X, the Y, and the Z"]),
     "", "regex",
     cfg(r"(\w+),\s+(\w+),\s+and\s+(\w+)\s+all"),
     json.dumps(["tautology", "triple", "AI"]), "builtin", None, "Abstract triple tautology pattern.", 0, now, now, None, 1, None, 0, "2.0", now),

    ("binary_judgment_closing", "Binary Judgment Closing", "Template Endings", "medium",
     json.dumps(["It was both X and Y", "not just X but also Y", "more than just X"]),
     "", "regex",
     cfg(r"\b(It\s+was\s+both|not\s+just\s+.+\s+but\s+also|more\s+than\s+just)\b"),
     json.dumps(["binary", "judgment", "AI"]), "builtin", None, "Binary judgment closing patterns.", 0, now, now, None, 1, None, 0, "2.0", now),

    ("pronoun_disappearance_run", "Subject Weakening", "Narrative Weight", "medium",
     json.dumps(["there was", "there were", "it was", "it seemed", "there came"]),
     "", "regex",
     cfg(r"\b(there\s+was|there\s+were|it\s+was\s+(?:a|the|some)|it\s+seemed|there\s+came)\b"),
     json.dumps(["pronoun", "disappearance", "AI"]), "builtin", None, "Subject-weakening constructions.", 0, now, now, None, 1, None, 0, "2.0", now),

    ("precise_timestamp_overuse", "Precise Timestamp Overuse", "AI Habits", "medium",
     json.dumps(["three seconds later", "five minutes passed", "a moment later"]),
     "", "regex",
     cfg(r"\b(\d+\s+(?:seconds?|minutes?|hours?)\s+(?:later|passed)|a\s+moment\s+later)\b"),
     json.dumps(["timestamp", "precise", "AI"]), "builtin", None, "Overuse of precise timestamps.", 0, now, now, None, 1, None, 0, "2.0", now),
]

for entry in entries:
    c.execute("""INSERT INTO entries (pattern_id, pattern_name, category, severity,
        example_phrases, example_template, detection_method, detection_config,
        keywords, source, project_id, notes, hit_count, first_seen_at, last_seen_at,
        last_hit_chapter, enabled, embedding_signature, vector_stale, schema_version, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", entry)

c.execute("INSERT INTO meta (key, value) VALUES ('library_locale', 'en')")
c.execute("INSERT INTO meta (key, value) VALUES ('schema_version', '2.0')")
c.execute("INSERT INTO meta (key, value) VALUES ('created_at', ?)", (now,))

conn.commit()
conn.close()

print(f"Created English Humanize Library DB with {len(entries)} entries at {DB_PATH}")
