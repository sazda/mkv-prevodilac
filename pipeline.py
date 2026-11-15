"""Tok obrade jednog MKV fajla: izvuci titl -> prevedi -> (opciono) ugradi u novi MKV."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import mkv
import subs
from translate import Cancelled, ProviderError, Translator

APP_NAME = "MKV Prevodilac"


def config_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    d = Path(base) / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


@dataclass
class Options:
    mkvtoolnix_dir: str = ""
    src_name: str = "English"
    src_codes: tuple = ("eng", "en")
    track_id: int | None = None  # None = automatski izbor
    latin: bool = True
    cyrillic: bool = True
    make_default: bool = True
    save_external: bool = False
    make_mkv: bool = True
    out_dir: str = ""
    batch_size: int = 60


def _unique(path: Path) -> Path:
    if not path.exists():
        return path
    i = 2
    while True:
        p = path.with_name(f"{path.stem} ({i}){path.suffix}")
        if not p.exists():
            return p
        i += 1


def process_file(
    path: str | Path,
    opts: Options,
    translator: Translator,
    log: Callable[[str], None],
    progress: Callable[[float, str], None],
    cancel: threading.Event,
) -> list[Path]:
    """Vraća listu napravljenih fajlova."""
    src = Path(path)
    if not (opts.latin or opts.cyrillic):
        raise ValueError("Izaberi bar jedno pismo (latinica ili ćirilica).")
    tools = mkv.find_tools(opts.mkvtoolnix_dir)
    tracks = mkv.probe(tools, src)
    text_tracks = [t for t in tracks if t.is_text]
    if not text_tracks:
        if tracks:
            raise mkv.MkvError("Titlovi su slikoviti (PGS/VobSub) - potreban je OCR, što još nije podržano.")
        raise mkv.MkvError("Fajl nema ugrađenih titlova.")

    if opts.track_id is not None:
        track = next((t for t in tracks if t.id == opts.track_id), None)
        if track is None or not track.is_text:
            raise mkv.MkvError("Izabrana traka ne postoji u ovom fajlu ili nije tekstualna.")
    else:
        track = mkv.choose_track(tracks, {c.lower() for c in opts.src_codes})
        assert track is not None
        if track.language.lower() not in {c.lower() for c in opts.src_codes}:
            log(f"  Upozorenje: nema titla na izabranom jeziku, uzimam: {track.label()}")
    log(f"  Izvorni titl: {track.label()}")

    out_dir = Path(opts.out_dir) if opts.out_dir else src.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    if opts.make_mkv:
        free = shutil.disk_usage(out_dir).free
        need = int(src.stat().st_size * 1.05)
        if free < need:
            raise mkv.MkvError(
                f"Nema dovoljno mesta na disku: treba oko {need // 2**20} MB, slobodno {free // 2**20} MB."
            )

    tmp = Path(tempfile.mkdtemp(prefix="mkvprev_"))
    try:
        # 1) ekstrakcija
        progress(0.02, "Izvlačim titl…")
        raw = tmp / f"src.{track.kind}"
        mkv.extract(tools, src, track.id, raw)
        sf = subs.parse(raw.read_text("utf-8-sig", errors="replace"), track.kind)
        if not sf.cues:
            raise mkv.MkvError("Titl je prazan ili ga nisam uspeo da pročitam.")
        log(f"  Redova za prevod: {len(sf.cues)}")

        # 2) prevod (sa kešom da se prekinut posao nastavlja)
        key = hashlib.sha1(f"{src.resolve()}|{src.stat().st_size}|{src.stat().st_mtime_ns}|{track.id}".encode()).hexdigest()
        cdir = config_dir() / "cache"
        cdir.mkdir(exist_ok=True)
        cpath = cdir / f"{key}.json"
        cache: dict = {}
        if cpath.is_file():
            try:
                cache = json.loads(cpath.read_text("utf-8"))
            except Exception:
                cache = {}

        def save_cache(c: dict) -> None:
            cpath.write_text(json.dumps(c, ensure_ascii=False), "utf-8")

        translator.src_name = opts.src_name
        translator.batch_size = opts.batch_size
        out = translator.translate_lines(
            [c.text for c in sf.cues],
            progress=lambda f: progress(0.05 + 0.75 * f, f"Prevodim… {int(f * 100)}%"),
            cache=cache,
            save_cache=save_cache,
        )
        for c, t in zip(sf.cues, out):
            c.translated = t
        cpath.unlink(missing_ok=True)

        # 3) fajlovi titlova
        scripts = []
        if opts.latin:
            scripts.append(("lat", "Srpski (latinica, AI)", f"{src.stem}.sr"))
        if opts.cyrillic:
            scripts.append(("cyr", "Srpski (ćirilica, AI)", f"{src.stem}.sr-cyrl"))
        new_subs: list[mkv.NewSub] = []
        created: list[Path] = []
        for i, (sc, name, base) in enumerate(scripts):
            f = tmp / f"sr_{sc}.{sf.ext}"
            f.write_text(sf.render(sc), "utf-8-sig")
            new_subs.append(mkv.NewSub(str(f), name, default=(opts.make_default and i == 0)))
            if opts.save_external:
                ext_path = _unique(out_dir / f"{base}.{sf.ext}")
                shutil.copyfile(f, ext_path)
                created.append(ext_path)
                log(f"  Sačuvan titl: {ext_path.name}")

        # 4) remux
        if opts.make_mkv:
            dst = _unique(out_dir / f"{src.stem}.sr.mkv")
            progress(0.82, "Ugrađujem titl u MKV…")
            mkv.remux(
                tools, src, dst, new_subs,
                clear_default_ids=[t.id for t in tracks if t.default] if opts.make_default else None,
                progress=lambda f: progress(0.82 + 0.18 * f, f"Ugrađujem titl… {int(f * 100)}%"),
                cancel=cancel,
            )
            created.append(dst)
            log(f"  Gotov fajl: {dst.name}")
        progress(1.0, "Gotovo")
        return created
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
