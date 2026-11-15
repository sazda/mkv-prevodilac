"""Otpremanje prevedenih titlova na OpenSubtitles.org (XML-RPC API).

Tok: LogIn -> TryUploadSubtitles (provera + prepoznavanje filma po hešu) -> UploadSubtitles -> LogOut.
Titl se UVEK šalje sa oznakom "mašinski prevedeno" (automatictranslation=1).
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import ssl
import struct
import xmlrpc.client
import zlib
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

API_URL = "https://api.opensubtitles.org/xml-rpc"
DEFAULT_USER_AGENT = "MKVPrevodilac v1.0"
LANG_SERBIAN = "scc"  # SubLanguageID za srpski na opensubtitles.org (GetSubLanguages)
TRANSLATOR = "MKV Prevodilac (AI)"
DEFAULT_COMMENT = "Mašinski prevod (AI), ekavica."


class OpenSubsError(Exception):
    """Greška OpenSubtitles servisa (status nije 200)."""


# ---------------- heš filma ----------------
_CHUNK = 64 * 1024


def movie_hash(path: str | Path) -> tuple[str, int]:
    """OpenSubtitles heš: veličina + zbir 64-bitnih reči prvih i poslednjih 64 KB. Vraća (heš, veličina)."""
    p = Path(path)
    size = p.stat().st_size
    if size < _CHUNK * 2:
        raise ValueError("Fajl je premali za OpenSubtitles heš.")
    h = size
    with p.open("rb") as f:
        for offset in (0, size - _CHUNK):
            f.seek(offset)
            buf = f.read(_CHUNK)
            for (v,) in struct.iter_unpack("<Q", buf):
                h = (h + v) & 0xFFFFFFFFFFFFFFFF
    return f"{h:016x}", size


def parse_imdb(text: str) -> str:
    """'tt0133093', 'https://www.imdb.com/title/tt0133093/' ili '133093' -> '133093' (bez 'tt' i nula)."""
    m = re.search(r"(?:tt)?(\d{5,9})", text.strip())
    if not m:
        raise ValueError("Neispravan IMDb broj (primer: tt0133093).")
    return str(int(m.group(1)))


# ---------------- klijent ----------------
def _ssl_context() -> ssl.SSLContext:
    # Windows skladište sertifikata ponekad nema novi Let's Encrypt lanac koji .org koristi.
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


@dataclass
class Movie:
    imdb: str  # bez "tt"
    name: str = ""
    year: str = ""

    def label(self) -> str:
        y = f" ({self.year})" if self.year else ""
        return f"{self.name or '?'}{y}  [tt{int(self.imdb):07d}]"


@dataclass
class TryResult:
    already_in_db: bool
    movies: list[Movie]


class Client:
    def __init__(self, user_agent: str = DEFAULT_USER_AGENT, url: str = API_URL, proxy=None):
        self.user_agent = user_agent.strip() or DEFAULT_USER_AGENT
        self.proxy = proxy or xmlrpc.client.ServerProxy(url, context=_ssl_context(), allow_none=True)
        self.token: str | None = None

    @staticmethod
    def _check(r) -> dict:
        if not isinstance(r, dict):
            raise OpenSubsError("Neočekivan odgovor servera.")
        status = str(r.get("status", ""))
        if not status.startswith("200"):
            hint = {
                "401": " (pogrešno korisničko ime ili lozinka)",
                "414": " (User Agent nije registrovan kod OpenSubtitles-a)",
                "415": " (User Agent nije registrovan kod OpenSubtitles-a)",
                "402": " (titl je neispravnog formata)",
                "403": " (titl je neispravnog formata)",
                "407": " (dnevni limit je potrošen)",
            }.get(status[:3], "")
            raise OpenSubsError(f"OpenSubtitles: {status or 'nema statusa'}{hint}")
        return r

    def _call(self, method: str, *args):
        try:
            return self._check(getattr(self.proxy, method)(*args))
        except OpenSubsError:
            raise
        except xmlrpc.client.Fault as e:
            raise OpenSubsError(f"OpenSubtitles greška: {e.faultString}") from e
        except Exception as e:  # noqa: BLE001 - mreža, SSL...
            raise OpenSubsError(f"Ne mogu da se povežem sa OpenSubtitles.org: {e}") from e

    def login(self, username: str, password: str) -> None:
        if not username or not password:
            raise OpenSubsError("Za otpremanje je potreban nalog na opensubtitles.org (korisničko ime i lozinka).")
        r = self._call("LogIn", username, password, "en", self.user_agent)
        self.token = r.get("token")
        if not self.token:
            raise OpenSubsError("Prijava nije uspela (server nije vratio token).")

    def logout(self) -> None:
        if self.token:
            try:
                self.proxy.LogOut(self.token)
            except Exception:  # noqa: BLE001
                pass
            self.token = None

    def try_upload(self, cd1: dict) -> TryResult:
        r = self._call("TryUploadSubtitles", self.token, {"cd1": cd1})
        movies = []
        data = r.get("data")
        if isinstance(data, list):
            for d in data:
                if isinstance(d, dict) and d.get("IDMovieImdb"):
                    movies.append(Movie(str(d["IDMovieImdb"]), d.get("MovieName", ""), str(d.get("MovieYear", ""))))
        return TryResult(bool(int(r.get("alreadyindb", 0) or 0)), movies)

    def search_imdb(self, query: str) -> list[Movie]:
        r = self._call("SearchMoviesOnIMDB", self.token, query)
        out = []
        for d in r.get("data") or []:
            if isinstance(d, dict) and d.get("id"):
                title = str(d.get("title", ""))
                m = re.match(r"(.*?)\s*\((\d{4})\)\s*$", title)
                out.append(Movie(str(int(d["id"])), m.group(1) if m else title, m.group(2) if m else ""))
        return out

    def upload(self, baseinfo: dict, cd1: dict) -> str:
        r = self._call("UploadSubtitles", self.token, {"baseinfo": baseinfo, "cd1": cd1})
        return str(r.get("data") or "")


# ---------------- podaci za otpremanje ----------------
def sub_cd1(sub_path: Path, movie_file: Path, mhash: str, msize: int, with_content: bool) -> dict:
    raw = sub_path.read_bytes()
    cd1 = {
        "subhash": hashlib.md5(raw).hexdigest(),
        "subfilename": sub_path.name,
        "moviehash": mhash,
        "moviebytesize": str(msize),
        "moviefilename": movie_file.name,
    }
    if with_content:
        cd1["subcontent"] = base64.b64encode(zlib.compress(raw)).decode("ascii")
    return cd1


def baseinfo(imdb: str, release_name: str, comment: str, hearing_impaired: bool, hd: bool) -> dict:
    return {
        "idmovieimdb": imdb,
        "sublanguageid": LANG_SERBIAN,
        "moviereleasename": release_name,
        "subauthorcomment": comment,
        "subtranslator": TRANSLATOR,
        "hearingimpaired": "1" if hearing_impaired else "0",
        "highdefinition": "1" if hd else "0",
        "automatictranslation": "1",  # uvek: prevod je napravio AI
        "foreignpartsonly": "0",
    }


# ---------------- spisak prevoda koji čekaju otpremanje ----------------
@dataclass
class Job:
    """Jedan preveden film. Fajlovi titlova su sačuvani u folderu posla."""
    movie_path: str
    movie_hash: str
    movie_size: int
    subs: dict = field(default_factory=dict)  # "lat"/"cyr" -> ime fajla u folderu posla
    created: str = ""
    uploaded: dict = field(default_factory=dict)  # "lat"/"cyr" -> URL ili napomena
    folder: str = ""

    @property
    def title(self) -> str:
        return Path(self.movie_path).name

    def sub_path(self, script: str) -> Path:
        return Path(self.folder) / self.subs[script]

    def save(self) -> None:
        d = asdict(self)
        d.pop("folder")
        (Path(self.folder) / "job.json").write_text(json.dumps(d, ensure_ascii=False, indent=2), "utf-8")


def jobs_dir(config_dir: Path) -> Path:
    d = config_dir / "za_otpremanje"
    d.mkdir(parents=True, exist_ok=True)
    return d


def create_job(config_dir: Path, movie: Path, subs: dict[str, Path]) -> Job:
    """Kopira prevedene titlove u poseban folder i pamti podatke o filmu za kasnije otpremanje."""
    mhash, msize = movie_hash(movie)
    folder = jobs_dir(config_dir) / f"{datetime.now():%Y%m%d-%H%M%S}-{mhash}"
    folder.mkdir(parents=True, exist_ok=True)
    names = {}
    for script, src in subs.items():
        dst = folder / src.name
        dst.write_bytes(src.read_bytes())
        names[script] = dst.name
    job = Job(str(movie), mhash, msize, names, datetime.now().isoformat(timespec="seconds"), folder=str(folder))
    job.save()
    return job


def load_jobs(config_dir: Path) -> list[Job]:
    jobs = []
    for f in sorted(jobs_dir(config_dir).glob("*/job.json"), reverse=True):
        try:
            d = json.loads(f.read_text("utf-8"))
            jobs.append(Job(**d, folder=str(f.parent)))
        except Exception:  # noqa: BLE001 - oštećen zapis preskačemo
            continue
    return jobs
