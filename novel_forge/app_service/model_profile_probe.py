"""Engine-owned, non-persistent connectivity checks for model profiles.

Both desktop clients need to validate a saved profile or an in-flight settings
draft.  The probe must not persist a draft API key, and the UI transport must
not reimplement the ``preserve``/``replace``/``clear`` secret semantics.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from novel_forge.gateway.profile_probe import ModelProfileProbeResult, probe_model_profile
from novel_forge.gateway.profiles import ModelProfile, load_or_import_profiles


class ModelProfileProbeRequestError(ValueError):
    """The submitted temporary model-profile probe is not safe to run."""


@dataclass(frozen=True)
class ModelProfileProbeRequest:
    """A transient profile candidate submitted by a replaceable UI client."""

    profile_id: str
    provider: str
    model: str
    previous_profile_id: str | None = None
    api_key_action: Literal["preserve", "replace", "clear"] = "preserve"
    api_key: str | None = None
    base_url: str = ""


async def probe_model_profile_request(
    request: ModelProfileProbeRequest,
) -> ModelProfileProbeResult:
    """Probe a profile draft without writing it or exposing its API key.

    ``preserve`` resolves the existing protected key only for the duration of
    the provider call. ``replace`` and ``clear`` apply only to this transient
    candidate.  The result contains diagnostic capability data, never secrets.
    """

    profile_id = request.profile_id.strip()
    provider = request.provider.strip().lower()
    model = request.model.strip()
    if not profile_id:
        raise ModelProfileProbeRequestError("模型档案 ID 不能为空")
    if not provider:
        raise ModelProfileProbeRequestError("供应商不能为空")
    if not model:
        raise ModelProfileProbeRequestError("模型 ID 不能为空")

    action = request.api_key_action.strip().lower()
    if action not in {"preserve", "replace", "clear"}:
        raise ModelProfileProbeRequestError("无效的 API Key 操作")

    config = load_or_import_profiles()
    previous = config.get_profile(request.previous_profile_id or profile_id)
    api_key = (
        (request.api_key or "").strip()
        if action == "replace"
        else ""
        if action == "clear"
        else previous.api_key
        if previous is not None
        else ""
    )
    profile = ModelProfile(
        profile_id=profile_id,
        display_name=profile_id,
        provider=provider,
        model_id=model,
        api_key=api_key,
        base_url=request.base_url.strip(),
    )
    return await probe_model_profile(profile)
