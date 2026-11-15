import base64
import hashlib
import struct
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

import opensubs as os_


def _ref_hash(data: bytes) -> str:
    h = len(data)
    for part in (data[:65536], data[-65536:]):
        for i in range(0, 65536, 8):
            h += struct.unpack("<Q", part[i:i + 8])[0]
    return f"{h & 0xFFFFFFFFFFFFFFFF:016x}"


def test_movie_hash_zeros_is_size(tmp_path):
    f = tmp_path / "a.mkv"
    f.write_bytes(bytes(200_000))
    assert os_.movie_hash(f) == (f"{200_000:016x}", 200_000)


def test_movie_hash_matches_reference_and_overflows(tmp_path):
    data = bytes([0xFF]) * 70_000 + bytes(range(256)) * 400
    f = tmp_path / "b.mkv"
    f.write_bytes(data)
    assert os_.movie_hash(f)[0] == _ref_hash(data)


def test_movie_hash_too_small(tmp_path):
    f = tmp_path / "c.mkv"
    f.write_bytes(b"x" * 1000)
    with pytest.raises(ValueError):
        os_.movie_hash(f)


@pytest.mark.parametrize("text,exp", [
    ("tt0133093", "133093"),
    ("https://www.imdb.com/title/tt0133093/?ref_=x", "133093"),
    (" 0133093 ", "133093"),
    ("tt15239678", "15239678"),
])
def test_parse_imdb(text, exp):
    assert os_.parse_imdb(text) == exp


def test_parse_imdb_bad():
    with pytest.raises(ValueError):
        os_.parse_imdb("Matrix")


def test_baseinfo_always_machine_translated():
    b = os_.baseinfo("133093", "Film.2020.1080p", "kom", False, True)
    assert b["automatictranslation"] == "1"
    assert b["sublanguageid"] == "scc"
    assert b["highdefinition"] == "1" and b["hearingimpaired"] == "0"


def test_sub_cd1_content(tmp_path):
    sub = tmp_path / "Film.sr.srt"
    raw = "1\n00:00:01,000 --> 00:00:02,000\nZdravo, šta radiš?\n".encode("utf-8-sig")
    sub.write_bytes(raw)
    cd = os_.sub_cd1(sub, Path("Film.mkv"), "abc", 123, True)
    assert cd["subhash"] == hashlib.md5(raw).hexdigest()
    assert cd["moviebytesize"] == "123" and cd["moviefilename"] == "Film.mkv"
    assert zlib.decompress(base64.b64decode(cd["subcontent"])) == raw
    assert "subcontent" not in os_.sub_cd1(sub, Path("Film.mkv"), "abc", 123, False)


def test_jobs_roundtrip(tmp_path):
    movie = tmp_path / "Film.mkv"
    movie.write_bytes(bytes(150_000))
    lat = tmp_path / "Film.sr.srt"
    lat.write_text("lat", "utf-8")
    cfg = tmp_path / "cfg"
    job = os_.create_job(cfg, movie, {"lat": lat})
    lat.unlink()  # posao ima svoju kopiju
    jobs = os_.load_jobs(cfg)
    assert len(jobs) == 1 and jobs[0].movie_hash == job.movie_hash
    assert jobs[0].sub_path("lat").read_text("utf-8") == "lat"
    jobs[0].uploaded["lat"] = "http://x"
    jobs[0].save()
    assert os_.load_jobs(cfg)[0].uploaded == {"lat": "http://x"}


class FakeProxy:
    def __init__(self, **responses):
        self.r = responses
        self.calls = []

    def __getattr__(self, name):
        def call(*args):
            self.calls.append((name, args))
            return self.r[name]
        return call


def test_login_errors():
    c = os_.Client("UA", proxy=FakeProxy(LogIn={"status": "401 Unauthorized"}))
    with pytest.raises(os_.OpenSubsError, match="lozinka"):
        c.login("u", "p")
    with pytest.raises(os_.OpenSubsError, match="nalog"):
        c.login("", "")
    c = os_.Client("UA", proxy=FakeProxy(LogIn={"status": "414 Unknown User Agent"}))
    with pytest.raises(os_.OpenSubsError, match="User Agent"):
        c.login("u", "p")


def test_try_upload_search_and_upload():
    p = FakeProxy(
        LogIn={"status": "200 OK", "token": "T"},
        TryUploadSubtitles={"status": "200 OK", "alreadyindb": 0,
                            "data": [{"IDMovieImdb": "133093", "MovieName": "The Matrix", "MovieYear": "1999"}]},
        SearchMoviesOnIMDB={"status": "200 OK", "data": [{"id": "0133093", "title": "The Matrix (1999)"}]},
        UploadSubtitles={"status": "200 OK", "data": "http://www.opensubtitles.org/subtitles/1"},
    )
    c = os_.Client("UA", proxy=p)
    c.login("u", "p")
    assert p.calls[0] == ("LogIn", ("u", "p", "en", "UA"))
    r = c.try_upload({"subhash": "x"})
    assert not r.already_in_db and r.movies[0].label() == "The Matrix (1999)  [tt0133093]"
    m = c.search_imdb("matrix")[0]
    assert (m.imdb, m.name, m.year) == ("133093", "The Matrix", "1999")
    assert c.upload({"a": 1}, {"b": 2}) == "http://www.opensubtitles.org/subtitles/1"
    assert p.calls[-1] == ("UploadSubtitles", ("T", {"baseinfo": {"a": 1}, "cd1": {"b": 2}}))


def test_try_upload_already_in_db_without_movie():
    p = FakeProxy(TryUploadSubtitles={"status": "200 OK", "alreadyindb": 1, "data": False})
    r = os_.Client("UA", proxy=p).try_upload({})
    assert r.already_in_db and r.movies == []
