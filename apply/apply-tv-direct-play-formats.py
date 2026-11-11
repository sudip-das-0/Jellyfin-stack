#!/usr/bin/env python3
"""Apply H.264 + AAC custom formats to Sonarr/Radarr HD-1080p profiles."""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from arr_common import RADARR_CONFIG, RADARR_URL, SONARR_CONFIG, SONARR_URL, read_api_key
PROFILE_NAME = "HD-1080p"
ANIME_PROFILE_NAME = "HD-1080p Anime"

SCORES = {
    "TV Direct Play - x264": 1000,
    "TV Direct Play - AAC": 500,
    "TV Direct Play - English Audio": 800,
    "TV Direct Play - English Title": 350,
    "TV Direct Play - Japanese Audio": 450,
    "TV Direct Play - Korean Audio": 450,
    "TV Direct Play - Hindi Audio": 400,
    "TV Direct Play - Asian Multi": 300,
    "TV Direct Play - HEVC": -1000,
    "TV Direct Play - Surround": -750,
    "TV Direct Play - French Only": -600,
}

# Prefer releases with Japanese audio; minFormatScore requires Japanese track in the file.
ANIME_SCORES = {
    **SCORES,
    "TV Direct Play - Japanese Audio": 900,
    "TV Direct Play - English Audio": 400,
    "TV Direct Play - English Title": 200,
}

FORMATS = [
    {
        "name": "TV Direct Play - x264",
        "includeCustomFormatWhenRenaming": False,
        "specifications": [
            {
                "name": "x264",
                "implementation": "ReleaseTitleSpecification",
                "negate": False,
                "required": False,
                "fields": [
                    {
                        "order": 0,
                        "name": "value",
                        "label": "Regular Expression",
                        "value": "(x|h)\\.?264",
                        "type": "textbox",
                    }
                ],
            }
        ],
    },
    {
        "name": "TV Direct Play - AAC",
        "includeCustomFormatWhenRenaming": False,
        "specifications": [
            {
                "name": "AAC",
                "implementation": "ReleaseTitleSpecification",
                "negate": False,
                "required": False,
                "fields": [
                    {
                        "order": 0,
                        "name": "value",
                        "label": "Regular Expression",
                        "value": "\\b(?<!E)AAC(\\b|\\d)",
                        "type": "textbox",
                    }
                ],
            }
        ],
    },
    {
        "name": "TV Direct Play - HEVC",
        "includeCustomFormatWhenRenaming": False,
        "specifications": [
            {
                "name": "x265",
                "implementation": "ReleaseTitleSpecification",
                "negate": False,
                "required": False,
                "fields": [
                    {
                        "order": 0,
                        "name": "value",
                        "label": "Regular Expression",
                        "value": "(((x|h)\\.?265)|(HEVC))",
                        "type": "textbox",
                    }
                ],
            }
        ],
    },
    {
        "name": "TV Direct Play - Surround",
        "includeCustomFormatWhenRenaming": False,
        "specifications": [
            {
                "name": "Surround",
                "implementation": "ReleaseTitleSpecification",
                "negate": False,
                "required": False,
                "fields": [
                    {
                        "order": 0,
                        "name": "value",
                        "label": "Regular Expression",
                        "value": "DTS.?(HD|ES|X(?!\\D))|TRUEHD|ATMOS|DD(\\+|P).?([5-9])|EAC3.?([5-9])|\\bAC3\\b",
                        "type": "textbox",
                    }
                ],
            }
        ],
    },
    {
        "name": "TV Direct Play - English Audio",
        "includeCustomFormatWhenRenaming": False,
        "specifications": [
            {
                "name": "English",
                "implementation": "LanguageSpecification",
                "negate": False,
                "required": False,
                "fields": [
                    {"order": 0, "name": "value", "label": "Language", "value": 1, "type": "select"},
                    {"order": 1, "name": "exceptLanguage", "label": "Except Language", "value": False, "type": "checkbox"},
                ],
            }
        ],
    },
    {
        "name": "TV Direct Play - English Title",
        "includeCustomFormatWhenRenaming": False,
        "specifications": [
            {
                "name": "English in release name",
                "implementation": "ReleaseTitleSpecification",
                "negate": False,
                "required": False,
                "fields": [
                    {
                        "order": 0,
                        "name": "value",
                        "label": "Regular Expression",
                        "value": "(\\bENG\\b|\\bEN-US\\b|English|Dual[ .-]?Audio)",
                        "type": "textbox",
                    }
                ],
            }
        ],
    },
    {
        "name": "TV Direct Play - French Only",
        "includeCustomFormatWhenRenaming": False,
        "specifications": [
            {
                "name": "VOSTFR / VF",
                "implementation": "ReleaseTitleSpecification",
                "negate": False,
                "required": False,
                "fields": [
                    {
                        "order": 0,
                        "name": "value",
                        "label": "Regular Expression",
                        "value": "(VOSTFR|\\bVFQ\\b|\\bVFO\\b|FRENCH(?!.*\\bENG\\b))",
                        "type": "textbox",
                    }
                ],
            }
        ],
    },
    {
        "name": "TV Direct Play - Japanese Audio",
        "includeCustomFormatWhenRenaming": False,
        "specifications": [
            {
                "name": "Japanese",
                "implementation": "LanguageSpecification",
                "negate": False,
                "required": False,
                "fields": [
                    {"order": 0, "name": "value", "label": "Language", "value": 8, "type": "select"},
                    {"order": 1, "name": "exceptLanguage", "label": "Except Language", "value": False, "type": "checkbox"},
                ],
            }
        ],
    },
    {
        "name": "TV Direct Play - Korean Audio",
        "includeCustomFormatWhenRenaming": False,
        "specifications": [
            {
                "name": "Korean",
                "implementation": "LanguageSpecification",
                "negate": False,
                "required": False,
                "fields": [
                    {"order": 0, "name": "value", "label": "Language", "value": 21, "type": "select"},
                    {"order": 1, "name": "exceptLanguage", "label": "Except Language", "value": False, "type": "checkbox"},
                ],
            }
        ],
    },
    {
        "name": "TV Direct Play - Hindi Audio",
        "includeCustomFormatWhenRenaming": False,
        "specifications": [
            {
                "name": "Hindi",
                "implementation": "LanguageSpecification",
                "negate": False,
                "required": False,
                "fields": [
                    {"order": 0, "name": "value", "label": "Language", "value": 27, "type": "select"},
                    {"order": 1, "name": "exceptLanguage", "label": "Except Language", "value": False, "type": "checkbox"},
                ],
            }
        ],
    },
    {
        "name": "TV Direct Play - Asian Multi",
        "includeCustomFormatWhenRenaming": False,
        "specifications": [
            {
                "name": "Bengali/Hindi/Japanese/Korean in title",
                "implementation": "ReleaseTitleSpecification",
                "negate": False,
                "required": False,
                "fields": [
                    {
                        "order": 0,
                        "name": "value",
                        "label": "Regular Expression",
                        "value": "(\\bHIN(DI)?\\b|\\bBEN(GALI)?\\b|Bangla|\\bJPN\\b|\\bKOR\\b|Japanese|Korean)",
                        "type": "textbox",
                    }
                ],
            }
        ],
    },
]


def http_json(url: str, api_key: str, *, method: str = "GET", data: dict | None = None) -> object:
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={"X-Api-Key": api_key, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        raw = resp.read()
        return json.loads(raw.decode()) if raw else None


def ensure_formats(base_url: str, api_key: str) -> dict[str, int]:
    existing = http_json(f"{base_url}/api/v3/customformat", api_key)
    by_name = {cf["name"]: cf["id"] for cf in existing} if isinstance(existing, list) else {}

    for fmt in FORMATS:
        name = fmt["name"]
        if name in by_name:
            cf_id = by_name[name]
            http_json(f"{base_url}/api/v3/customformat/{cf_id}", api_key, method="PUT", data={**fmt, "id": cf_id})
            print(f"{base_url}: updated custom format {name} (id={cf_id})")
            continue
        created = http_json(f"{base_url}/api/v3/customformat", api_key, method="POST", data=fmt)
        by_name[name] = created["id"]
        print(f"{base_url}: created custom format {name} (id={created['id']})")

    return by_name


def _apply_profile_scores(
    base_url: str,
    api_key: str,
    profiles: list[dict],
    name_to_id: dict[str, int],
    *,
    profile_name: str,
    scores: dict[str, int],
    min_format_score: int,
    clone_from: str | None = None,
) -> dict:
    profile = next((p for p in profiles if p["name"] == profile_name), None)
    if profile is None:
        if not clone_from:
            raise RuntimeError(f"Profile {profile_name!r} not found on {base_url}")
        template = next((p for p in profiles if p["name"] == clone_from), None)
        if not template:
            raise RuntimeError(f"Template profile {clone_from!r} not found on {base_url}")
        profile = {k: v for k, v in template.items() if k != "id"}
        profile["name"] = profile_name
        profile = http_json(f"{base_url}/api/v3/qualityprofile", api_key, method="POST", data=profile)
        profiles.append(profile)
        print(f"{base_url}: created profile {profile_name} (id={profile['id']})")

    profile["formatItems"] = [
        {"format": name_to_id[fmt_name], "name": fmt_name, "score": score}
        for fmt_name, score in scores.items()
    ]
    profile["minFormatScore"] = min_format_score
    profile["minUpgradeFormatScore"] = 1

    http_json(
        f"{base_url}/api/v3/qualityprofile/{profile['id']}",
        api_key,
        method="PUT",
        data=profile,
    )
    print(
        f"{base_url}: applied scores to profile {profile_name} "
        f"(id={profile['id']}, minFormatScore={min_format_score})"
    )
    return profile


def apply_profile(base_url: str, api_key: str, name_to_id: dict[str, int]) -> None:
    profiles = http_json(f"{base_url}/api/v3/qualityprofile", api_key)
    _apply_profile_scores(
        base_url,
        api_key,
        profiles,
        name_to_id,
        profile_name=PROFILE_NAME,
        scores=SCORES,
        min_format_score=0,
    )


def apply_anime_profile(base_url: str, api_key: str, name_to_id: dict[str, int]) -> int:
    profiles = http_json(f"{base_url}/api/v3/qualityprofile", api_key)
    profile = _apply_profile_scores(
        base_url,
        api_key,
        profiles,
        name_to_id,
        profile_name=ANIME_PROFILE_NAME,
        scores=ANIME_SCORES,
        min_format_score=ANIME_SCORES["TV Direct Play - Japanese Audio"],
        clone_from=PROFILE_NAME,
    )
    return int(profile["id"])


def assign_sonarr_anime_quality_profile(base_url: str, api_key: str, anime_profile_id: int) -> None:
    series_list = http_json(f"{base_url}/api/v3/series", api_key)
    updated = 0
    for series in series_list:
        is_anime = series.get("seriesType") == "anime" or "/anime" in (
            series.get("rootFolderPath") or ""
        ).replace("\\", "/")
        if not is_anime or series.get("qualityProfileId") == anime_profile_id:
            continue
        body = {**series, "qualityProfileId": anime_profile_id}
        http_json(f"{base_url}/api/v3/series/{series['id']}", api_key, method="PUT", data=body)
        updated += 1
    print(f"{base_url}: set {ANIME_PROFILE_NAME} on {updated} anime series")


def main() -> int:
    for label, config, base in (
        ("Sonarr", SONARR_CONFIG, SONARR_URL),
        ("Radarr", RADARR_CONFIG, RADARR_URL),
    ):
        api_key = read_api_key(config)
        try:
            name_to_id = ensure_formats(base, api_key)
            apply_profile(base, api_key, name_to_id)
            if label == "Sonarr":
                anime_id = apply_anime_profile(base, api_key, name_to_id)
                assign_sonarr_anime_quality_profile(base, api_key, anime_id)
        except urllib.error.URLError as e:
            print(f"{label} unavailable: {e}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
