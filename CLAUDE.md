# MKV Prevodilac — kontekst za Claude Code

Windows desktop aplikacija (Python 3.10+, PySide6). Iz MKV-a izvlači tekstualni titl (SRT/ASS),
prevodi ga na srpski (ekavica) preko LLM API-ja i ugrađuje latiničku i/ili ćiriličku traku u
**novi** fajl `ime.sr.mkv` (remux preko MKVToolNix-a, bez ponovnog kodiranja). Original se ne dira.

Korisnik komunicira na srpskom (latinica); odgovaraj na srpskom.

## Struktura
- `app.py` — GUI (PySide6), lista fajlova, podešavanja, ključevi, pokretanje u QThread-u
- `mkv.py` — mkvmerge -J / mkvextract / mkvmerge, pronalaženje MKVToolNix-a
- `subs.py` — parsiranje i upis SRT/ASS, transliteracija latinica → ćirilica
- `translate.py` — Gemini (glavni) + OpenAI-kompatibilni rezervni servis (Groq/OpenRouter)
- `pipeline.py` — izbor trake, serije od 60 redova + 4 reda konteksta, keš u `%APPDATA%\MKV Prevodilac\cache`
- `tests/test_core.py` — 23 unit testa (`python -m pytest tests`), prevodilac je lažan

## Odluke
- API umesto lokalnog modela (bolji srpski); besplatni Gemini nivo, rezerva kad se potroši limit.
- Ćirilica se pravi transliteracijom iz latiničkog prevoda (jedan poziv API-ja).
- Ključevi su u `%APPDATA%\MKV Prevodilac\config.json` kao običan tekst.

## Neprovereno / nije urađeno
- Nikad nije testirano sa pravim API ključem: naziv modela `gemini-flash-lite-latest` i Groq model
  `openai/gpt-oss-120b` nisu potvrđeni.
- GUI nije ručno isproban na Windows-u.
- Nema OCR-a za PGS/VobSub; ASS tagovi usred reda se gube.
- `.exe` preko `build.bat` (PyInstaller) nije još napravljen; ideja za kasnije: MSI kao za PDF Alat.
