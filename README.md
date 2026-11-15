# MKV Prevodilac titlova

Windows aplikacija (Python + PySide6): iz MKV-a izvuče tekstualni titl, prevede ga na srpski
(ekavica; latinica i/ili ćirilica) preko Gemini API-ja (uz rezervni servis) i ugradi prevod
u **novi** MKV bez ponovnog kodiranja. Original ostaje netaknut.

## Potrebno
1. Python 3.10+ i `pip install -r requirements.txt`
2. [MKVToolNix](https://mkvtoolnix.download) (mkvmerge + mkvextract) - aplikacija ga sama nađe u Program Files ili PATH-u
3. Gemini API ključ (Google AI Studio, besplatno, bez kartice). Opciono ključ za rezervni servis (Groq/OpenRouter - bilo koji OpenAI-kompatibilan).

## Pokretanje
    python app.py
Za .exe: `build.bat` -> `dist\MKV Prevodilac.exe`

## Kako radi
1. Izabere titl (traka na izabranom jeziku; izbegava forced/SDH/signs) ili ručno izabrana traka (kad je u listi jedan fajl).
2. Prevodi u serijama od 60 redova uz 4 reda konteksta; proverava da broj redova odgovara, a ako ne - deli seriju.
3. Kad dnevni limit Gemini-ja prođe, prelazi na rezervni servis. Delimičan prevod se čuva u kešu
   (`%APPDATA%\MKV Prevodilac\cache`), pa prekinut posao nastavlja tamo gde je stao.
4. Ćirilica se pravi transliteracijom iz latinice (iz istog prevoda), tagovi se ne diraju.
5. mkvmerge pravi `ime.sr.mkv` sa dodatim trakama "Srpski (latinica, AI)" / "Srpski (ćirilica, AI)".

## Ograničenja
- Slikoviti titlovi (PGS, VobSub) nisu podržani - treba OCR.
- ASS: tagovi unutar reda (kurziv usred rečenice) se gube, početni ({\an8}) i crteži ostaju.
- Ključevi su u `%APPDATA%\MKV Prevodilac\config.json` kao običan tekst.
- Besplatni nivoi imaju dnevne limite i mogu koristiti poslat tekst za poboljšanje servisa.

## Testovi
    python -m pytest tests
