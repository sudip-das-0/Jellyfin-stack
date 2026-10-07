#!/usr/bin/env python3
"""Apply H.264 + AAC custom formats to Sonarr/Radarr HD-1080p profiles."""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from arr_common import RADARR_CONFIG, RADARR_URL, SONARR_CONFIG, SONARR_URL, read_api_key
PROFILE_NAME = "HD-1080p"

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


def apply_profile(base_url: str, api_key: str, name_to_id: dict[str, int]) -> None:
    profiles = http_json(f"{base_url}/api/v3/qualityprofile", api_key)
    profile = next((p for p in profiles if p["name"] == PROFILE_NAME), None)
    if not profile:
        raise RuntimeError(f"Profile {PROFILE_NAME!r} not found on {base_url}")

    profile["formatItems"] = [
        {"format": name_to_id[fmt_name], "name": fmt_name, "score": score}
        for fmt_name, score in SCORES.items()
    ]
    profile["minFormatScore"] = 0
    profile["minUpgradeFormatScore"] = 1

    http_json(
        f"{base_url}/api/v3/qualityprofile/{profile['id']}",
        api_key,
        method="PUT",
        data=profile,
    )
    print(f"{base_url}: applied scores to profile {PROFILE_NAME} (id={profile['id']})")


def main() -> int:
    for label, config, base in (
        ("Sonarr", SONARR_CONFIG, SONARR_URL),
        ("Radarr", RADARR_CONFIG, RADARR_URL),
    ):
        api_key = read_api_key(config)
        try:
            name_to_id = ensure_formats(base, api_key)
            apply_profile(base, api_key, name_to_id)
        except urllib.error.URLError as e:
            print(f"{label} unavailable: {e}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
