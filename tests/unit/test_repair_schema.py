from __future__ import annotations

import pytest
from pydantic import ValidationError

from novel_forge.core.schemas.repair import RepairCandidate, RepairVerificationBundle


def test_changed_candidate_requires_exact_patch_evidence() -> None:
    with pytest.raises(ValidationError, match="exact patch evidence"):
        RepairCandidate(
            case_id="case",
            version=1,
            base_hash="before",
            candidate_hash="after",
            blob_hash="blob",
            origin="model",
            patch_count=0,
        )


def test_passing_verification_requires_original_validator_evidence() -> None:
    with pytest.raises(ValidationError, match="original validator evidence"):
        RepairVerificationBundle(
            case_id="case",
            candidate_version=1,
            candidate_hash="after",
            passed=True,
            resolved_issue_ids=["issue"],
        )
