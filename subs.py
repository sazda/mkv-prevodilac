"""Parsiranje i pisanje titlova (SRT, ASS/SSA) + transliteracija latinica <-> ćirilica.

Bez spoljnih zavisnosti.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# HTML tagovi (<i>), ASS override blokovi ({\an8}) i ASS prelomi (\N, \n, \h)
TAG_RE = re.compile(r"(<[^>]*>|\{[^}]*\}|\\[Nnh])")
LETTER_RE = re.compile(r"[^\W\d_]")


# --------------------------------------------------------------------------
# Transliteracija
# --------------------------------------------------------------------------
_SEP = ""  # privremeni separator koji prekida digraf (nj, lj, dž)

_EXC_PATTERNS = [
    # injekcija, injunkcija
    (re.compile(r"(?i)(?<![a-zčćžšđ])(in)(?=j(?:ek|unk))"), r"\1" + _SEP),
    # konjugacija, konjunkcija
    (re.compile(r"(?i)(?<![a-zčćžšđ])(kon)(?=j(?:ug|unk))"), r"\1" + _SEP),
    # nadžupan, podžupan (d + ž, a ne dž)
    (re.compile(r"(?i)(?<![a-zčćžšđ])((?:na|po)d)(?=ž)"), r"\1" + _SEP),
]

_LAT2CYR = {
    "a": "а", "b": "б", "v": "в", "g": "г", "d": "д", "đ": "ђ", "e": "е",
    "ž": "ж", "z": "з", "i": "и", "j": "ј", "k": "к", "l": "л", "m": "м",
    "n": "н", "o": "о", "p": "п", "r": "р", "s": "с", "t": "т", "ć": "ћ",
    "u": "у", "f": "ф", "h": "х", "c": "ц", "č": "ч", "š": "ш",
}
_DIGRAPHS = {"lj": "љ", "nj": "њ", "dž": "џ"}
_CYR2LAT = {v: k for k, v in _LAT2CYR.items()}
_CYR2LAT.update({"љ": "lj", "њ": "nj", "џ": "dž"})


def _cyr_plain(s: str) -> str:
    out: list[str] = []
    i, n = 0, len(s)
    while i < n:
        ch = s[i]
        if i + 1 < n:
            pair = s[i : i + 2].lower()
            if pair in _DIGRAPHS:
                c = _DIGRAPHS[pair]
                out.append(c.upper() if ch.isupper() else c)
                i += 2
                continue
        low = ch.lower()
        if low in _LAT2CYR:
            c = _LAT2CYR[low]
            out.append(c.upper() if ch.isupper() else c)
        elif ch == _SEP:
            pass
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def to_cyrillic(text: str) -> str:
    """Latinica -> ćirilica; tagovi (<i>, {\\an8}, \\N) ostaju nepromenjeni."""
    for pat, repl in _EXC_PATTERNS:
        text = pat.sub(repl, text)
    parts = TAG_RE.split(text)
    # neparni indeksi su tagovi
    return "".join(p if k % 2 else _cyr_plain(p) for k, p in enumerate(parts))


def to_latin(text: str) -> str:
    """Ćirilica -> latinica (zaštita ako model slučajno vrati ćirilicu)."""
    parts = TAG_RE.split(text)
    res = []
    for k, p in enumerate(parts):
        if k % 2:
            res.append(p)
            continue
        o = []
        for ch in p:
            low = ch.lower()
            if low in _CYR2LAT:
                lat = _CYR2LAT[low]
                if ch.isupper():
                    lat = lat[0].upper() + lat[1:]
                o.append(lat)
            else:
                o.append(ch)
        res.append("".join(o))
    return "".join(res)


_CYR_RE = re.compile(r"[Ѐ-ӿ]")


def has_cyrillic(s: str) -> bool:
    return bool(_CYR_RE.search(s))


# --------------------------------------------------------------------------
# Model
# --------------------------------------------------------------------------
@dataclass
class Cue:
    text: str                      # tekst za prevod ('\n' između redova)
    translated: str | None = None
    timing: str = ""               # SRT: red sa vremenom
    line_no: int = -1              # ASS: indeks reda u fajlu
    prefix: str = ""               # ASS: 'Dialogue: ...,' do teksta
    lead: str = ""                 # ASS: početni override tagovi ({\an8}...)


@dataclass
class SubFile:
    kind: str                      # 'srt' | 'ass'
    cues: list[Cue] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)  # ASS: svi redovi fajla

    @property
    def ext(self) -> str:
        return "srt" if self.kind == "srt" else "ass"

    def render(self, script: str = "lat") -> str:
        """Vraća sadržaj fajla sa prevodom. script: 'lat' ili 'cyr'."""

        def final(c: Cue) -> str:
            t = c.translated if c.translated is not None else c.text
            if script == "cyr":
                return to_cyrillic(to_latin(t))
            return to_latin(t) if has_cyrillic(t) else t

        if self.kind == "srt":
            out = []
            for i, c in enumerate(self.cues, 1):
                out.append(f"{i}\n{c.timing}\n{final(c)}\n")
            return "\n".join(out)

        lines = list(self.lines)
        for c in self.cues:
            body = final(c).replace("\r", "").replace("\n", "\\N")
            lines[c.line_no] = c.prefix + c.lead + body
        return "\n".join(lines)


# --------------------------------------------------------------------------
# Parseri
# --------------------------------------------------------------------------
def _norm(data: str) -> str:
    return data.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")


def parse_srt(data: str) -> SubFile:
    data = _norm(data).strip("\n")
    cues: list[Cue] = []
    for block in re.split(r"\n{2,}", data):
        lines = block.split("\n")
        ti = next((i for i, l in enumerate(lines) if "-->" in l), None)
        if ti is None:
            continue
        cues.append(Cue(text="\n".join(lines[ti + 1 :]).strip("\n"), timing=lines[ti].strip()))
    return SubFile("srt", cues)


_DRAWING_RE = re.compile(r"\\p[1-9]")


def parse_ass(data: str) -> SubFile:
    lines = _norm(data).split("\n")
    section = ""
    fmt: list[str] | None = None
    cues: list[Cue] = []
    for i, raw in enumerate(lines):
        s = raw.strip()
        if s.startswith("[") and s.endswith("]"):
            section = s.lower()
            continue
        if section != "[events]":
            continue
        low = s.lower()
        if low.startswith("format:"):
            fmt = [f.strip().lower() for f in s.split(":", 1)[1].split(",")]
        elif low.startswith("dialogue:") and fmt:
            n = len(fmt)
            if fmt[-1] != "text":
                continue  # nepodržan raspored kolona
            rest = raw.split(":", 1)[1].lstrip()
            parts = rest.split(",", n - 1)
            if len(parts) < n:
                continue
            text = parts[-1]
            if _DRAWING_RE.search(text):
                continue
            m = re.match(r"^(?:\{[^}]*\})+", text)
            lead = m.group(0) if m else ""
            body = text[len(lead):]
            body = re.sub(r"\{[^}]*\}", "", body)
            body = re.sub(r"\\[Nn]", "\n", body).replace("\\h", " ").strip()
            if not LETTER_RE.search(body):
                continue
            prefix = raw[: len(raw) - len(text)]
            cues.append(Cue(text=body, line_no=i, prefix=prefix, lead=lead))
    return SubFile("ass", cues, lines)


def parse(data: str, kind: str) -> SubFile:
    return parse_srt(data) if kind == "srt" else parse_ass(data)
