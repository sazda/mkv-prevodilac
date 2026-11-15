"""Prozor za otpremanje sačuvanih prevoda na OpenSubtitles.org."""
from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFormLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit, QPushButton, QSplitter,
    QVBoxLayout, QWidget,
)

import opensubs

SCRIPTS = {"lat": "Latinica", "cyr": "Ćirilica"}


class _Busy:
    def __enter__(self):
        QGuiApplication.setOverrideCursor(Qt.WaitCursor)

    def __exit__(self, *a):
        QGuiApplication.restoreOverrideCursor()


class UploadDialog(QDialog):
    def __init__(self, parent, config_dir: Path, username: str, password: str, user_agent: str, title: str):
        super().__init__(parent)
        self.setWindowTitle(f"Otpremanje na OpenSubtitles.org - {title}")
        self.resize(900, 620)
        self.config_dir = config_dir
        self.app_title = title
        self.client: opensubs.Client | None = None
        self.jobs: list[opensubs.Job] = []
        self._build(username, password, user_agent)
        self._reload()

    # ---------------- UI ----------------
    def _build(self, username: str, password: str, user_agent: str):
        root = QVBoxLayout(self)
        acc = QFormLayout()
        self.user = QLineEdit(username)
        self.pwd = QLineEdit(password)
        self.pwd.setEchoMode(QLineEdit.Password)
        self.pwd.setPlaceholderText("ne čuva se na disku - pamti se dok je aplikacija otvorena")
        self.agent = QLineEdit(user_agent)
        acc.addRow("Korisničko ime (opensubtitles.org):", self.user)
        acc.addRow("Lozinka:", self.pwd)
        acc.addRow("User Agent:", self.agent)
        root.addLayout(acc)

        split = QSplitter()
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.addWidget(QLabel("Sačuvani prevodi:"))
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._on_select)
        lv.addWidget(self.list)
        b = QPushButton("Obriši izabrani iz liste")
        b.clicked.connect(self._delete)
        lv.addWidget(b)
        split.addWidget(left)

        right = QWidget()
        f = QFormLayout(right)
        self.file_lbl = QLabel("-")
        self.file_lbl.setWordWrap(True)
        f.addRow("Video fajl:", self.file_lbl)
        h = QHBoxLayout()
        self.imdb = QLineEdit()
        self.imdb.setPlaceholderText("tt0133093 ili link sa imdb.com")
        self.imdb.textEdited.connect(lambda _t: self.movie_lbl.setText("(ručno unet IMDb broj)"))
        b1 = QPushButton("Prepoznaj po fajlu")
        b1.clicked.connect(self._detect)
        b2 = QPushButton("Traži po nazivu…")
        b2.clicked.connect(self._search)
        h.addWidget(self.imdb)
        h.addWidget(b1)
        h.addWidget(b2)
        f.addRow("IMDb film:", h)
        self.movie_lbl = QLabel("-")
        f.addRow("", self.movie_lbl)
        h = QHBoxLayout()
        self.cb = {k: QCheckBox(v) for k, v in SCRIPTS.items()}
        for c in self.cb.values():
            h.addWidget(c)
        h.addStretch()
        f.addRow("Otpremi:", h)
        self.cb_hi = QCheckBox("Za osobe oštećenog sluha (SDH)")
        self.cb_hd = QCheckBox("HD izdanje")
        self.cb_mt = QCheckBox("Mašinski prevod (AI) - uvek uključeno")
        self.cb_mt.setChecked(True)
        self.cb_mt.setEnabled(False)
        for c in (self.cb_hi, self.cb_hd, self.cb_mt):
            f.addRow(c)
        self.comment = QLineEdit(opensubs.DEFAULT_COMMENT)
        f.addRow("Komentar:", self.comment)
        self.btn_up = QPushButton("Otpremi na OpenSubtitles.org")
        self.btn_up.clicked.connect(self._upload)
        f.addRow(self.btn_up)
        note = QLabel(
            "Titlovi se otpremaju kao srpski, uz oznaku da su mašinski prevedeni. "
            "Pre otpremanja pogledaj prevod - loši prevodi se na sajtu brišu."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: gray;")
        f.addRow(note)
        split.addWidget(right)
        split.setSizes([300, 600])
        root.addWidget(split, 1)

        self.logbox = QPlainTextEdit()
        self.logbox.setReadOnly(True)
        self.logbox.setMaximumHeight(140)
        root.addWidget(self.logbox)

    def _log(self, s: str):
        self.logbox.appendPlainText(s)

    # ---------------- lista ----------------
    def _reload(self, keep: int = 0):
        self.jobs = opensubs.load_jobs(self.config_dir)
        self.list.clear()
        for j in self.jobs:
            done = [SCRIPTS[k] for k in j.uploaded if k in SCRIPTS]
            suffix = f"  ✓ {', '.join(done)}" if done else ""
            it = QListWidgetItem(f"{j.title}{suffix}")
            it.setToolTip(f"{j.movie_path}\nprevedeno: {j.created}")
            self.list.addItem(it)
        if self.jobs:
            self.list.setCurrentRow(min(keep, len(self.jobs) - 1))
        else:
            self._on_select(-1)

    def _job(self) -> opensubs.Job | None:
        i = self.list.currentRow()
        return self.jobs[i] if 0 <= i < len(self.jobs) else None

    def _on_select(self, _row: int):
        j = self._job()
        self.btn_up.setEnabled(j is not None)
        self.imdb.clear()
        self.movie_lbl.setText("-")
        if not j:
            self.file_lbl.setText("Nema sačuvanih prevoda. Prevedi film, pa se vrati ovde.")
            for c in self.cb.values():
                c.setEnabled(False)
                c.setChecked(False)
            return
        self.file_lbl.setText(j.movie_path)
        for k, c in self.cb.items():
            have = k in j.subs
            c.setEnabled(have)
            c.setChecked(have and k not in j.uploaded)
        self.cb_hd.setChecked(any(t in j.title.lower() for t in ("720p", "1080p", "2160p", "bluray", "web-dl")))

    def _delete(self):
        j = self._job()
        if not j:
            return
        if QMessageBox.question(self, self.app_title, f"Obrisati sačuvan prevod za:\n{j.title}?\n\n"
                                "(Briše se samo kopija za otpremanje, ne i video ni napravljeni MKV.)") \
                != QMessageBox.Yes:
            return
        shutil.rmtree(j.folder, ignore_errors=True)
        self._reload(self.list.currentRow())

    # ---------------- mreža ----------------
    def _ensure_login(self) -> opensubs.Client:
        if self.client and self.client.token:
            return self.client
        c = opensubs.Client(self.agent.text())
        with _Busy():
            c.login(self.user.text().strip(), self.pwd.text())
        self._log(f"Prijavljen kao {self.user.text().strip()}.")
        self.client = c
        return c

    def _guard(self, fn):
        try:
            return fn()
        except (opensubs.OpenSubsError, ValueError, FileNotFoundError) as e:
            self._log(f"GREŠKA: {e}")
            QMessageBox.warning(self, self.app_title, str(e))
            return None

    def _detect(self):
        j = self._job()
        if not j:
            return

        def go():
            c = self._ensure_login()
            k = next(iter(j.subs))
            with _Busy():
                r = c.try_upload(opensubs.sub_cd1(j.sub_path(k), Path(j.movie_path), j.movie_hash, j.movie_size, False))
            if r.movies:
                m = r.movies[0]
                self.imdb.setText(f"tt{int(m.imdb):07d}")
                self.movie_lbl.setText(m.label())
                self._log(f"Prepoznat film: {m.label()}")
            else:
                self.movie_lbl.setText("Film nije prepoznat po fajlu - potraži ga po nazivu.")
                self._log("Film nije prepoznat po hešu fajla.")

        self._guard(go)

    def _search(self):
        j = self._job()
        default = Path(j.movie_path).stem.replace(".", " ") if j else ""
        q, ok = QInputDialog.getText(self, self.app_title, "Naziv filma (engleski):", text=default)
        if not ok or not q.strip():
            return

        def go():
            c = self._ensure_login()
            with _Busy():
                movies = c.search_imdb(q.strip())
            if not movies:
                QMessageBox.information(self, self.app_title, "Ništa nije pronađeno.")
                return
            labels = [m.label() for m in movies[:30]]
            pick, ok2 = QInputDialog.getItem(self, self.app_title, "Izaberi film:", labels, 0, False)
            if ok2:
                m = movies[labels.index(pick)]
                self.imdb.setText(f"tt{int(m.imdb):07d}")
                self.movie_lbl.setText(m.label())

        self._guard(go)

    def _upload(self):
        j = self._job()
        if not j:
            return
        scripts = [k for k, c in self.cb.items() if c.isChecked() and c.isEnabled()]
        if not scripts:
            QMessageBox.information(self, self.app_title, "Izaberi latinicu i/ili ćirilicu.")
            return

        def go():
            imdb = opensubs.parse_imdb(self.imdb.text())
            again = [SCRIPTS[k] for k in scripts if k in j.uploaded]
            msg = (
                f"Otpremiti na opensubtitles.org?\n\n"
                f"Film: {self.movie_lbl.text()}  (IMDb tt{int(imdb):07d})\n"
                f"Fajl: {j.title}\n"
                f"Titlovi: {', '.join(SCRIPTS[k] for k in scripts)} - srpski, mašinski prevod (AI)\n"
            )
            if again:
                msg += f"\nPAŽNJA: već otpremljeno: {', '.join(again)}\n"
            msg += "\nOvo se ne može poništiti iz aplikacije."
            if QMessageBox.question(self, self.app_title, msg) != QMessageBox.Yes:
                return
            c = self._ensure_login()
            base = opensubs.baseinfo(
                imdb, Path(j.movie_path).stem, self.comment.text().strip(),
                self.cb_hi.isChecked(), self.cb_hd.isChecked(),
            )
            movie = Path(j.movie_path)
            for k in scripts:
                sp = j.sub_path(k)
                with _Busy():
                    tr = c.try_upload(opensubs.sub_cd1(sp, movie, j.movie_hash, j.movie_size, False))
                    if tr.already_in_db:
                        self._log(f"{SCRIPTS[k]}: ovaj titl već postoji na sajtu - preskačem.")
                        j.uploaded[k] = "već postoji"
                        continue
                    url = c.upload(base, opensubs.sub_cd1(sp, movie, j.movie_hash, j.movie_size, True))
                j.uploaded[k] = url or "otpremljeno"
                self._log(f"{SCRIPTS[k]}: otpremljeno {url}")
                j.save()
            j.save()
            self._reload(self.list.currentRow())
            QMessageBox.information(self, self.app_title, "Gotovo. Detalji su u dnu prozora.")

        self._guard(go)

    def done(self, r):
        if self.client:
            self.client.logout()
        super().done(r)
