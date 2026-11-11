#!/usr/bin/env python3
"""
Global language policy (single source of truth).

Subtitles: English only — Bazarr downloads, Jellyfin subtitle fetch, import sidecars, LG extract.
Audio: English + Japanese + Korean + Hindi + Bengali — Sonarr/Radarr grabs, LG remux track pick.
"""
from __future__ import annotations

import re
from pathlib import Path

# --- Audio (releases + remux; English is default playback) ---
AUDIO_ALLOWED = frozenset(
    {
        "eng",
        "en",
        "english",
        "jpn",
        "ja",
        "japanese",
        "kor",
        "ko",
        "korean",
        "hin",
        "hi",
        "hindi",
        "ben",
        "bn",
        "bengali",
        "bangla",
    }
)

AUDIO_PRIORITY = {
    "eng": 0,
    "en": 0,
    "english": 0,
    "jpn": 1,
    "ja": 1,
    "japanese": 1,
    "kor": 2,
    "ko": 2,
    "korean": 2,
    "hin": 3,
    "hi": 3,
    "hindi": 3,
    "ben": 4,
    "bn": 4,
    "bengali": 4,
    "bangla": 4,
}

# Jellyfin / ffprobe-style tags for allowed audio languages
JELLYFIN_AUDIO_LANGS = ("eng", "jpn", "kor", "hin", "ben")

# --- Subtitles (English only everywhere) ---
SUBTITLE_ALLOWED = frozenset({"eng", "en", "english"})
JELLYFIN_SUBTITLE_LANGS = ("eng",)
BAZARR_SUBTITLE_LANGS = ("en",)
SIDECAR_SUFFIX = "eng"
# Only this external sidecar is kept beside each video (Jellyfin shows one English sub).
CANONICAL_SUB_FILENAME_SUFFIX = f".{SIDECAR_SUFFIX}.srt"

# Jellyfin library: never expose embedded sub menus (external .eng.srt only).
JELLYFIN_ALLOW_EMBEDDED_SUBTITLES = "AllowNone"

# Prefer one English sidecar from multi-sub releases (many false "eng" tags).
_SUBTITLE_TITLE_ENGLISH = re.compile(
    r"\b(english|en-us|en-gb|full.?subtitle|full.?sub)\b", re.I
)
_SUBTITLE_TITLE_AVOID = re.compile(
    r"\b(forced|force|sdh|cc|closed.?caption|commentary|descr|signs.?only|signs)\b", re.I
)
_SUBTITLE_TITLE_FOREIGN = re.compile(
    r"\b(japan|japanese|jpn|french|fran|fre|spanish|spa|german|ger|deu|"
    r"chinese|chi|zho|korean|kor|portuguese|por|italian|ita|"
    r"arabic|ara|russian|rus|hindi|hin|bengali|ben|thai|tha|"
    r"vietnamese|vie|polish|pol|dutch|dut|nld|turkish|tur)\b",
    re.I,
)


def normalize(lang: str) -> str:
    return (lang or "").strip().lower()


def allowed(lang: str) -> bool:
    """Allowed audio language tag (remux / track pick)."""
    return normalize(lang) in AUDIO_ALLOWED


def priority(lang: str) -> int:
    return AUDIO_PRIORITY.get(normalize(lang), 99)


def subtitle_allowed(lang: str) -> bool:
    return normalize(lang) in SUBTITLE_ALLOWED


def sidecar_suffix(lang: str) -> str:
    if subtitle_allowed(lang):
        return SIDECAR_SUFFIX
    return ""


def _subtitle_track_score(lang: str, title: str) -> int:
    """Lower is better."""
    lang_n = normalize(lang)
    title = title or ""
    score = 0
    if subtitle_allowed(lang_n):
        score -= 40
    elif _SUBTITLE_TITLE_ENGLISH.search(title):
        score -= 25
    else:
        return 9999
    if _SUBTITLE_TITLE_ENGLISH.search(title):
        score -= 15
    if _SUBTITLE_TITLE_AVOID.search(title):
        score += 35
    if _SUBTITLE_TITLE_FOREIGN.search(title):
        score += 80
    if not lang_n:
        score += 10
    return score


def pick_sidecar_subtitles(streams: list[dict]) -> list[tuple[int, str]]:
    """
    Return at most one (stream_index, sidecar_suffix) to extract before remux (-sn).
    """
    candidates: list[tuple[int, int, str]] = []
    for stream in streams:
        if stream.get("codec_type") != "subtitle":
            continue
        tags = stream.get("tags") or {}
        lang = tags.get("language") or tags.get("LANGUAGE") or ""
        title = tags.get("title") or tags.get("TITLE") or ""
        score = _subtitle_track_score(lang, title)
        if score >= 9999:
            continue
        idx = stream.get("index")
        if idx is None:
            continue
        suf = sidecar_suffix(lang) or SIDECAR_SUFFIX
        candidates.append((score, int(idx), suf))
    if not candidates:
        return []
    candidates.sort(key=lambda row: (row[0], row[1]))
    _, best_idx, best_suf = candidates[0]
    return [(best_idx, best_suf)]


def canonical_sidecar_path(video_path: str | Path) -> Path:
    video_path = Path(video_path)
    return video_path.with_suffix(CANONICAL_SUB_FILENAME_SUFFIX)


_FOREIGN_SIDECAR_LANG = re.compile(
    r"\.(jpn|ja|japanese|kor|ko|korean|hin|hi|hindi|ben|bn|bengali|"
    r"fre|fra|fr|french|deu|ger|de|german|spa|es|spanish|ita|it|italian|"
    r"por|pt|portuguese|rus|ru|russian|ara|ar|arabic|zho|chi|zh|chinese|"
    r"vie|vi|vietnamese|pol|pl|polish|nld|dut|nl|dutch|tur|tr|turkish|"
    r"tha|th|thai|swe|sv|swedish|nor|no|norwegian|dan|da|danish|"
    r"fin|fi|finnish|ces|cs|czech|hun|hu|hungarian|ron|ro|romanian|"
    r"heb|he|hebrew|ukr|uk|ukrainian|ell|el|greek)\.srt$",
    re.I,
)


def is_foreign_sidecar_name(name: str) -> bool:
    return bool(_FOREIGN_SIDECAR_LANG.search(name))


# WEB release folders: 2_English.srt, 10_German.srt, 24_Lithuanian.srt, …
_RELEASE_NUMBERED_LANG = re.compile(r"(?i)^\d+_([a-z0-9]+)\.srt$")
_RELEASE_LANG_FOREIGN = frozenset(
    {
        "arabic",
        "bengali",
        "ben",
        "bulgarian",
        "chinese",
        "czech",
        "danish",
        "dutch",
        "finnish",
        "french",
        "fre",
        "ger",
        "german",
        "deu",
        "greek",
        "ell",
        "hebrew",
        "hin",
        "hindi",
        "hungarian",
        "indonesian",
        "italian",
        "japanese",
        "jpn",
        "korean",
        "kor",
        "lithuanian",
        "norwegian",
        "polish",
        "portuguese",
        "romanian",
        "russian",
        "slovak",
        "slovenian",
        "spanish",
        "spa",
        "swedish",
        "tam",
        "tamil",
        "tel",
        "telugu",
        "thai",
        "turkish",
        "ukrainian",
        "vietnamese",
    }
)


def is_foreign_subtitle_release_name(name: str) -> bool:
    """Foreign language track in a release/torrent folder (not canonical .eng.srt)."""
    if is_foreign_sidecar_name(name):
        return True
    if _SUBTITLE_TITLE_FOREIGN.search(name):
        return True
    m = _RELEASE_NUMBERED_LANG.match(name)
    if m:
        tok = m.group(1).lower()
        if tok in ("english", "eng", "en"):
            return False
        if tok in _RELEASE_LANG_FOREIGN:
            return True
        # Unknown token in numbered WEB subs — treat as foreign unless explicitly English.
        return True
    return False


def is_english_release_subtitle_name(name: str) -> bool:
    """English .srt candidate inside a release folder (import / torrent repair)."""
    if is_foreign_subtitle_release_name(name):
        return False
    lower = name.lower()
    if re.search(r"(?<![a-z])(english|eng(?![a-z]))", lower):
        return True
    if "eng" in lower or ".en." in lower:
        return True
    # Episode-named subs without a language tag (e.g. Vyndros BluRay): English by policy.
    if _EPISODE_IN_NAME.search(name) and lower.endswith(".srt"):
        return True
    return False


_EPISODE_IN_NAME = re.compile(r"(?i)(?<![0-9])s(\d{1,2})e(\d{1,2})(?![0-9])")


def is_english_sidecar_variant(video_stem: str, sidecar_path: Path) -> bool:
    """Sidecar belongs to this video stem and is an English (non-foreign) track."""
    name = sidecar_path.name
    if not name.startswith(video_stem) or not name.endswith(".srt"):
        return False
    if is_foreign_sidecar_name(name):
        return False
    tail = name[len(video_stem) :].lower()
    if tail in (".srt", ".eng.srt", ".en.srt", ".english.srt"):
        return True
    if tail.startswith(".en.") and tail.endswith(".srt"):
        return True
    if "english" in tail or ".eng." in tail:
        return True
    return False


def english_sidecar_pick_score(name: str) -> int:
    """Lower is better when consolidating duplicates to .eng.srt."""
    lower = name.lower()
    if lower.endswith(".eng.srt"):
        return 0
    if lower.endswith(".en.srt"):
        return 10
    if lower.endswith(".english.srt"):
        return 12
    if lower.endswith(".srt"):
        return 20
    if ".en.hi." in lower or lower.endswith(".hi.srt") or "sdh" in lower:
        return 40
    if "forced" in lower or ".forced." in lower:
        return 50
    return 30
