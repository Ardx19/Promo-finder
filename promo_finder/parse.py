"""Parse timestamped microdrama screenplays (the Kuku input-pack format) into a Show.

Format, per episode file:
    EPISODE NUMBER: Episode 1
    EPISODE LOGLINE: ...
    CONTINUITY NOTES: ...
    --- SCRIPT START ---
    SCENE 01
    Scene heading: INT. ... (header case varies between files)
    Scene start: 00:00.00
    Scene end: 02:18.00          (sometimes missing)
    **CAMERA:** | **ACTION:** | **SOUND FX:** | **SPEAKER (Tag, Tag):**
    Start: (00:00.00) | End: (00:04.80)
    text...
"""
from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

from .models import Beat, Episode, Show

HEADER_RE = re.compile(r"^\*\*(?P<label>[^*]+?):\*\*\s*$")
TIME_RE = re.compile(r"Start:\s*\(?\s*(?P<s>[\d:.]+)\s*\)?\s*\|\s*End:\s*\(?\s*(?P<e>[\d:.]+)\s*\)?", re.I)
EP_NUM_RE = re.compile(r"^EPISODE NUMBER:\s*(?:Episode\s*)?(\d+)", re.I)
LOGLINE_RE = re.compile(r"^EPISODE LOGLINE:\s*(.*)", re.I)
CONT_RE = re.compile(r"^CONTINUITY NOTES:\s*(.*)", re.I)
SCENE_RE = re.compile(r"^SCENE\s+(\d+)\s*$", re.I)
FILE_EP_RE = re.compile(r"episode[-_ ]?0*(\d+)", re.I)

KIND_MAP = {"CAMERA": "CAMERA", "ACTION": "ACTION", "SOUND FX": "SFX", "SFX": "SFX", "SOUND": "SFX", "MUSIC": "SFX"}


class ParseError(ValueError):
    pass


def parse_ts(value: str) -> float:
    """'MM:SS.ss', 'H:MM:SS.ss' or 'SS.ss' -> seconds."""
    parts = value.strip().split(":")
    try:
        nums = [float(p) for p in parts]
    except ValueError as exc:
        raise ParseError(f"bad timestamp {value!r}") from exc
    total = 0.0
    for n in nums:
        total = total * 60 + n
    return total


def _split_label(label: str) -> tuple[str, str | None, list[str]]:
    """'LADY X (Cold, Commanding)' -> ('DIALOGUE', 'LADY X', ['Cold', 'Commanding'])."""
    label = label.strip()
    m = re.match(r"^(?P<name>[^()]+?)\s*\((?P<tags>.*)\)\s*$", label)
    name, tags = (m.group("name").strip(), m.group("tags")) if m else (label, "")
    kind = KIND_MAP.get(name.upper())
    if kind:
        return kind, None, []
    emotions = [t.strip() for t in tags.split(",") if t.strip()]
    return "DIALOGUE", name, emotions


def parse_episode_text(text: str, source_name: str = "") -> Episode:
    lines = text.replace("\r\n", "\n").split("\n")
    number = None
    logline = continuity = ""
    scene = 0
    beats: list[dict] = []
    current: dict | None = None
    scene_end_max = 0.0

    def flush():
        nonlocal current
        if current is not None:
            if current.get("start") is None:
                raise ParseError(f"{source_name}: block '{current['label']}' has no Start/End line")
            current["text"] = " ".join(t.strip() for t in current["text"] if t.strip())
            beats.append(current)
        current = None

    for raw in lines:
        line = raw.strip()
        if number is None and (m := EP_NUM_RE.match(line)):
            number = int(m.group(1))
            continue
        if (m := LOGLINE_RE.match(line)) and not logline:
            logline = m.group(1).strip()
            continue
        if (m := CONT_RE.match(line)) and not continuity:
            continuity = m.group(1).strip()
            continue
        if line.startswith("---"):
            flush()
            continue
        if m := SCENE_RE.match(line):
            flush()
            scene = int(m.group(1))
            continue
        if re.match(r"^scene end:", line, re.I):
            try:
                scene_end_max = max(scene_end_max, parse_ts(line.split(":", 1)[1]))
            except ParseError:
                pass
            continue
        if re.match(r"^(scene heading|scene start|actions and dialogue)\s*:", line, re.I):
            continue
        if m := HEADER_RE.match(line):
            flush()
            kind, speaker, emotions = _split_label(m.group("label"))
            current = {"label": m.group("label"), "kind": kind, "speaker": speaker, "emotions": emotions,
                       "scene": max(scene, 1), "start": None, "end": None, "text": []}
            continue
        if current is not None:
            if current["start"] is None and (m := TIME_RE.search(line)):
                current["start"] = parse_ts(m.group("s"))
                current["end"] = parse_ts(m.group("e"))
            elif line:
                current["text"].append(line)
    flush()

    if number is None:
        m = FILE_EP_RE.search(source_name)
        if not m:
            raise ParseError(f"{source_name}: no EPISODE NUMBER header and no episode number in filename")
        number = int(m.group(1))
    if not beats:
        raise ParseError(f"{source_name}: no beats found")

    out = []
    for i, b in enumerate(beats, 1):
        out.append(Beat(id=f"E{number:02d}-B{i:03d}", episode=number, scene=b["scene"], kind=b["kind"],
                        start=b["start"], end=max(b["end"], b["start"]), speaker=b["speaker"],
                        emotions=b["emotions"], text=b["text"]))
    duration = max(scene_end_max, max(b.end for b in out))
    return Episode(number=number, source_name=source_name, logline=logline, continuity=continuity,
                   beats=out, duration=duration)


def _slugify(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return s[:60] or "show"


def _clean_title(name: str) -> str:
    name = re.sub(r"\.zip$", "", name, flags=re.I)
    name = re.sub(r"[-_ ]*scripts?\b.*$", "", name, flags=re.I)  # "...-scripts (2) (1)"
    return name.strip(" -_") or "show"


def load_show(path: str | Path, title: str | None = None) -> Show:
    """Load a show from a .zip or a folder of episode .txt files."""
    path = Path(path)
    files: list[tuple[str, str]] = []
    if path.is_file() and path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as zf:
            for info in zf.infolist():
                if info.is_dir() or not info.filename.lower().endswith(".txt"):
                    continue
                base = Path(info.filename).name
                if base.startswith("._"):
                    continue
                files.append((base, zf.read(info).decode("utf-8-sig", errors="replace")))
        inferred = _clean_title(path.name)
        tops = {Path(i).parts[0] for i in zipfile.ZipFile(path).namelist() if len(Path(i).parts) > 1}
        if len(tops) == 1:
            inferred = tops.pop()
    elif path.is_dir():
        for f in sorted(path.rglob("*.txt")):
            files.append((f.name, f.read_text(encoding="utf-8-sig", errors="replace")))
        inferred = path.name
    else:
        raise ParseError(f"{path}: expected a .zip or a folder of episode .txt files")
    return show_from_texts(files, title or inferred)


def show_from_texts(files: list[tuple[str, str]], title: str) -> Show:
    if not files:
        raise ParseError("no episode .txt files found")
    episodes = [parse_episode_text(text, name) for name, text in files]
    episodes.sort(key=lambda e: e.number)
    seen = set()
    for e in episodes:
        if e.number in seen:
            raise ParseError(f"duplicate episode number {e.number}")
        seen.add(e.number)
    return Show(slug=_slugify(title), title=title, episodes=episodes)


def show_from_uploads(uploads: list[tuple[str, bytes]], title: str | None = None) -> Show:
    """For the app: a list of (filename, bytes), either one .zip or several .txt."""
    if len(uploads) == 1 and uploads[0][0].lower().endswith(".zip"):
        name, data = uploads[0]
        files = []
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            tops = set()
            for info in zf.infolist():
                if info.is_dir() or not info.filename.lower().endswith(".txt") or Path(info.filename).name.startswith("._"):
                    continue
                parts = Path(info.filename).parts
                if len(parts) > 1:
                    tops.add(parts[0])
                files.append((Path(info.filename).name, zf.read(info).decode("utf-8-sig", errors="replace")))
        inferred = tops.pop() if len(tops) == 1 else _clean_title(name)
        return show_from_texts(files, title or inferred)
    files = [(n, b.decode("utf-8-sig", errors="replace")) for n, b in uploads if n.lower().endswith(".txt")]
    return show_from_texts(files, title or "uploaded-show")
