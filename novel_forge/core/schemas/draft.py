"""Draft and edit result schemas."""

from __future__ import annotations

from pydantic import Field

from novel_forge.core.schemas.base import VersionedSchema
from novel_forge.core.utils.text_validation import count_chapter_words


class Draft(VersionedSchema):
    """A generated text draft (short story or single chapter)."""

    text: str = Field(description="The prose content.")
    word_count: int = Field(default=0, ge=0, description="Actual word count.")
    iteration: int = Field(default=1, ge=1, description="Which draft iteration this is.")

    def model_post_init(self, __context: object) -> None:
        """Auto-compute word count if not provided."""
        if self.word_count == 0 and self.text:
            object.__setattr__(self, "word_count", count_chapter_words(self.text))


class EditResult(VersionedSchema):
    """Result of an editing pass over a draft."""

    revised_text: str = Field(description="The edited prose.")
    edit_notes: list[str] = Field(
        default_factory=list,
        description="Notes on what was changed.",
    )
    iteration: int = Field(ge=1, description="Edit iteration number.")
    word_count: int = Field(default=0, ge=0)

    def model_post_init(self, __context: object) -> None:
        if self.word_count == 0 and self.revised_text:
            object.__setattr__(self, "word_count", count_chapter_words(self.revised_text))
