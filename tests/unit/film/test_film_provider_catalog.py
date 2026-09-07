from __future__ import annotations

from novel_forge.film.providers.base import FilmGenerationMode
from novel_forge.film.providers.catalog import FILM_PROVIDER_CATALOG, film_provider_catalog


def test_catalog_exposes_latest_bailian_minimax_and_volcengine_models() -> None:
    bailian = FILM_PROVIDER_CATALOG["bailian"]
    minimax = FILM_PROVIDER_CATALOG["minimax"]
    volcengine = FILM_PROVIDER_CATALOG["volcengine_ark"]

    assert {item["id"] for item in bailian["video_models"]} >= {
        "wan2.7-t2v",
        "wan2.7-i2v",
        "wan2.7-r2v",
    }
    assert {item["id"] for item in bailian["image_models"]} >= {
        "wan2.7-image-pro",
        "qwen-image-2.0-pro",
    }
    assert {item["id"] for item in minimax["video_models"]} >= {
        "MiniMax-H3",
        "MiniMax-Hailuo-2.3",
        "MiniMax-Hailuo-02",
        "S2V-01",
    }
    h3 = next(item for item in minimax["video_models"] if item["id"] == "MiniMax-H3")
    assert minimax["recommended"] is True
    assert h3["recommended"] is True
    assert h3["durations"] == {"min": 4, "max": 15}
    assert h3["default_resolution"] == "768P"
    assert FilmGenerationMode.SUBJECT_REFERENCE_VIDEO.value in h3["modes"]
    assert {"native_audio", "multi_modal_reference", "context_ir", "queued_cancel"} <= set(
        h3["features"]
    )
    assert h3["reference_limits"]["images"] == 9
    assert minimax["audio_models"]["speech"][0] == "speech-2.8-hd"
    assert minimax["audio_models"]["music"][0] == "music-3.0"
    assert {item["id"] for item in volcengine["video_models"]} >= {
        "doubao-seedance-2-0-260128",
        "doubao-seedance-2-0-fast-260128",
    }
    assert volcengine["image_models"][0]["id"] == "doubao-seedream-5-0-lite-260128"
    assert "asset://" in volcengine["asset_schemes"]
    seedance = volcengine["video_models"][0]
    assert {"generate_audio", "return_last_frame", "seed"} <= set(seedance["features"])

    wan_t2v = next(item for item in bailian["video_models"] if item["id"] == "wan2.7-t2v")
    assert {"negative_prompt", "prompt_optimizer", "driving_audio"} <= set(
        wan_t2v["features"]
    )


def test_catalog_declares_advanced_film_modes_and_returns_copy() -> None:
    catalog = film_provider_catalog()
    r2v = next(item for item in catalog["bailian"]["video_models"] if item["id"] == "wan2.7-r2v")
    assert FilmGenerationMode.REFERENCE_TO_VIDEO.value in r2v["modes"]
    assert "storyboard_reference" in r2v["features"]

    catalog["bailian"]["label"] = "changed"
    assert FILM_PROVIDER_CATALOG["bailian"]["label"] == "阿里百炼"
