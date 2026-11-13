#!/usr/bin/env python3
"""Apply Bazarr/Jellyfin/*arr language policy (English subs, allowed audio)."""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from arr_common import (  # noqa: E402
    BAZARR_CONFIG,
    BAZARR_DB,
    JELLYFIN_CONFIG_ROOT,
    RADARR_CONFIG,
    RADARR_URL,
    SONARR_CONFIG,
    SONARR_URL,
    arr_json,
    load_media_lang_policy,
    read_api_key,
)


def apply_bazarr_subtitle_profile(mlp) -> None:
    items = [
        {
            "language": c,
            "forced": False,
            "hi": False,
            "audio_only": False,
            "itemType": "language",
            "id": i + 1,
            "audio_exclude": "False",
            "audio_only_include": "False",
        }
        for i, c in enumerate(mlp.BAZARR_SUBTITLE_LANGS)
    ]
    conn = sqlite3.connect(BAZARR_DB)
    conn.execute(
        "UPDATE table_languages_profiles SET name=?, items=? WHERE profileId=1",
        ("English subtitles", json.dumps(items)),
    )
    conn.commit()
    conn.close()
    print("Bazarr subtitle profile:", list(mlp.BAZARR_SUBTITLE_LANGS))
    print("Audio allow-list (grabs/remux):", list(mlp.JELLYFIN_AUDIO_LANGS))


def apply_arr_release_profiles() -> None:
    for base, config in ((SONARR_URL, SONARR_CONFIG), (RADARR_URL, RADARR_CONFIG)):
        key = read_api_key(config)
        profiles = arr_json(f"{base}/api/v3/releaseprofile", key)
        body = {
            "enabled": True,
            "required": [],
            "ignored": ["VOSTFR", "VFQ", "VFO", "SUBFRENCH"],
            "indexerId": 0,
            "tags": [],
        }
        if isinstance(profiles, list) and profiles:
            body = {**profiles[0], **body}
            arr_json(
                f"{base}/api/v3/releaseprofile/{profiles[0]['id']}",
                key,
                method="PUT",
                body=body,
            )
        else:
            arr_json(f"{base}/api/v3/releaseprofile", key, method="POST", body=body)
        print(f"{base}: release profile updated")


def apply_jellyfin_library_options(mlp) -> None:
    subtitle_langs = list(mlp.JELLYFIN_SUBTITLE_LANGS)
    block = "\n".join(f"    <string>{c}</string>" for c in subtitle_langs)
    for name in ("Series", "Anime", "Movies", "Other"):
        p = JELLYFIN_CONFIG_ROOT / "root/default" / name / "options.xml"
        text = p.read_text(encoding="utf-8")
        new = re.sub(
            r"<SubtitleDownloadLanguages>.*?</SubtitleDownloadLanguages>",
            f"<SubtitleDownloadLanguages>\n{block}\n  </SubtitleDownloadLanguages>",
            text,
            count=1,
            flags=re.DOTALL,
        )
        new = re.sub(
            r"<AllowEmbeddedSubtitles>.*?</AllowEmbeddedSubtitles>",
            f"<AllowEmbeddedSubtitles>{mlp.JELLYFIN_ALLOW_EMBEDDED_SUBTITLES}</AllowEmbeddedSubtitles>",
            new,
            count=1,
            flags=re.DOTALL,
        )
        new = re.sub(
            r"<RequirePerfectSubtitleMatch>.*?</RequirePerfectSubtitleMatch>",
            "<RequirePerfectSubtitleMatch>false</RequirePerfectSubtitleMatch>",
            new,
            count=1,
            flags=re.DOTALL,
        )
        p.write_text(new, encoding="utf-8")
        print(
            f"Jellyfin {p.parent.name}: subtitle langs {subtitle_langs}, "
            f"embedded={mlp.JELLYFIN_ALLOW_EMBEDDED_SUBTITLES}"
        )


def apply_bazarr_defaults() -> None:
    cfg = Path(BAZARR_CONFIG)
    text = cfg.read_text(encoding="utf-8")
    replacements = {
        r"^  serie_default_profile: .*$": "  serie_default_profile: 1",
        r"^  movie_default_profile: .*$": "  movie_default_profile: 1",
        r"^  single_language: .*$": "  single_language: true",
        r"^  use_embedded_subs: .*$": "  use_embedded_subs: false",
    }
    for pattern, repl in replacements.items():
        text = re.sub(pattern, repl, text, count=1, flags=re.M)
    cfg.write_text(text, encoding="utf-8")
    print(
        "Bazarr defaults: profileId=1 (English subtitles), "
        "single_language=true, use_embedded_subs=false"
    )


def main() -> int:
    mlp = load_media_lang_policy()
    apply_bazarr_subtitle_profile(mlp)
    apply_arr_release_profiles()
    apply_jellyfin_library_options(mlp)
    apply_bazarr_defaults()
    return 0


if __name__ == "__main__":
    sys.exit(main())
