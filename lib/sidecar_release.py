"""Pick episode-correct English subtitles from Sonarr/Radarr release folders."""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from arr_common import load_media_lang_policy

mlp = load_media_lang_policy()

_EPISODE = re.compile(
    r"(?i)(?<![0-9])s(\d{1,2})e(\d{1,2})(?![0-9])",
)


def episode_code_label(text: str) -> str | None:
    m = _EPISODE.search(text)
    if not m:
        return None
    return f"S{int(m.group(1)):02d}E{int(m.group(2)):02d}"


_LIBRARY_ROOTS = ("series", "anime", "movies", "other")


def library_show_name(video_path: Path) -> str:
    parts = video_path.parts
    for root in _LIBRARY_ROOTS:
        if root in parts:
            idx = parts.index(root)
            if idx + 1 < len(parts):
                return parts[idx + 1]
    return ""


def show_match_tokens(show_name: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", show_name.lower()) if len(t) >= 4]


def path_matches_show(path: Path, tokens: list[str]) -> bool:
    if not tokens:
        return True
    blob = str(path).lower().replace(" ", "")
    return any(t in blob for t in tokens)


def release_subtitle_episode_code(srt: Path) -> str | None:
    """SxxExx from the .srt name or a parent folder (e.g. Subs/Show.S02E02…/2_English.srt)."""
    code = episode_code_label(srt.name)
    if code:
        return code
    for parent in srt.parents:
        code = episode_code_label(parent.name)
        if code:
            return code
    return None


def is_english_release_subtitle_name(name: str) -> bool:
    return mlp.is_english_release_subtitle_name(name)


def _stem_overlap_score(video_stem: str, sub_stem: str) -> int:
    """Lower is better. 0 = sub stem matches start of video stem."""
    if video_stem == sub_stem or video_stem.startswith(sub_stem + ".") or video_stem.startswith(sub_stem):
        return 0
    if sub_stem.startswith(video_stem):
        return 5
    v_ep = episode_code_label(video_stem)
    s_ep = episode_code_label(sub_stem)
    if v_ep and v_ep == s_ep:
        return 15
    return 100


def score_release_candidate(video_stem: str, video_ep: str | None, srt: Path) -> int | None:
    name = srt.name
    if not is_english_release_subtitle_name(name):
        return None
    if mlp.is_foreign_sidecar_name(name):
        return None

    srt_ep = release_subtitle_episode_code(srt)
    if video_ep:
        if not srt_ep or srt_ep.upper() != video_ep.upper():
            return None
    else:
        # Movie: require filename overlap with the video stem (avoid season-pack bleed).
        sub_stem = srt.stem
        overlap = _stem_overlap_score(video_stem, sub_stem)
        if overlap >= 100:
            return None

    score = mlp.english_sidecar_pick_score(name)
    score += _stem_overlap_score(video_stem, srt.stem)
    if _SUBTITLE_TITLE_AVOID(name):
        score += 25
    return score


def _SUBTITLE_TITLE_AVOID(name: str) -> bool:
    return bool(mlp._SUBTITLE_TITLE_AVOID.search(name))


def iter_release_srts(root: Path):
    if not root.is_dir():
        return
    seen: set[Path] = set()
    patterns = (
        "*.srt",
        "*/*.srt",
        "Subs/*.srt",
        "Subs/*/*.srt",
    )
    for pattern in patterns:
        for path in root.glob(pattern):
            if path.is_file():
                resolved = path.resolve()
                if resolved not in seen:
                    seen.add(resolved)
                    yield path


def find_best_release_sidecar(video_path: Path, search_dirs: list[Path]) -> Path | None:
    video_path = Path(video_path)
    video_stem = video_path.stem
    video_ep = episode_code_label(video_stem)

    best: tuple[int, str, Path] | None = None
    for folder in search_dirs:
        if not folder:
            continue
        folder = Path(folder)
        for subdir in (folder, folder / "Subs"):
            for srt in iter_release_srts(subdir):
                score = score_release_candidate(video_stem, video_ep, srt)
                if score is None:
                    continue
                key = (score, srt.name.lower(), srt)
                if best is None or key < best:
                    best = key
    return best[2] if best else None


def import_sidecar_for_video(
    video_path: Path,
    search_dirs: list[Path],
    *,
    overwrite: bool = False,
) -> Path | None:
    video_path = Path(video_path)
    dest = mlp.canonical_sidecar_path(video_path)
    if dest.is_file() and not overwrite:
        return None
    source = find_best_release_sidecar(video_path, search_dirs)
    if not source:
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, dest)
    return dest


def find_torrent_sidecar_for_video(
    video_path: Path,
    torrent_root: Path,
) -> Path | None:
    """Match a video under media/ to a release .srt under torrent_root/."""
    video_path = Path(video_path)
    video_ep = episode_code_label(video_path.stem)
    if not video_ep:
        return None
    if not torrent_root.is_dir():
        return None

    tokens = show_match_tokens(library_show_name(video_path))

    def consider(candidate: Path) -> None:
        nonlocal best
        score = score_release_candidate(video_path.stem, video_ep, candidate)
        if score is None:
            return
        key = (score, candidate.name.lower(), candidate)
        if best is None or key < best:
            best = key

    best: tuple[int, str, Path] | None = None
    for srt in torrent_root.rglob("*.srt"):
        if release_subtitle_episode_code(srt) != video_ep:
            continue
        if not path_matches_show(srt, tokens):
            continue
        consider(srt)

    video_suffixes = {".mkv", ".mp4"}
    for release in torrent_root.rglob("*"):
        if release.suffix.lower() not in video_suffixes or not release.is_file():
            continue
        if release_subtitle_episode_code(release) != video_ep:
            continue
        if not path_matches_show(release, tokens):
            continue
        folder = release.parent
        stem = release.stem
        for candidate in (
            folder / f"{stem}.srt",
            folder / f"{stem}.eng.srt",
            folder / "Subs" / f"{stem}.srt",
        ):
            if candidate.is_file():
                consider(candidate)
        subs_dir = folder / "Subs" / stem
        if subs_dir.is_dir():
            for candidate in subs_dir.glob("*.srt"):
                if candidate.is_file():
                    consider(candidate)

    return best[2] if best else None
