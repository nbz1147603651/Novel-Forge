"""Pipeline stage implementations for long-form chapter execution.

Each sub-module handles one phase of the chapter pipeline:

- planning  : bridge generation and chapter planning
- draft     : Generate context building and raw DRAFT prose
- wave      : WAVE scene weaving and reviewable handoff
- quality   : alignment/continuity checks, repairs, polish, and humanize
- finalize  : canon extraction, evaluation, and persistence
"""
