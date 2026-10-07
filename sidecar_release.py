"""Pick episode-correct English subtitles from Sonarr/Radarr release folders."""
from __future__ import annotations

import importlib.util
import re
import shutil
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_POLICY_PATH = _SCRIPT_DIR / "media-lang-policy.py"
_spec = importlib.util.spec_from_file_location("mlp", _POLICY_PATH)
mlp = importlib.util.module_from_spec(_spec)
assert _spec.loader
_spec.loader.exec_module(mlp)

_EPISODE = re.compile(
    r"(?i)(?<![0-9])s(\d{1,2})e(\d{1,2})(?![0-9])",
)


def episode_code_label(text: str) -> str | None:
    m = _EPISODE.search(text)
    if not m:
        return None
    return f"S{int(m.group(1)):02d}E{int(m.group(2)):02d}"


def is_english_release_subtitle_name(name: str) -> bool:
    if mlp.is_foreign_sidecar_name(name):
        return False
    lower = name.lower()
    if "eng" in lower or ".en." in lower or "english" in lower:
        return True
    if lower.endswith(".srt"):
        return True
    return False


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

    srt_ep = episode_code_label(name)
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

    show_hint = ""
    parts = video_path.parts
    if "series" in parts:
        idx = parts.index("series")
        if idx + 1 < len(parts):
            show_hint = parts[idx + 1].lower().replace(" ", "")

    best: tuple[int, str, Path] | None = None
    for srt in torrent_root.rglob("*.srt"):
        if episode_code_label(srt.name) != video_ep:
            continue
        if show_hint:
            folder_blob = str(srt.parent).lower().replace(" ", "")
            if show_hint[:8] not in folder_blob and show_hint not in folder_blob:
                # loose: require show token in torrent path
                tokens = [t for t in re.split(r"[^a-z0-9]+", show_hint) if len(t) >= 4]
                if tokens and not any(t in folder_blob for t in tokens):
                    continue
        score = score_release_candidate(video_path.stem, video_ep, srt)
        if score is None:
            continue
        key = (score, srt.name.lower(), srt)
        if best is None or key < best:
            best = key
    return best[2] if best else None
