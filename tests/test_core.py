import json
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import subs
import translate as tr


# ---------------- transliteracija ----------------
@pytest.mark.parametrize(
    "lat,cyr",
    [
        ("Ljubav", "Љубав"),
        ("NJEGOŠ", "ЊЕГОШ"),
        ("Njegoš i konj", "Његош и коњ"),
        ("džep, Džabe, DŽEM", "џеп, Џабе, ЏЕМ"),
        ("injekcija", "инјекција"),
        ("konjugacija i konjunkcija", "конјугација и конјункција"),
        ("nadžupan", "наджупан"),
        ("đak, ćevap, čaša, šuma, žaba", "ђак, ћевап, чаша, шума, жаба"),
        ("Hello w x q 123!", "Хелло w x q 123!"),
    ],
)
def test_to_cyrillic(lat, cyr):
    assert subs.to_cyrillic(lat) == cyr


def test_cyrillic_keeps_tags():
    assert subs.to_cyrillic("<i>Dobro jutro</i>") == "<i>Добро јутро</i>"
    assert subs.to_cyrillic("{\\an8}Šta?\\NNe znam") == "{\\an8}Шта?\\NНе знам"


def test_to_latin_roundtrip():
    s = "Ljubav, njiva, džem, đak, žaba"
    assert subs.to_latin(subs.to_cyrillic(s)) == s
    assert subs.to_latin("<i>Љубав</i>") == "<i>Ljubav</i>"


# ---------------- SRT ----------------
SRT = """﻿1
00:00:01,000 --> 00:00:02,500
Hello there.

2
00:00:03,000 --> 00:00:05,000
<i>How are you?</i>
I'm fine.

3
00:00:06,000 --> 00:00:07,000
♪ la la ♪
"""


def test_srt_roundtrip():
    sf = subs.parse_srt(SRT)
    assert [c.text for c in sf.cues] == ["Hello there.", "<i>How are you?</i>\nI'm fine.", "♪ la la ♪"]
    sf.cues[0].translated = "Zdravo."
    sf.cues[1].translated = "<i>Kako si?</i>\nDobro sam."
    out = sf.render("lat")
    again = subs.parse_srt(out)
    assert again.cues[0].timing == "00:00:01,000 --> 00:00:02,500"
    assert again.cues[1].text == "<i>Kako si?</i>\nDobro sam."
    assert again.cues[2].text == "♪ la la ♪"  # neprevedeno ostaje original
    cyr = sf.render("cyr")
    assert "<i>Како си?</i>" in cyr and "Здраво." in cyr


def test_model_cyrillic_output_becomes_latin():
    sf = subs.parse_srt(SRT)
    sf.cues[0].translated = "Здраво."
    assert "Zdravo." in sf.render("lat")


# ---------------- ASS ----------------
ASS = """[Script Info]
Title: t

[V4+ Styles]
Format: Name, Fontname
Style: Default,Arial

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,{\\an8}Hello, world\\Nsecond {\\i1}line{\\i0}
Dialogue: 0,0:00:03.00,0:00:04.00,Default,,0,0,0,,{\\p1}m 0 0 l 10 10{\\p0}
Comment: 0,0:00:05.00,0:00:06.00,Default,,0,0,0,,not me
Dialogue: 0,0:00:07.00,0:00:08.00,Default,,0,0,0,,Plain, with comma
"""


def test_ass_roundtrip():
    sf = subs.parse_ass(ASS)
    assert [c.text for c in sf.cues] == ["Hello, world\nsecond line", "Plain, with comma"]
    assert sf.cues[0].lead == "{\\an8}"
    sf.cues[0].translated = "Zdravo, svete\ndrugi red"
    sf.cues[1].translated = "Obično, sa zarezom"
    out = sf.render("lat")
    assert "{\\an8}Zdravo, svete\\Ndrugi red" in out
    assert "{\\p1}m 0 0 l 10 10{\\p0}" in out  # crtež netaknut
    assert "Comment: 0,0:00:05.00,0:00:06.00,Default,,0,0,0,,not me" in out
    assert "Dialogue: 0,0:00:07.00,0:00:08.00,Default,,0,0,0,,Obično, sa zarezom" in out
    assert sf.render("cyr").count("Здраво, свете\\Nдруги ред") == 1


# ---------------- prevodilac ----------------
class Fake(tr.BaseProvider):
    def __init__(self, name="Fake", fail=None, wrong_len=False):
        super().__init__(rpm=100000)
        self.name, self.fail, self.wrong_len, self.calls = name, fail, wrong_len, 0

    def translate_batch(self, lines, context, src_name):
        self.calls += 1
        if self.fail:
            raise self.fail
        out = [f"PR:{l}" for l in lines]
        if self.wrong_len and len(lines) > 3:
            out = out[:-1]  # model "pojede" jedan red
        return out


def test_batching_and_empty_lines():
    p = Fake()
    t = tr.Translator([p], batch_size=3, log=lambda s: None)
    out = t.translate_lines(["a", "", "b", "c", "d", "e", "f"])
    assert out == ["PR:a", "", "PR:b", "PR:c", "PR:d", "PR:e", "PR:f"]
    assert p.calls == 2  # 6 neprazna reda / 3


def test_length_mismatch_splits():
    p = Fake(wrong_len=True)
    t = tr.Translator([p], batch_size=8, log=lambda s: None)
    lines = [f"l{i}" for i in range(8)]
    assert t.translate_lines(lines) == [f"PR:{l}" for l in lines]


def test_fallback_on_quota_and_error():
    a = Fake("A", fail=tr.QuotaExceeded("dnevni"))
    b = Fake("B")
    t = tr.Translator([a, b], batch_size=2, log=lambda s: None)
    out = t.translate_lines(["x", "y", "z"])
    assert out == ["PR:x", "PR:y", "PR:z"]
    assert a.calls == 1  # posle kvote više se ne zove
    assert [p.name for p in t.active] == ["B"]

    c = Fake("C", fail=tr.ProviderError("pao"))
    d = Fake("D")
    t = tr.Translator([c, d], batch_size=2, log=lambda s: None)
    t.translate_lines(["x", "y", "z"])
    assert c.calls == 2 and [p.name for p in t.active] == ["C", "D"]  # obična greška ne isključuje


def test_all_fail_raises():
    t = tr.Translator([Fake(fail=tr.ProviderError("e"))], log=lambda s: None)
    with pytest.raises(tr.ProviderError):
        t.translate_lines(["x"])


def test_cache_resume():
    cache = {}
    texts = [f"t{i}" for i in range(7)]
    p1 = Fake()
    t1 = tr.Translator([p1], batch_size=3, log=lambda s: None)
    t1.translate_lines(texts, cache=cache)
    # drugi pokušaj: sve iz keša, nijedan poziv
    p2 = Fake(fail=tr.ProviderError("ne sme"))
    t2 = tr.Translator([p2], batch_size=3, log=lambda s: None)
    assert t2.translate_lines(texts, cache=cache) == [f"PR:{x}" for x in texts]
    assert p2.calls == 0
    # promenjen sadržaj poništava keš -> traži novi prevod (p2 baca grešku)
    with pytest.raises(tr.ProviderError):
        t2.translate_lines(texts[:-1] + ["novo"], cache=cache)


def test_cancel():
    ev = threading.Event()
    ev.set()
    t = tr.Translator([Fake()], cancel=ev, log=lambda s: None)
    with pytest.raises(tr.Cancelled):
        t.translate_lines(["x"])


# ---------------- parsiranje odgovora ----------------
def test_parse_json_array():
    assert tr.parse_json_array('["a","b"]') == ["a", "b"]
    assert tr.parse_json_array('```json\n["a"]\n```') == ["a"]
    assert tr.parse_json_array('Evo:\n["a", "b"]\nGotovo') == ["a", "b"]
    assert tr.parse_json_array('<think>hm</think>["a"]') == ["a"]
    assert tr.parse_json_array('{"translations": ["a", "b"]}') == ["a", "b"]
    with pytest.raises(tr.ProviderError):
        tr.parse_json_array("nije json")


def test_retry_delay():
    assert tr._retry_delay('{"retryDelay": "23s"}') == 23
    assert tr._retry_delay("Please retry in 7.5s.") == 7.5
    assert tr._retry_delay("nista") is None


def test_prompt_contains_rules():
    s = tr.build_system("English")
    assert "EKAVIAN" in s and "English" in s and "{...}" in s
    u = tr.build_user(["a", "b"], ["c"])
    assert "exactly 2" in u
