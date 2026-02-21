"""
RBXChat_Launcher.py
───────────────────
Auto-updating launcher for RBXChat.
• Shows a splash screen while checking for updates
• Downloads & replaces main.py automatically if a newer version exists
• Launches RBXChat after update (or immediately if up-to-date)

Host your version manifest at the UPDATE_URL below.
"""

import sys
import os
import json
import urllib.request
import urllib.error
import subprocess
import threading
import hashlib
import shutil
import time

from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QProgressBar, QFrame
)
from PyQt5.QtCore import (
    Qt, QThread, pyqtSignal, QTimer, QPropertyAnimation,
    QEasingCurve, QPoint
)
from PyQt5.QtGui import (
    QColor, QFont, QPainter, QPainterPath,
    QLinearGradient, QPixmap, QIcon
)

# ═══════════════════════════════════════════════════════════════════
#  CONFIG  —  edit these two lines
# ═══════════════════════════════════════════════════════════════════
CURRENT_VERSION = "1.0.0"

# Host this JSON file publicly (GitHub raw, Pastebin raw, your own server…)
# Leave as "" to skip update checks (offline / dev mode)
UPDATE_URL = "https://raw.githubusercontent.com/Elite23311/-RBXChat-/refs/heads/main/version.json"
# e.g. UPDATE_URL = "https://raw.githubusercontent.com/YOU/rbxchat/main/version.json"

# ═══════════════════════════════════════════════════════════════════
#  Paths
# ═══════════════════════════════════════════════════════════════════
BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
MAIN_PY    = os.path.join(BASE_DIR, "main.py")
BACKUP_PY  = os.path.join(BASE_DIR, "main.py.bak")

# ═══════════════════════════════════════════════════════════════════
#  Colours
# ═══════════════════════════════════════════════════════════════════
BG      = "#0f1012"
BG2     = "#18191d"
BORDER  = "#2a2b33"
TEXT    = "#e6e8ef"
TEXT2   = "#8b909e"
ACCENT  = "#5865f2"
SUCCESS = "#3ba55d"
WARN    = "#faa61a"
DANGER  = "#ed4245"


# ═══════════════════════════════════════════════════════════════════
#  Update worker thread
# ═══════════════════════════════════════════════════════════════════
class UpdateWorker(QThread):
    status      = pyqtSignal(str)          # status text
    progress    = pyqtSignal(int)          # 0-100
    done        = pyqtSignal(bool, str)    # success, message
    new_version = pyqtSignal(str)          # version string if update found

    def run(self):
        # ── Step 1: Check manifest ────────────────────────────────
        self.status.emit("Checking for updates…")
        self.progress.emit(10)

        if not UPDATE_URL:
            self.status.emit("No update server configured — launching…")
            self.progress.emit(100)
            time.sleep(0.6)
            self.done.emit(True, "offline")
            return

        try:
            with urllib.request.urlopen(UPDATE_URL + "?t=" + str(int(time.time())), timeout=6) as r:
                manifest = json.loads(r.read().decode())
        except Exception as e:
            self.status.emit(f"Update check failed ({e}) — launching anyway…")
            self.progress.emit(100)
            time.sleep(0.8)
            self.done.emit(True, "skip")
            return

        self.progress.emit(30)
        remote_ver = manifest.get("version", "0.0.0")

        if not self._is_newer(remote_ver, CURRENT_VERSION):
            self.status.emit(f"Already up to date (v{CURRENT_VERSION}) — launching…")
            self.progress.emit(100)
            time.sleep(0.6)
            self.done.emit(True, "uptodate")
            return

        # ── Step 2: Download new main.py ──────────────────────────
        download_url = manifest.get("main_url", "")
        if not download_url:
            self.status.emit("Update found but no download URL — launching anyway…")
            self.progress.emit(100)
            time.sleep(0.6)
            self.done.emit(True, "skip")
            return

        self.new_version.emit(remote_ver)
        self.status.emit(f"Downloading v{remote_ver}…")
        self.progress.emit(40)

        try:
            tmp_path = MAIN_PY + ".tmp"
            def reporthook(count, block_size, total_size):
                if total_size > 0:
                    pct = int(40 + (count * block_size / total_size) * 50)
                    self.progress.emit(min(pct, 90))

            urllib.request.urlretrieve(download_url, tmp_path, reporthook)
        except Exception as e:
            self.status.emit(f"Download failed: {e} — launching old version…")
            self.progress.emit(100)
            time.sleep(0.8)
            self.done.emit(True, "skip")
            return

        # ── Step 3: Verify checksum (optional) ───────────────────
        self.progress.emit(92)
        expected_sha = manifest.get("sha256", "")
        if expected_sha:
            self.status.emit("Verifying download…")
            with open(tmp_path, "rb") as f:
                actual_sha = hashlib.sha256(f.read()).hexdigest()
            if actual_sha != expected_sha:
                os.remove(tmp_path)
                self.status.emit("Checksum mismatch — update aborted, launching old version…")
                self.progress.emit(100)
                time.sleep(0.8)
                self.done.emit(True, "skip")
                return

        # ── Step 4: Replace main.py ───────────────────────────────
        self.status.emit("Applying update…")
        if os.path.exists(MAIN_PY):
            shutil.copy2(MAIN_PY, BACKUP_PY)   # keep backup
        shutil.move(tmp_path, MAIN_PY)

        self.progress.emit(100)
        self.status.emit(f"Updated to v{remote_ver}! Launching…")
        time.sleep(0.8)
        self.done.emit(True, "updated")

    @staticmethod
    def _is_newer(a: str, b: str) -> bool:
        try:
            return [int(x) for x in a.split(".")] > [int(x) for x in b.split(".")]
        except Exception:
            return False


# ═══════════════════════════════════════════════════════════════════
#  Splash / Launcher window
# ═══════════════════════════════════════════════════════════════════
class LauncherWindow(QWidget):
    def __init__(self):
        super().__init__()
        self._drag_pos = None
        self._new_ver  = None
        self._setup_window()
        self._build_ui()
        self._start_worker()

    def _setup_window(self):
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(380, 240)
        # Centre on screen
        screen = QApplication.primaryScreen().availableGeometry()
        self.move(
            (screen.width()  - self.width())  // 2,
            (screen.height() - self.height()) // 2,
        )

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)

        card = QFrame()
        card.setStyleSheet(
            f"QFrame {{ background: {BG}; border: 1px solid {BORDER};"
            f"border-radius: 16px; }}"
        )
        outer.addWidget(card)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(0)

        # ── Logo row ──────────────────────────────────────────────
        logo_row = QHBoxLayout()
        logo_row.setSpacing(10)

        logo_box = QLabel()
        logo_box.setFixedSize(42, 42)
        logo_box.setStyleSheet(
            f"background: {ACCENT}; border-radius: 12px;"
        )
        logo_box.setAlignment(Qt.AlignCenter)
        logo_box.setText("💬")
        logo_box.setFont(QFont("Segoe UI Emoji", 20))
        logo_row.addWidget(logo_box)

        title_col = QVBoxLayout()
        title_col.setSpacing(2)

        name_lbl = QLabel("RBXChat")
        name_lbl.setFont(QFont("Segoe UI", 18, QFont.Bold))
        name_lbl.setStyleSheet(f"color: {TEXT};")
        title_col.addWidget(name_lbl)

        self.ver_lbl = QLabel(f"v{CURRENT_VERSION}  •  Roblox Overlay")
        self.ver_lbl.setFont(QFont("Segoe UI", 8))
        self.ver_lbl.setStyleSheet(f"color: {TEXT2};")
        title_col.addWidget(self.ver_lbl)

        logo_row.addLayout(title_col)
        logo_row.addStretch()

        # Close button
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(22, 22)
        close_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; border: none; color: {TEXT2};"
            f"font-size: 12px; border-radius: 11px; }}"
            f"QPushButton:hover {{ background: {DANGER}; color: white; }}"
        )
        close_btn.clicked.connect(self.close)
        logo_row.addWidget(close_btn, 0, Qt.AlignTop)

        layout.addLayout(logo_row)
        layout.addSpacing(22)

        # ── Status label ──────────────────────────────────────────
        self.status_lbl = QLabel("Initialising…")
        self.status_lbl.setFont(QFont("Segoe UI", 9))
        self.status_lbl.setStyleSheet(f"color: {TEXT2};")
        self.status_lbl.setAlignment(Qt.AlignLeft)
        layout.addWidget(self.status_lbl)
        layout.addSpacing(8)

        # ── Progress bar ──────────────────────────────────────────
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFixedHeight(6)
        self.progress.setTextVisible(False)
        self.progress.setStyleSheet(
            f"QProgressBar {{ background: {BG2}; border: none; border-radius: 3px; }}"
            f"QProgressBar::chunk {{ background: {ACCENT}; border-radius: 3px; }}"
        )
        layout.addWidget(self.progress)
        layout.addSpacing(18)

        # ── Update badge (hidden until update found) ──────────────
        self.update_badge = QLabel("")
        self.update_badge.setFont(QFont("Segoe UI", 8))
        self.update_badge.setStyleSheet(
            f"color: {WARN}; background: {WARN}22;"
            f"border: 1px solid {WARN}55; border-radius: 6px; padding: 3px 8px;"
        )
        self.update_badge.setVisible(False)
        layout.addWidget(self.update_badge)

        layout.addStretch()

        # ── Bottom row ────────────────────────────────────────────
        bottom = QHBoxLayout()
        bottom.setSpacing(0)

        self.footer_lbl = QLabel("Checking for updates…")
        self.footer_lbl.setFont(QFont("Segoe UI", 7))
        self.footer_lbl.setStyleSheet(f"color: {TEXT2}44;")
        bottom.addWidget(self.footer_lbl)
        bottom.addStretch()

        self.skip_btn = QPushButton("Skip Update")
        self.skip_btn.setFixedHeight(22)
        self.skip_btn.setFont(QFont("Segoe UI", 7, QFont.Bold))
        self.skip_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; border: 1px solid {BORDER};"
            f"border-radius: 6px; color: {TEXT2}; padding: 0 10px; }}"
            f"QPushButton:hover {{ color: {TEXT}; border-color: {TEXT2}; }}"
        )
        self.skip_btn.setVisible(False)
        self.skip_btn.clicked.connect(self._skip_update)
        bottom.addWidget(self.skip_btn)

        layout.addLayout(bottom)

        # Draggable
        card.mousePressEvent   = self._drag_start
        card.mouseMoveEvent    = self._drag_move

    # ── Worker ────────────────────────────────────────────────────
    def _start_worker(self):
        self.worker = UpdateWorker()
        self.worker.status.connect(self._on_status)
        self.worker.progress.connect(self._on_progress)
        self.worker.done.connect(self._on_done)
        self.worker.new_version.connect(self._on_new_version)
        self.worker.start()

    def _on_status(self, text):
        self.status_lbl.setText(text)
        self.footer_lbl.setText(text)

    def _on_progress(self, val):
        self.progress.setValue(val)
        # Change bar colour near done
        if val >= 90:
            self.progress.setStyleSheet(
                f"QProgressBar {{ background: {BG2}; border: none; border-radius: 3px; }}"
                f"QProgressBar::chunk {{ background: {SUCCESS}; border-radius: 3px; }}"
            )

    def _on_new_version(self, ver):
        self._new_ver = ver
        self.update_badge.setText(f"🆕 New version found: v{ver}")
        self.update_badge.setVisible(True)
        self.skip_btn.setVisible(True)

    def _on_done(self, ok, reason):
        if ok:
            self._launch_app()
        else:
            self.status_lbl.setText("Something went wrong. Please relaunch.")
            self.status_lbl.setStyleSheet(f"color: {DANGER};")

    def _skip_update(self):
        self.worker.terminate()
        self.status_lbl.setText("Skipping update — launching…")
        QTimer.singleShot(600, self._launch_app)

    def _launch_app(self):
        """Start main.py as a detached process then close launcher."""
        try:
            python = sys.executable  # same Python running the launcher
            # Use pythonw to avoid console window on Windows
            pythonw = python.replace("python.exe", "pythonw.exe")
            exe = pythonw if os.path.exists(pythonw) else python

            subprocess.Popen(
                [exe, MAIN_PY],
                cwd=BASE_DIR,
                creationflags=subprocess.DETACHED_PROCESS
                              | subprocess.CREATE_NEW_PROCESS_GROUP
                if sys.platform == "win32" else 0,
                close_fds=True,
            )
        except Exception as e:
            self.status_lbl.setText(f"Launch failed: {e}")
            self.status_lbl.setStyleSheet(f"color: {DANGER};")
            return

        # Small delay so the app has time to start, then close launcher
        QTimer.singleShot(400, self.close)

    # ── Drag ──────────────────────────────────────────────────────
    def _drag_start(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPos() - self.frameGeometry().topLeft()

    def _drag_move(self, event):
        if event.buttons() == Qt.LeftButton and self._drag_pos:
            self.move(event.globalPos() - self._drag_pos)


# ═══════════════════════════════════════════════════════════════════
#  Entry point
# ═══════════════════════════════════════════════════════════════════
def main():
    app = QApplication(sys.argv)
    app.setApplicationName("RBXChat Launcher")
    app.setQuitOnLastWindowClosed(True)

    window = LauncherWindow()
    window.show()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
