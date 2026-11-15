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
- `opensubs.py` — OpenSubtitles.org XML-RPC (heš filma, prijava, TryUpload/Upload), spisak sačuvanih prevoda
- `upload_dialog.py` — prozor za otpremanje
- `pipeline.py` — izbor trake, serije od 60 redova + 4 reda konteksta, keš u `%APPDATA%\MKV Prevodilac\cache`
- `tests/` — unit testovi (`python -m pytest tests`), prevodilac i OpenSubtitles server su lažni

## Odluke
- API umesto lokalnog modela (bolji srpski); besplatni Gemini nivo, rezerva kad se potroši limit.
- Ćirilica se pravi transliteracijom iz latiničkog prevoda (jedan poziv API-ja).
- Ključevi su u `%APPDATA%\MKV Prevodilac\config.json` kao običan tekst.
- Otpremanje na OpenSubtitles uvek šalje `automatictranslation=1`; to se ne sme ukloniti.
- `certifi` je obavezan: Windows skladište sertifikata ne prihvata lanac api.opensubtitles.org (ISRG Root YE).

## Neprovereno / nije urađeno
- Gemini prevod potvrđen u praksi (2026-11-15); Groq rezervni model nije proveren.
- Otpremanje na opensubtitles.org potvrđeno u praksi (2026-11-15).
- User Agent nije registrovan kod OpenSubtitles-a; opensubtitles.com REST nije podržan.
- Nema OCR-a za PGS/VobSub; ASS tagovi usred reda se gube.
- `.exe` (build.bat, PyInstaller onefile, ~45 MB) radi; certifi se pakuje sam. Ideja za kasnije: MSI kao za PDF Alat.
