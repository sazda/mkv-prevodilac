"""MKV Prevodilac titlova - PySide6 GUI."""
from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QMessageBox, QPlainTextEdit,
    QProgressBar, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

import mkv
import opensubs
from pipeline import Options, config_dir, process_file
from upload_dialog import UploadDialog
from translate import (
    Cancelled, GeminiProvider, OpenAICompatProvider, ProviderError, Translator,
)

TITLE = "MKV Prevodilac titlova By Aleksandar Sazdanović"

# naziv u listi -> (ime za prompt, ISO kodovi koje mkvmerge može da vrati)
LANGS = {
    "Engleski": ("English", ("eng", "en")),
    "Nemački": ("German", ("ger", "deu", "de")),
    "Francuski": ("French", ("fre", "fra", "fr")),
    "Španski": ("Spanish", ("spa", "es")),
    "Italijanski": ("Italian", ("ita", "it")),
    "Ruski": ("Russian", ("rus", "ru")),
    "Turski": ("Turkish", ("tur", "tr")),
    "Japanski": ("Japanese", ("jpn", "ja")),
    "Korejski": ("Korean", ("kor", "ko")),
    "Portugalski": ("Portuguese", ("por", "pt")),
    "Poljski": ("Polish", ("pol", "pl")),
    "Hrvatski/Bosanski": ("Croatian", ("hrv", "bos", "hr", "bs")),
}

DEFAULTS = {
    "gemini_key": "",
    "gemini_model": "gemini-flash-lite-latest",
    "gemini_rpm": 10,
    "fb_url": "https://api.groq.com/openai/v1",
    "fb_key": "",
    "fb_model": "openai/gpt-oss-120b",
    "mkv_dir": "",
    "out_dir": "",
    "lang": "Engleski",
    "latin": True,
    "cyrillic": True,
    "make_default": True,
    "save_external": False,
    "make_mkv": True,
    "os_user": "",
    "os_agent": opensubs.DEFAULT_USER_AGENT,
}


def cfg_path() -> Path:
    return config_dir() / "config.json"


def load_cfg() -> dict:
    cfg = dict(DEFAULTS)
    try:
        cfg.update(json.loads(cfg_path().read_text("utf-8")))
    except Exception:
        pass
    cfg["gemini_key"] = cfg["gemini_key"] or os.environ.get("GEMINI_API_KEY", "")
    return cfg


def save_cfg(cfg: dict) -> None:
    cfg_path().write_text(json.dumps(cfg, ensure_ascii=False, indent=2), "utf-8")


def build_providers(cfg: dict, cancel: threading.Event):
    provs = []
    if cfg["gemini_key"].strip():
        provs.append(GeminiProvider(cfg["gemini_key"], cfg["gemini_model"], cfg["gemini_rpm"], cancel))
    if cfg["fb_key"].strip():
        provs.append(OpenAICompatProvider(cfg["fb_url"], cfg["fb_key"], cfg["fb_model"], 20, cancel, "Rezerva"))
    return provs


class Worker(QObject):
    log = Signal(str)
    progress = Signal(float, str, int, int)  # frakcija fajla, poruka, indeks fajla, ukupno
    done = Signal(str)

    def __init__(self, files, opts: Options, cfg: dict):
        super().__init__()
        self.files, self.opts, self.cfg = files, opts, cfg
        self.cancel = threading.Event()

    def run(self):
        ok = fail = 0
        try:
            translator = Translator(
                build_providers(self.cfg, self.cancel), self.opts.src_name,
                self.opts.batch_size, self.log.emit, self.cancel,
            )
        except ProviderError as e:
            self.log.emit(f"GREŠKA: {e}")
            self.done.emit("Nije podešen prevodilac.")
            return
        total = len(self.files)
        for i, f in enumerate(self.files):
            if self.cancel.is_set():
                break
            self.log.emit(f"\n[{i + 1}/{total}] {Path(f).name}")
            try:
                process_file(
                    f, self.opts, translator, self.log.emit,
                    lambda fr, msg, i=i: self.progress.emit(fr, msg, i, total),
                    self.cancel,
                )
                ok += 1
            except Cancelled:
                self.log.emit("  Prekinuto.")
                break
            except Exception as e:  # noqa: BLE001 - prikaži korisniku i nastavi sa sledećim
                fail += 1
                self.log.emit(f"  GREŠKA: {e}")
        state = "Prekinuto. " if self.cancel.is_set() else ""
        self.done.emit(f"{state}Gotovo: {ok} uspešno, {fail} neuspešno.")


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(TITLE)
        self.resize(900, 820)
        self.setAcceptDrops(True)
        self.cfg = load_cfg()
        self.thread: QThread | None = None
        self.worker: Worker | None = None
        self.tracks_cache: dict[str, list[mkv.Track]] = {}
        self._os_pwd = ""  # lozinka za OpenSubtitles se ne čuva na disku
        self._build()
        self._load_into_ui()

    # ---------------- UI ----------------
    def _build(self):
        root = QVBoxLayout(self)

        g = QGroupBox("1. MKV fajlovi (možeš i prevući u prozor)")
        v = QVBoxLayout(g)
        self.files = QListWidget()
        self.files.setSelectionMode(QListWidget.ExtendedSelection)
        self.files.currentItemChanged.connect(self._on_select)
        v.addWidget(self.files)
        h = QHBoxLayout()
        for text, fn in (("Dodaj fajlove…", self._add_files), ("Dodaj folder…", self._add_folder),
                         ("Ukloni izabrane", self._remove), ("Očisti listu", self.files.clear)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            h.addWidget(b)
        h.addStretch()
        v.addLayout(h)
        root.addWidget(g)

        g = QGroupBox("2. Titl i pismo")
        f = QFormLayout(g)
        self.lang = QComboBox()
        self.lang.addItems(LANGS.keys())
        f.addRow("Jezik izvornog titla:", self.lang)
        self.track = QComboBox()
        self.track.addItem("Automatski (preporučeno)", None)
        f.addRow("Traka titla (za izabrani fajl):", self.track)
        h = QHBoxLayout()
        self.cb_lat = QCheckBox("Latinica")
        self.cb_cyr = QCheckBox("Ćirilica")
        h.addWidget(self.cb_lat)
        h.addWidget(self.cb_cyr)
        h.addStretch()
        f.addRow("Izlazno pismo:", h)
        root.addWidget(g)

        g = QGroupBox("3. Izlaz")
        f = QFormLayout(g)
        self.cb_mkv = QCheckBox("Napravi novi MKV sa ugrađenim prevodom (original ostaje netaknut)")
        self.cb_def = QCheckBox("Srpski titl postavi kao podrazumevani")
        self.cb_ext = QCheckBox("Sačuvaj i zasebne titl fajlove pored videa (.srt/.ass)")
        for c in (self.cb_mkv, self.cb_def, self.cb_ext):
            f.addRow(c)
        h = QHBoxLayout()
        self.out_dir = QLineEdit()
        self.out_dir.setPlaceholderText("prazno = isti folder kao original")
        b = QPushButton("Izaberi…")
        b.clicked.connect(lambda: self._pick_dir(self.out_dir))
        h.addWidget(self.out_dir)
        h.addWidget(b)
        f.addRow("Izlazni folder:", h)
        root.addWidget(g)

        g = QGroupBox("4. Podešavanja prevodioca")
        f = QFormLayout(g)
        self.g_key = QLineEdit()
        self.g_key.setEchoMode(QLineEdit.Password)
        self.g_model = QLineEdit()
        self.g_rpm = QSpinBox()
        self.g_rpm.setRange(1, 600)
        self.g_rpm.setSuffix(" zahteva/min")
        self.fb_url = QLineEdit()
        self.fb_key = QLineEdit()
        self.fb_key.setEchoMode(QLineEdit.Password)
        self.fb_model = QLineEdit()
        f.addRow("Gemini API ključ:", self.g_key)
        f.addRow("Gemini model:", self.g_model)
        f.addRow("Gemini brzina:", self.g_rpm)
        f.addRow("Rezerva - URL (OpenAI-kompatibilan):", self.fb_url)
        f.addRow("Rezerva - ključ:", self.fb_key)
        f.addRow("Rezerva - model:", self.fb_model)
        h = QHBoxLayout()
        self.mkv_dir = QLineEdit()
        self.mkv_dir.setPlaceholderText("prazno = automatski (Program Files ili PATH)")
        b = QPushButton("Izaberi…")
        b.clicked.connect(lambda: self._pick_dir(self.mkv_dir))
        h.addWidget(self.mkv_dir)
        h.addWidget(b)
        f.addRow("Folder MKVToolNix:", h)
        hb = QHBoxLayout()
        tb = QPushButton("Testiraj prevodioce")
        tb.clicked.connect(self._test)
        hb.addWidget(tb)
        hb.addStretch()
        f.addRow(hb)
        warn = QLabel("Ključevi se čuvaju kao običan tekst u %APPDATA%\\MKV Prevodilac\\config.json")
        warn.setStyleSheet("color: gray;")
        f.addRow(warn)
        root.addWidget(g)

        g = QGroupBox("5. OpenSubtitles.org")
        h = QHBoxLayout(g)
        lbl = QLabel("Svaki prevod se čuva, pa ga kasnije možeš otpremiti na opensubtitles.org "
                     "(sa oznakom da je mašinski preveden).")
        lbl.setWordWrap(True)
        h.addWidget(lbl, 1)
        b = QPushButton("Otpremi prevode…")
        b.clicked.connect(self._open_upload)
        h.addWidget(b)
        root.addWidget(g)

        h = QHBoxLayout()
        self.btn_start = QPushButton("Pokreni prevod")
        self.btn_start.clicked.connect(self._start)
        self.btn_cancel = QPushButton("Prekini")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self._cancel)
        h.addWidget(self.btn_start)
        h.addWidget(self.btn_cancel)
        root.addLayout(h)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.status = QLabel("")
        root.addWidget(self.bar)
        root.addWidget(self.status)
        self.logbox = QPlainTextEdit()
        self.logbox.setReadOnly(True)
        root.addWidget(self.logbox, 1)

    def _load_into_ui(self):
        c = self.cfg
        self.lang.setCurrentText(c["lang"])
        self.cb_lat.setChecked(c["latin"])
        self.cb_cyr.setChecked(c["cyrillic"])
        self.cb_mkv.setChecked(c["make_mkv"])
        self.cb_def.setChecked(c["make_default"])
        self.cb_ext.setChecked(c["save_external"])
        self.out_dir.setText(c["out_dir"])
        self.g_key.setText(c["gemini_key"])
        self.g_model.setText(c["gemini_model"])
        self.g_rpm.setValue(int(c["gemini_rpm"]))
        self.fb_url.setText(c["fb_url"])
        self.fb_key.setText(c["fb_key"])
        self.fb_model.setText(c["fb_model"])
        self.mkv_dir.setText(c["mkv_dir"])

    def _read_cfg(self) -> dict:
        return {
            "lang": self.lang.currentText(),
            "latin": self.cb_lat.isChecked(),
            "cyrillic": self.cb_cyr.isChecked(),
            "make_mkv": self.cb_mkv.isChecked(),
            "make_default": self.cb_def.isChecked(),
            "save_external": self.cb_ext.isChecked(),
            "out_dir": self.out_dir.text().strip(),
            "gemini_key": self.g_key.text().strip(),
            "gemini_model": self.g_model.text().strip() or DEFAULTS["gemini_model"],
            "gemini_rpm": self.g_rpm.value(),
            "fb_url": self.fb_url.text().strip(),
            "fb_key": self.fb_key.text().strip(),
            "fb_model": self.fb_model.text().strip(),
            "mkv_dir": self.mkv_dir.text().strip(),
            "os_user": self.cfg.get("os_user", ""),
            "os_agent": self.cfg.get("os_agent", opensubs.DEFAULT_USER_AGENT),
        }

    # ---------------- fajlovi ----------------
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        for u in e.mimeData().urls():
            p = Path(u.toLocalFile())
            if p.is_dir():
                self._add_paths(sorted(p.glob("*.mkv")))
            else:
                self._add_paths([p])

    def _add_paths(self, paths):
        have = {self.files.item(i).text() for i in range(self.files.count())}
        for p in paths:
            s = str(p)
            if s.lower().endswith(".mkv") and s not in have:
                self.files.addItem(s)
                have.add(s)

    def _add_files(self):
        fs, _ = QFileDialog.getOpenFileNames(self, "Izaberi MKV fajlove", "", "Matroska (*.mkv)")
        self._add_paths(fs)

    def _add_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Izaberi folder sa MKV fajlovima")
        if d:
            self._add_paths(sorted(Path(d).glob("*.mkv")))

    def _remove(self):
        for it in self.files.selectedItems():
            self.files.takeItem(self.files.row(it))

    def _pick_dir(self, edit: QLineEdit):
        d = QFileDialog.getExistingDirectory(self, "Izaberi folder", edit.text())
        if d:
            edit.setText(d)

    def _on_select(self, cur, _prev):
        self.track.clear()
        self.track.addItem("Automatski (preporučeno)", None)
        if not cur:
            return
        try:
            tools = mkv.find_tools(self.mkv_dir.text().strip())
            for t in mkv.probe(tools, cur.text()):
                if t.is_text:
                    self.track.addItem(t.label(), t.id)
        except Exception as e:  # noqa: BLE001
            self._log(f"Ne mogu da pročitam trake: {e}")

    # ---------------- rad ----------------
    def _log(self, s: str):
        self.logbox.appendPlainText(s)

    def _test(self):
        cfg = self._read_cfg()
        cancel = threading.Event()
        try:
            provs = build_providers(cfg, cancel)
        except ProviderError as e:
            QMessageBox.warning(self, TITLE, str(e))
            return
        if not provs:
            QMessageBox.warning(self, TITLE, "Unesi bar Gemini ključ.")
            return
        lines = []
        for p in provs:
            try:
                lines.append(f"{p.name}: OK  (\"Hello, how are you?\" -> \"{p.ping()}\")")
            except Exception as e:  # noqa: BLE001
                lines.append(f"{p.name}: GREŠKA - {e}")
        QMessageBox.information(self, TITLE, "\n".join(lines))

    def _open_upload(self):
        dlg = UploadDialog(self, config_dir(), self.cfg.get("os_user", ""), self._os_pwd,
                           self.cfg.get("os_agent", opensubs.DEFAULT_USER_AGENT), TITLE)
        dlg.exec()
        self._os_pwd = dlg.pwd.text()
        self.cfg = {**self._read_cfg(), "os_user": dlg.user.text().strip(),
                    "os_agent": dlg.agent.text().strip() or opensubs.DEFAULT_USER_AGENT}
        save_cfg(self.cfg)

    def _start(self):
        cfg = self._read_cfg()
        save_cfg(cfg)
        self.cfg = cfg
        files = [self.files.item(i).text() for i in range(self.files.count())]
        if not files:
            QMessageBox.information(self, TITLE, "Dodaj bar jedan MKV fajl.")
            return
        if not (cfg["latin"] or cfg["cyrillic"]):
            QMessageBox.information(self, TITLE, "Izaberi latinicu i/ili ćirilicu.")
            return
        if not (cfg["make_mkv"] or cfg["save_external"]):
            QMessageBox.information(self, TITLE, "Uključi pravljenje MKV-a ili čuvanje zasebnih titlova.")
            return
        name, codes = LANGS[cfg["lang"]]
        # ručno izabrana traka važi samo ako se obrađuje jedan fajl
        track_id = self.track.currentData() if len(files) == 1 else None
        opts = Options(
            mkvtoolnix_dir=cfg["mkv_dir"], src_name=name, src_codes=codes, track_id=track_id,
            latin=cfg["latin"], cyrillic=cfg["cyrillic"], make_default=cfg["make_default"],
            save_external=cfg["save_external"], make_mkv=cfg["make_mkv"], out_dir=cfg["out_dir"],
        )
        self.logbox.clear()
        self.worker = Worker(files, opts, cfg)
        self.thread = QThread()
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.log.connect(self._log)
        self.worker.progress.connect(self._on_progress)
        self.worker.done.connect(self._on_done)
        self.btn_start.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.thread.start()

    def _cancel(self):
        if self.worker:
            self.worker.cancel.set()
            self.status.setText("Prekidam…")

    def _on_progress(self, frac: float, msg: str, i: int, total: int):
        self.bar.setValue(int(((i + frac) / total) * 1000))
        self.status.setText(f"Fajl {i + 1}/{total}: {msg}")

    def _on_done(self, msg: str):
        self.status.setText(msg)
        self._log("\n" + msg)
        self.btn_start.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        if self.thread:
            self.thread.quit()
            self.thread.wait()

    def closeEvent(self, e):
        if self.worker:
            self.worker.cancel.set()
        if self.thread and self.thread.isRunning():
            self.thread.quit()
            self.thread.wait(3000)
        e.accept()


def main():
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
