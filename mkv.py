"""Omotač oko MKVToolNix alata (mkvmerge, mkvextract)."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

_NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0

TEXT_CODECS = {"S_TEXT/UTF8": "srt", "S_TEXT/ASS": "ass", "S_TEXT/SSA": "ass"}
_BAD_NAME = re.compile(r"forced|sdh|hearing|commentary|signs|songs|nagluv|\bcc\b", re.I)


class MkvError(Exception):
    pass


@dataclass
class Tools:
    mkvmerge: str
    mkvextract: str


@dataclass
class Track:
    id: int
    codec_id: str
    language: str
    ietf: str
    name: str
    forced: bool
    default: bool

    @property
    def kind(self) -> str | None:
        return TEXT_CODECS.get(self.codec_id)

    @property
    def is_text(self) -> bool:
        return self.kind is not None

    def label(self) -> str:
        flags = []
        if self.default:
            flags.append("default")
        if self.forced:
            flags.append("forced")
        kind = self.kind.upper() if self.kind else f"slikovni/nepodržan ({self.codec_id})"
        extra = f" [{', '.join(flags)}]" if flags else ""
        nm = f' "{self.name}"' if self.name else ""
        return f"#{self.id}  {self.language or 'und'}  {kind}{nm}{extra}"


def find_tools(configured_dir: str = "") -> Tools:
    exe = ".exe" if os.name == "nt" else ""
    cands: list[Path] = []
    if configured_dir:
        cands.append(Path(configured_dir))
    for env in ("ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(env)
        if base:
            cands.append(Path(base) / "MKVToolNix")
    found: dict[str, str] = {}
    for tool in ("mkvmerge", "mkvextract"):
        path = None
        for d in cands:
            p = d / (tool + exe)
            if p.is_file():
                path = str(p)
                break
        path = path or shutil.which(tool)
        if not path:
            raise MkvError(
                f"Nije pronađen {tool}. Instaliraj MKVToolNix (mkvtoolnix.download) "
                "ili u podešavanjima izaberi njegov folder."
            )
        found[tool] = path
    return Tools(found["mkvmerge"], found["mkvextract"])


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, creationflags=_NOWIN)


def probe(tools: Tools, path: str | Path) -> list[Track]:
    r = _run([tools.mkvmerge, "--output-charset", "UTF-8", "-J", str(path)])
    out = r.stdout.decode("utf-8", "replace")
    if r.returncode == 2 or not out.strip():
        raise MkvError(f"mkvmerge ne može da pročita fajl: {out.strip()[:200] or r.stderr.decode('utf-8','replace')[:200]}")
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        raise MkvError("Neočekivan odgovor mkvmerge-a pri čitanju traka.")
    tracks: list[Track] = []
    for t in data.get("tracks", []):
        if t.get("type") != "subtitles":
            continue
        p = t.get("properties", {})
        tracks.append(
            Track(
                id=t["id"],
                codec_id=p.get("codec_id", ""),
                language=p.get("language", "und"),
                ietf=p.get("language_ietf", ""),
                name=p.get("track_name", ""),
                forced=bool(p.get("forced_track", False)),
                default=bool(p.get("default_track", False)),
            )
        )
    return tracks


def choose_track(tracks: list[Track], lang_codes: set[str]) -> Track | None:
    """Automatski izbor: tekstualni titl na traženom jeziku, bez forced/SDH/signs."""
    text = [t for t in tracks if t.is_text]
    if not text:
        return None

    def score(t: Track) -> int:
        s = 0
        if t.language.lower() in lang_codes or t.ietf.lower().split("-")[0] in lang_codes:
            s += 100
        if not t.forced:
            s += 20
        if not _BAD_NAME.search(t.name or ""):
            s += 10
        if t.default:
            s += 1
        return s

    return max(text, key=score)


def extract(tools: Tools, src: str | Path, track_id: int, dst: str | Path) -> None:
    r = _run([tools.mkvextract, str(src), "tracks", f"{track_id}:{dst}"])
    if r.returncode == 2 or not Path(dst).is_file():
        raise MkvError("mkvextract nije uspeo: " + r.stdout.decode("utf-8", "replace")[-300:])


@dataclass
class NewSub:
    path: str
    name: str
    default: bool = False
    language: str = "srp"


def remux(
    tools: Tools,
    src: str | Path,
    dst: str | Path,
    subs: list[NewSub],
    clear_default_ids: list[int] | None = None,
    progress: Callable[[float], None] | None = None,
    cancel: threading.Event | None = None,
) -> None:
    """Pravi novi MKV (bez ponovnog kodiranja) sa dodatim titlovima. Original ostaje netaknut."""
    cmd = [tools.mkvmerge, "--gui-mode", "-o", str(dst)]
    for tid in clear_default_ids or []:
        cmd += ["--default-track-flag", f"{tid}:0"]
    cmd.append(str(src))
    for s in subs:
        cmd += [
            "--language", f"0:{s.language}",
            "--track-name", f"0:{s.name}",
            "--default-track-flag", f"0:{1 if s.default else 0}",
            "--sub-charset", "0:UTF-8",
            s.path,
        ]
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=_NOWIN
    )
    tail: list[str] = []
    assert proc.stdout is not None
    for raw in proc.stdout:
        if cancel is not None and cancel.is_set():
            proc.kill()
            proc.wait()
            Path(dst).unlink(missing_ok=True)
            raise MkvError("Prekinuto.")
        line = raw.decode("utf-8", "replace").strip()
        m = re.match(r"#GUI#progress\s+(\d+)%", line)
        if m and progress:
            progress(int(m.group(1)) / 100.0)
        elif line:
            tail.append(line)
            tail = tail[-8:]
    code = proc.wait()
    if code == 2:
        Path(dst).unlink(missing_ok=True)
        raise MkvError("mkvmerge nije uspeo: " + " | ".join(tail))
