"""Prevodioci titlova: Gemini API + rezervni OpenAI-kompatibilni servis (Groq, OpenRouter...).

Samo standardna biblioteka (urllib).
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import urllib.error
import urllib.request
from typing import Callable

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"


class ProviderError(Exception):
    """Greška servisa (mreža, ključ, loš odgovor)."""


class QuotaExceeded(ProviderError):
    """Dnevna/trajna kvota potrošena - nema smisla ponavljati."""


class Cancelled(Exception):
    pass


SYSTEM_TMPL = """You are a professional subtitle translator. Translate subtitle lines from {src} into Serbian.

Hard rules:
1. Output ONLY a JSON array of strings, with EXACTLY as many items as the "translate" array, in the same order. Never merge, split, drop or add items. If an item is empty, return an empty string.
2. Serbian, EKAVIAN pronunciation only (lepo, mleko, dete, razumeti, svetlost - never ijekavian: lijepo, mlijeko, dijete). LATIN script with correct diacritics (č, ć, ž, š, đ). Never write 'dj' for 'đ'.
3. Subtitles are spoken dialogue: use natural, concise, colloquial Serbian that is easy to read in a couple of seconds. Keep tone and register (formal/slang) of the original.
4. Keep HTML tags (<i>, <b>, <font ...>) and any {{...}} blocks exactly as they are, in the right place. Keep line breaks (\\n) where they are in the source.
5. Do not translate personal names or place names. Translate sound/music cues in brackets, e.g. [music] -> [muzika]. Keep music notes (♪).
6. "context_before" contains preceding lines for consistency only. Do NOT translate or return them.
7. Output nothing except the JSON array."""


def build_system(src_name: str) -> str:
    src = src_name if src_name and src_name.lower() != "auto" else "the source language (detect it automatically)"
    return SYSTEM_TMPL.format(src=src)


def build_user(lines: list[str], context: list[str]) -> str:
    payload = {"context_before": context[-4:], "translate": lines}
    return (
        f'Translate the "translate" array. Return ONLY a JSON array with exactly {len(lines)} strings.\n'
        + json.dumps(payload, ensure_ascii=False)
    )


def parse_json_array(text: str) -> list[str]:
    t = text.strip()
    t = re.sub(r"<think>.*?</think>", "", t, flags=re.S | re.I).strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.I).strip()
    try:
        data = json.loads(t)
    except json.JSONDecodeError:
        a, b = t.find("["), t.rfind("]")
        if a < 0 or b <= a:
            raise ProviderError("Odgovor nije JSON niz.")
        try:
            data = json.loads(t[a : b + 1])
        except json.JSONDecodeError as e:
            raise ProviderError(f"Neispravan JSON u odgovoru: {e}")
    if isinstance(data, dict):  # {"translations": [...]}
        for v in data.values():
            if isinstance(v, list):
                data = v
                break
    if not isinstance(data, list):
        raise ProviderError("Odgovor nije niz.")
    return ["" if x is None else str(x) for x in data]


def _retry_delay(body: str) -> float | None:
    for pat in (r'retryDelay\W+(\d+(?:\.\d+)?)\s*s', r"retry in\s+(\d+(?:\.\d+)?)\s*s"):
        m = re.search(pat, body, re.I)
        if m:
            return float(m.group(1))
    return None


class BaseProvider:
    name = "base"

    def __init__(self, rpm: float = 10, cancel: threading.Event | None = None):
        self.min_interval = 60.0 / max(rpm, 0.5)
        self._last = 0.0
        self.cancel = cancel or threading.Event()

    # --- pomoćne ---
    def _sleep(self, sec: float) -> None:
        end = time.time() + sec
        while time.time() < end:
            if self.cancel.is_set():
                raise Cancelled()
            time.sleep(min(0.25, max(0.0, end - time.time())))

    def _pace(self) -> None:
        wait = self._last + self.min_interval - time.time()
        if wait > 0:
            self._sleep(wait)
        self._last = time.time()

    def _post(self, url: str, headers: dict, body: dict) -> dict:
        data = json.dumps(body).encode("utf-8")
        hdr = {"Content-Type": "application/json", **headers}
        last: Exception | None = None
        for attempt in range(4):
            self._pace()
            req = urllib.request.Request(url, data=data, headers=hdr, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    return json.loads(r.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                text = e.read().decode("utf-8", "replace")
                if e.code == 429:
                    if re.search(r"PerDay|per day|daily", text, re.I):
                        raise QuotaExceeded("dnevni limit potrošen")
                    wait = _retry_delay(text) or 20 * (attempt + 1)
                    if wait > 90:
                        raise QuotaExceeded(f"limit, čekanje {int(wait)}s")
                    self._sleep(wait + 1)
                    continue
                if e.code in (500, 502, 503, 504):
                    last = ProviderError(f"HTTP {e.code}")
                    self._sleep(3 * 2**attempt)
                    continue
                if e.code in (401, 403):
                    raise ProviderError(f"Ključ nije ispravan ili nema pristup (HTTP {e.code})")
                raise ProviderError(f"HTTP {e.code}: {text[:300]}")
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                last = e
                self._sleep(3 * (attempt + 1))
        raise ProviderError(f"Mrežna greška: {last}")

    def translate_batch(self, lines: list[str], context: list[str], src_name: str) -> list[str]:
        raise NotImplementedError

    def ping(self) -> str:
        """Brz test ključa. Vraća kratku poruku ili baca ProviderError."""
        out = self.translate_batch(["Hello, how are you?"], [], "English")
        return out[0] if out else ""


class GeminiProvider(BaseProvider):
    name = "Gemini"

    def __init__(self, api_key: str, model: str = "gemini-flash-lite-latest", rpm: float = 10, cancel=None):
        super().__init__(rpm, cancel)
        if not api_key:
            raise ProviderError("Nedostaje Gemini API ključ.")
        self.key, self.model = api_key.strip(), model.strip()

    def translate_batch(self, lines, context, src_name):
        body = {
            "systemInstruction": {"parts": [{"text": build_system(src_name)}]},
            "contents": [{"role": "user", "parts": [{"text": build_user(lines, context)}]}],
            "generationConfig": {
                "temperature": 0.3,
                "responseMimeType": "application/json",
                "responseSchema": {"type": "ARRAY", "items": {"type": "STRING"}},
            },
        }
        url = f"{GEMINI_BASE}/models/{self.model}:generateContent"
        resp = self._post(url, {"x-goog-api-key": self.key}, body)
        cands = resp.get("candidates") or []
        if not cands:
            reason = (resp.get("promptFeedback") or {}).get("blockReason", "nepoznato")
            raise ProviderError(f"Gemini nije vratio odgovor (razlog: {reason}).")
        parts = (cands[0].get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts)
        return parse_json_array(text)


class OpenAICompatProvider(BaseProvider):
    def __init__(self, base_url: str, api_key: str, model: str, rpm: float = 20, cancel=None, name: str = "Rezerva"):
        super().__init__(rpm, cancel)
        if not (base_url and api_key and model):
            raise ProviderError("Rezervni servis nije potpuno podešen.")
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.key, self.model, self.name = api_key.strip(), model.strip(), name

    def translate_batch(self, lines, context, src_name):
        body = {
            "model": self.model,
            "temperature": 0.3,
            "messages": [
                {"role": "system", "content": build_system(src_name)},
                {"role": "user", "content": build_user(lines, context)},
            ],
        }
        resp = self._post(self.url, {"Authorization": f"Bearer {self.key}"}, body)
        try:
            text = resp["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            raise ProviderError("Neočekivan oblik odgovora.")
        return parse_json_array(text)


class Translator:
    """Prevodi listu redova u serijama; prelazi na rezervni servis kad glavni zakaže."""

    def __init__(
        self,
        providers: list[BaseProvider],
        src_name: str = "English",
        batch_size: int = 60,
        log: Callable[[str], None] = print,
        cancel: threading.Event | None = None,
    ):
        if not providers:
            raise ProviderError("Nijedan prevodilac nije podešen (unesi bar Gemini ključ).")
        self.providers = providers
        self.active = list(providers)
        self.src_name, self.batch_size, self.log = src_name, batch_size, log
        self.cancel = cancel or threading.Event()

    def _do_batch(self, lines: list[str], ctx: list[str]) -> list[str]:
        errors: list[str] = []
        for p in list(self.active):
            if self.cancel.is_set():
                raise Cancelled()
            try:
                out = p.translate_batch(lines, ctx, self.src_name)
            except QuotaExceeded as e:
                self.log(f"[{p.name}] {e} - isključujem do kraja ovog posla.")
                self.active.remove(p)
                errors.append(f"{p.name}: {e}")
                continue
            except ProviderError as e:
                self.log(f"[{p.name}] greška: {e}")
                errors.append(f"{p.name}: {e}")
                continue
            if len(out) == len(lines):
                return out
            if len(lines) > 1:  # broj redova se ne poklapa -> podeli seriju
                mid = len(lines) // 2
                return self._do_batch(lines[:mid], ctx) + self._do_batch(lines[mid:], ctx + lines[:mid])
            if out:
                return [" ".join(out)]
            errors.append(f"{p.name}: prazan odgovor")
        raise ProviderError("Prevod nije uspeo - " + "; ".join(errors) if errors else "nijedan servis nije dostupan")

    def translate_lines(
        self,
        texts: list[str],
        progress: Callable[[float], None] | None = None,
        cache: dict | None = None,
        save_cache: Callable[[dict], None] | None = None,
    ) -> list[str]:
        idx = [i for i, t in enumerate(texts) if t.strip()]
        src = [texts[i] for i in idx]
        result = list(texts)  # prazni ostaju isti
        h = hashlib.sha1("\x00".join(src).encode("utf-8")).hexdigest()
        meta = {"n": len(src), "bs": self.batch_size, "h": h}
        if cache is not None and cache.get("meta") != meta:
            cache.clear()
            cache.update({"meta": meta, "done": {}})
        done = cache["done"] if cache is not None else {}

        out_all: list[str] = []
        total = len(src)
        for start in range(0, total, self.batch_size):
            if self.cancel.is_set():
                raise Cancelled()
            chunk = src[start : start + self.batch_size]
            key = str(start)
            if key in done and len(done[key]) == len(chunk):
                out = done[key]
            else:
                ctx = src[max(0, start - 4) : start]
                out = self._do_batch(chunk, ctx)
                done[key] = out
                if save_cache and cache is not None:
                    save_cache(cache)
            out_all.extend(o.strip() for o in out)
            if progress:
                progress(min(1.0, (start + len(chunk)) / max(total, 1)))
        for i, o in zip(idx, out_all):
            result[i] = o
        return result
