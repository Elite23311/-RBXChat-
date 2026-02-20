"""
RBXChat — Roblox Overlay  v1.0.0
Always-on-top PyQt5 chat overlay with Firebase Realtime Database
"""

import sys
import os
import json
import threading
import time
import base64
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime
from io import BytesIO

from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QLineEdit, QTextEdit, QStackedWidget, QFileDialog,
    QColorDialog, QKeySequenceEdit, QFrame, QScrollArea, QSizeGrip,
    QGraphicsDropShadowEffect, QMessageBox
)
from PyQt5.QtCore import (
    Qt, QThread, pyqtSignal, QTimer, QPoint, QSize,
    QPropertyAnimation, QEasingCurve, QRect, QSettings, pyqtSlot
)
from PyQt5.QtGui import (
    QColor, QFont, QFontDatabase, QPainter, QPainterPath,
    QPixmap, QImage, QIcon, QKeySequence, QPalette, QCursor
)

import firebase_db as fb
import config as cfg

# ── Version ────────────────────────────────────────────────────────────────────
VERSION = "1.0.0"
UPDATE_CHECK_URL = ""   # e.g. "https://raw.githubusercontent.com/you/rbxchat/main/version.json"

# ── Palette ────────────────────────────────────────────────────────────────────
C = {
    "bg":      "#16171a",
    "bg2":     "#1e1f23",
    "bg3":     "#26272d",
    "border":  "#33343c",
    "text":    "#e6e8ef",
    "text2":   "#8b909e",
    "danger":  "#ed4245",
    "success": "#3ba55d",
    "warn":    "#faa61a",
}

def accent():
    return cfg.load_settings().get("theme", "#5865f2")

def apply_qss(widget, qss):
    widget.setStyleSheet(qss)


# ══════════════════════════════════════════════════════════════════════════════
#  Firebase listener thread
# ══════════════════════════════════════════════════════════════════════════════
class FirebaseThread(QThread):
    message_received = pyqtSignal(dict)
    connection_status = pyqtSignal(bool)

    def __init__(self):
        super().__init__()
        self._running = True
        self._last_key = None

    def run(self):
        retry = 0
        while self._running:
            try:
                messages = fb.get_recent_messages(limit=50)
                if messages:
                    for key, msg in messages.items():
                        if key != self._last_key:
                            self._last_key = key
                            self.message_received.emit(msg)
                self.connection_status.emit(True)
                retry = 0
                # Poll every 2 seconds
                for _ in range(20):
                    if not self._running:
                        return
                    time.sleep(0.1)

                # Now check for new messages since last key
                if self._last_key:
                    new = fb.get_messages_after(self._last_key)
                    if new:
                        for key, msg in new.items():
                            self._last_key = key
                            self.message_received.emit(msg)

            except Exception as e:
                self.connection_status.emit(False)
                retry = min(retry + 1, 10)
                time.sleep(retry * 0.5)

    def stop(self):
        self._running = False
        self.wait()


# ══════════════════════════════════════════════════════════════════════════════
#  Update checker thread
# ══════════════════════════════════════════════════════════════════════════════
class UpdateThread(QThread):
    update_available = pyqtSignal(str, str, str)  # version, url, notes

    def run(self):
        if not UPDATE_CHECK_URL:
            return
        try:
            with urllib.request.urlopen(UPDATE_CHECK_URL, timeout=5) as r:
                data = json.loads(r.read())
            remote = data.get("version", "0.0.0")
            if self._is_newer(remote, VERSION):
                self.update_available.emit(remote, data.get("url",""), data.get("notes",""))
        except Exception:
            pass

    @staticmethod
    def _is_newer(a, b):
        pa = [int(x) for x in a.split(".")]
        pb = [int(x) for x in b.split(".")]
        return pa > pb


# ══════════════════════════════════════════════════════════════════════════════
#  Rounded avatar label
# ══════════════════════════════════════════════════════════════════════════════
class AvatarLabel(QLabel):
    def __init__(self, size=32, parent=None):
        super().__init__(parent)
        self._size = size
        self.setFixedSize(size, size)
        self._pixmap = None

    def setAvatar(self, pixmap_or_b64: str):
        if not pixmap_or_b64:
            self._pixmap = None
        elif pixmap_or_b64.startswith("data:image") or len(pixmap_or_b64) > 200:
            # base64
            try:
                raw = pixmap_or_b64.split(",", 1)[-1]
                img_data = base64.b64decode(raw)
                pm = QPixmap()
                pm.loadFromData(img_data)
                self._pixmap = pm.scaled(self._size, self._size,
                                         Qt.KeepAspectRatioByExpanding,
                                         Qt.SmoothTransformation)
            except Exception:
                self._pixmap = None
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        path = QPainterPath()
        path.addEllipse(0, 0, self._size, self._size)
        painter.setClipPath(path)

        if self._pixmap:
            painter.drawPixmap(0, 0, self._pixmap)
        else:
            painter.fillPath(path, QColor(C["bg3"]))
            painter.setPen(QColor(C["text2"]))
            painter.setFont(QFont("Segoe UI", self._size // 3))
            painter.drawText(self.rect(), Qt.AlignCenter, "?")

        # Border ring
        painter.setClipping(False)
        painter.setPen(QColor(C["border"]))
        painter.drawEllipse(1, 1, self._size-2, self._size-2)


# ══════════════════════════════════════════════════════════════════════════════
#  Single message bubble
# ══════════════════════════════════════════════════════════════════════════════
class MessageBubble(QFrame):
    def __init__(self, data: dict, accent_color: str, parent=None):
        super().__init__(parent)
        self.setObjectName("MsgBubble")

        row = QHBoxLayout(self)
        row.setContentsMargins(6, 4, 6, 4)
        row.setSpacing(8)
        row.setAlignment(Qt.AlignTop)

        # Avatar
        av = AvatarLabel(28)
        av.setAvatar(data.get("avatar", ""))
        row.addWidget(av, 0, Qt.AlignTop)

        # Body
        body = QVBoxLayout()
        body.setSpacing(2)
        body.setContentsMargins(0, 0, 0, 0)

        name_row = QHBoxLayout()
        name_row.setSpacing(6)
        name_lbl = QLabel(data.get("username", "Unknown"))
        name_lbl.setFont(QFont("Segoe UI", 9, QFont.Bold))
        name_lbl.setStyleSheet(f"color: {accent_color};")
        name_row.addWidget(name_lbl)

        ts = data.get("timestamp", "")
        if ts:
            try:
                dt = datetime.fromtimestamp(float(ts)/1000) if isinstance(ts, (int,float)) else datetime.fromisoformat(str(ts))
                ts_str = dt.strftime("%H:%M")
            except Exception:
                ts_str = str(ts)[:5]
        else:
            ts_str = datetime.now().strftime("%H:%M")

        time_lbl = QLabel(ts_str)
        time_lbl.setFont(QFont("Segoe UI", 7))
        time_lbl.setStyleSheet(f"color: {C['text2']};")
        name_row.addWidget(time_lbl)
        name_row.addStretch()
        body.addLayout(name_row)

        text_lbl = QLabel(data.get("text", ""))
        text_lbl.setFont(QFont("Segoe UI", 9))
        text_lbl.setStyleSheet(
            f"color: {C['text']}; background: {C['bg2']};"
            f"border: 1px solid {C['border']}; border-radius: 0 7px 7px 7px;"
            f"padding: 4px 8px;"
        )
        text_lbl.setWordWrap(True)
        text_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        body.addWidget(text_lbl)
        row.addLayout(body)

        self.setStyleSheet("QFrame#MsgBubble { background: transparent; }")


# ══════════════════════════════════════════════════════════════════════════════
#  Messages Panel
# ══════════════════════════════════════════════════════════════════════════════
class MessagesPanel(QWidget):
    send_requested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._seen = set()
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Scroll area
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll.setStyleSheet(
            f"QScrollArea {{ border: none; background: {C['bg']}; }}"
            f"QScrollBar:vertical {{ background: transparent; width: 4px; }}"
            f"QScrollBar::handle:vertical {{ background: {C['border']}; border-radius: 2px; }}"
            f"QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}"
        )
        self.msg_container = QWidget()
        self.msg_container.setStyleSheet(f"background: {C['bg']};")
        self.msg_layout = QVBoxLayout(self.msg_container)
        self.msg_layout.setContentsMargins(0, 4, 0, 4)
        self.msg_layout.setSpacing(0)
        self.msg_layout.addStretch()
        self.scroll.setWidget(self.msg_container)
        layout.addWidget(self.scroll)

        # Input row
        input_frame = QFrame()
        input_frame.setStyleSheet(
            f"background: {C['bg']}; border-top: 1px solid {C['border']};"
        )
        inp_row = QHBoxLayout(input_frame)
        inp_row.setContentsMargins(8, 6, 8, 6)
        inp_row.setSpacing(6)

        self.input = QLineEdit()
        self.input.setPlaceholderText("Message…")
        self.input.setMaxLength(300)
        self.input.setFont(QFont("Segoe UI", 9))
        self.input.setStyleSheet(
            f"QLineEdit {{ background: {C['bg3']}; border: 1px solid {C['border']};"
            f"border-radius: 8px; color: {C['text']}; padding: 5px 10px; }}"
            f"QLineEdit:focus {{ border-color: {accent()}; }}"
        )
        self.input.returnPressed.connect(self._send)
        inp_row.addWidget(self.input)

        self.send_btn = QPushButton("➤")
        self.send_btn.setFixedSize(32, 28)
        self.send_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.send_btn.clicked.connect(self._send)
        self._style_send_btn()
        inp_row.addWidget(self.send_btn)

        layout.addWidget(input_frame)

    def _style_send_btn(self):
        self.send_btn.setStyleSheet(
            f"QPushButton {{ background: {accent()}; border: none;"
            f"border-radius: 8px; color: white; font-size: 13px; }}"
            f"QPushButton:hover {{ opacity: 0.85; }}"
            f"QPushButton:pressed {{ background: {C['bg3']}; }}"
        )

    def _send(self):
        text = self.input.text().strip()
        if not text:
            return
        self.input.clear()
        self.send_requested.emit(text)

    def add_message(self, data: dict):
        # Deduplicate
        uid = data.get("_key") or f"{data.get('username')}|{data.get('text')}|{data.get('timestamp','')}"
        if uid in self._seen:
            return
        self._seen.add(uid)

        bubble = MessageBubble(data, accent())
        # Insert before the trailing stretch
        self.msg_layout.insertWidget(self.msg_layout.count() - 1, bubble)
        QTimer.singleShot(50, self._scroll_bottom)

    def _scroll_bottom(self):
        sb = self.scroll.verticalScrollBar()
        sb.setValue(sb.maximum())

    def refresh_accent(self):
        self._style_send_btn()
        self.input.setStyleSheet(
            f"QLineEdit {{ background: {C['bg3']}; border: 1px solid {C['border']};"
            f"border-radius: 8px; color: {C['text']}; padding: 5px 10px; }}"
            f"QLineEdit:focus {{ border-color: {accent()}; }}"
        )


# ══════════════════════════════════════════════════════════════════════════════
#  Profile Panel
# ══════════════════════════════════════════════════════════════════════════════
class ProfilePanel(QWidget):
    profile_saved = pyqtSignal(dict)
    logged_out    = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._avatar_b64 = ""
        self._build_ui()
        self.load()

    def _field_label(self, text):
        lbl = QLabel(text.upper())
        lbl.setFont(QFont("Segoe UI", 7, QFont.Bold))
        lbl.setStyleSheet(f"color: {C['text2']}; letter-spacing: 1px;")
        return lbl

    def _field_input(self, placeholder="", multi=False):
        if multi:
            w = QTextEdit()
            w.setPlaceholderText(placeholder)
            w.setFixedHeight(54)
            w.setFont(QFont("Segoe UI", 9))
        else:
            w = QLineEdit()
            w.setPlaceholderText(placeholder)
            w.setFont(QFont("Segoe UI", 9))
        style = (
            f"background: {C['bg3']}; border: 1px solid {C['border']};"
            f"border-radius: 8px; color: {C['text']}; padding: 5px 10px;"
        )
        w.setStyleSheet(style)
        return w

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        # Avatar area
        av_frame = QFrame()
        av_frame.setStyleSheet(f"border-bottom: 1px solid {C['border']}; background: transparent;")
        av_col = QVBoxLayout(av_frame)
        av_col.setAlignment(Qt.AlignHCenter)
        av_col.setSpacing(6)
        av_col.setContentsMargins(0, 0, 0, 10)

        self.avatar_lbl = AvatarLabel(62)
        av_col.addWidget(self.avatar_lbl, 0, Qt.AlignHCenter)

        change_av_btn = QPushButton("Change Avatar")
        change_av_btn.setFixedHeight(24)
        change_av_btn.setFont(QFont("Segoe UI", 8))
        change_av_btn.setCursor(QCursor(Qt.PointingHandCursor))
        change_av_btn.setStyleSheet(
            f"QPushButton {{ background: {C['bg3']}; border: 1px solid {C['border']};"
            f"border-radius: 6px; color: {C['text2']}; padding: 0 10px; }}"
            f"QPushButton:hover {{ color: {C['text']}; }}"
        )
        change_av_btn.clicked.connect(self._pick_avatar)
        av_col.addWidget(change_av_btn, 0, Qt.AlignHCenter)

        self.disp_name = QLabel("RobloxPlayer")
        self.disp_name.setFont(QFont("Segoe UI", 11, QFont.Bold))
        self.disp_name.setStyleSheet(f"color: {C['text']}; border: none;")
        self.disp_name.setAlignment(Qt.AlignHCenter)
        av_col.addWidget(self.disp_name)

        self.disp_bio = QLabel("No bio set.")
        self.disp_bio.setFont(QFont("Segoe UI", 8))
        self.disp_bio.setStyleSheet(f"color: {C['text2']}; border: none;")
        self.disp_bio.setAlignment(Qt.AlignHCenter)
        self.disp_bio.setWordWrap(True)
        av_col.addWidget(self.disp_bio)

        root.addWidget(av_frame)

        # Username
        root.addWidget(self._field_label("Username"))
        self.username_input = self._field_input("YourRobloxName")
        self.username_input.setMaxLength(40)
        root.addWidget(self.username_input)

        # Bio
        root.addWidget(self._field_label("Bio"))
        self.bio_input = self._field_input("Tell everyone about yourself…", multi=True)
        root.addWidget(self.bio_input)

        root.addStretch()

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self.save_btn = QPushButton("Save Profile")
        self.save_btn.setFixedHeight(30)
        self.save_btn.setFont(QFont("Segoe UI", 9, QFont.Bold))
        self.save_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.save_btn.clicked.connect(self._save)
        btn_row.addWidget(self.save_btn)

        logout_btn = QPushButton("Logout")
        logout_btn.setFixedHeight(30)
        logout_btn.setFont(QFont("Segoe UI", 9, QFont.Bold))
        logout_btn.setCursor(QCursor(Qt.PointingHandCursor))
        logout_btn.setStyleSheet(
            f"QPushButton {{ background: {C['danger']}; border: none;"
            f"border-radius: 8px; color: white; }}"
            f"QPushButton:hover {{ background: #c0393b; }}"
        )
        logout_btn.clicked.connect(self._logout)
        btn_row.addWidget(logout_btn)
        root.addLayout(btn_row)

        self.refresh_accent()

    def refresh_accent(self):
        a = accent()
        self.save_btn.setStyleSheet(
            f"QPushButton {{ background: {a}; border: none;"
            f"border-radius: 8px; color: white; }}"
            f"QPushButton:hover {{ background: {a}cc; }}"
        )

    def _pick_avatar(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose Avatar", "", "Images (*.png *.jpg *.jpeg *.gif *.webp)")
        if not path:
            return
        with open(path, "rb") as f:
            raw = f.read()
        ext = os.path.splitext(path)[1].lower().strip(".")
        mime = {"jpg":"jpeg","jpeg":"jpeg","png":"png","gif":"gif","webp":"webp"}.get(ext,"png")
        self._avatar_b64 = f"data:image/{mime};base64," + base64.b64encode(raw).decode()
        pm = QPixmap(path).scaled(62, 62, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        self.avatar_lbl.setAvatar(self._avatar_b64)

    def _save(self):
        username = self.username_input.text().strip() or "RobloxPlayer"
        bio      = self.bio_input.toPlainText().strip()
        profile  = {"username": username, "bio": bio, "avatar": self._avatar_b64}
        cfg.save_profile(profile)
        self.disp_name.setText(username)
        self.disp_bio.setText(bio or "No bio set.")
        self.profile_saved.emit(profile)

    def _logout(self):
        reply = QMessageBox.question(self, "Logout", "Clear local profile and logout?",
                                     QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            cfg.save_profile({"username":"RobloxPlayer","bio":"","avatar":""})
            self.username_input.setText("")
            self.bio_input.setPlainText("")
            self._avatar_b64 = ""
            self.avatar_lbl.setAvatar("")
            self.disp_name.setText("RobloxPlayer")
            self.disp_bio.setText("No bio set.")
            self.logged_out.emit()

    def load(self):
        p = cfg.load_profile()
        if not p:
            return
        self.username_input.setText(p.get("username",""))
        self.bio_input.setPlainText(p.get("bio",""))
        self._avatar_b64 = p.get("avatar","")
        self.avatar_lbl.setAvatar(self._avatar_b64)
        self.disp_name.setText(p.get("username","RobloxPlayer"))
        self.disp_bio.setText(p.get("bio","") or "No bio set.")

    def get_profile(self):
        return cfg.load_profile() or {"username":"RobloxPlayer","bio":"","avatar":""}


# ══════════════════════════════════════════════════════════════════════════════
#  Settings Panel
# ══════════════════════════════════════════════════════════════════════════════
class SettingsPanel(QWidget):
    settings_saved  = pyqtSignal(dict)
    theme_changed   = pyqtSignal(str)

    PRESETS = [
        ("#5865f2","Blurple"), ("#eb459e","Pink"), ("#3ba55d","Green"),
        ("#faa61a","Gold"),    ("#ed4245","Red"),  ("#00b4d8","Cyan"),
        ("#9b59b6","Purple"),  ("#ff8c00","Orange"),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._capture_key = False
        self._build_ui()
        self.load()

    def _section_title(self, text):
        lbl = QLabel(text.upper())
        lbl.setFont(QFont("Segoe UI", 7, QFont.Bold))
        lbl.setStyleSheet(f"color: {C['text2']}; letter-spacing: 1px;")
        return lbl

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(14)

        # ── Theme colour ──
        root.addWidget(self._section_title("Theme Colour"))
        swatch_row = QHBoxLayout()
        swatch_row.setSpacing(6)
        self._swatches = []
        for color, name in self.PRESETS:
            btn = QPushButton()
            btn.setFixedSize(22, 22)
            btn.setToolTip(name)
            btn.setCursor(QCursor(Qt.PointingHandCursor))
            btn.setStyleSheet(
                f"QPushButton {{ background: {color}; border-radius: 11px; border: 2px solid transparent; }}"
                f"QPushButton:hover {{ border-color: white; }}"
            )
            btn.clicked.connect(lambda _, c=color: self._pick_preset(c))
            swatch_row.addWidget(btn)
            self._swatches.append((btn, color))

        custom_btn = QPushButton("＋")
        custom_btn.setFixedSize(22, 22)
        custom_btn.setToolTip("Custom colour")
        custom_btn.setCursor(QCursor(Qt.PointingHandCursor))
        custom_btn.setStyleSheet(
            f"QPushButton {{ background: {C['bg3']}; border: 2px dashed {C['border']};"
            f"border-radius: 11px; color: {C['text2']}; font-size: 10px; }}"
        )
        custom_btn.clicked.connect(self._pick_custom)
        swatch_row.addWidget(custom_btn)
        swatch_row.addStretch()
        root.addLayout(swatch_row)

        # ── Bind key ──
        root.addWidget(self._section_title("Toggle Overlay Key"))
        key_row = QHBoxLayout()
        key_row.setSpacing(8)
        self.key_display = QLabel("F9")
        self.key_display.setFont(QFont("Courier New", 12, QFont.Bold))
        self.key_display.setAlignment(Qt.AlignCenter)
        self.key_display.setFixedHeight(28)
        self.key_display.setStyleSheet(
            f"background: {C['bg3']}; border: 1px solid {C['border']};"
            f"border-radius: 8px; color: {C['text']}; padding: 0 12px;"
        )
        key_row.addWidget(self.key_display, 1)

        self.set_key_btn = QPushButton("Set Key")
        self.set_key_btn.setFixedHeight(28)
        self.set_key_btn.setFont(QFont("Segoe UI", 8, QFont.Bold))
        self.set_key_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.set_key_btn.setStyleSheet(
            f"QPushButton {{ background: {C['bg3']}; border: 1px solid {C['border']};"
            f"border-radius: 8px; color: {C['text']}; padding: 0 12px; }}"
            f"QPushButton:hover {{ background: {C['border']}; }}"
        )
        self.set_key_btn.clicked.connect(self._start_key_capture)
        key_row.addWidget(self.set_key_btn)
        root.addLayout(key_row)

        # ── Version / update ──
        root.addWidget(self._section_title("Version"))
        self.ver_box = QLabel(f"RBXChat v{VERSION} — checking for updates…")
        self.ver_box.setFont(QFont("Segoe UI", 8))
        self.ver_box.setWordWrap(True)
        self.ver_box.setStyleSheet(
            f"background: {C['bg3']}; border: 1px solid {C['border']};"
            f"border-radius: 8px; color: {C['text2']}; padding: 8px 10px;"
        )
        root.addWidget(self.ver_box)

        # ── Firebase info ──
        root.addWidget(self._section_title("Database"))
        db_box = QLabel(f"🔥 Firebase RTDB: <b>{fb.DB_URL}</b>")
        db_box.setFont(QFont("Segoe UI", 8))
        db_box.setWordWrap(True)
        db_box.setStyleSheet(
            f"background: {C['bg3']}; border: 1px solid {C['border']};"
            f"border-radius: 8px; color: {C['text2']}; padding: 8px 10px;"
        )
        root.addWidget(db_box)

        root.addStretch()

        self.save_btn = QPushButton("Save Settings")
        self.save_btn.setFixedHeight(30)
        self.save_btn.setFont(QFont("Segoe UI", 9, QFont.Bold))
        self.save_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.save_btn.clicked.connect(self._save)
        self._style_save_btn()
        root.addWidget(self.save_btn)

    def _style_save_btn(self):
        a = accent()
        self.save_btn.setStyleSheet(
            f"QPushButton {{ background: {a}; border: none;"
            f"border-radius: 8px; color: white; }}"
            f"QPushButton:hover {{ background: {a}cc; }}"
        )

    def _pick_preset(self, color):
        self._set_accent(color)

    def _pick_custom(self):
        color = QColorDialog.getColor(QColor(accent()), self, "Choose Accent Colour")
        if color.isValid():
            self._set_accent(color.name())

    def _set_accent(self, color):
        s = cfg.load_settings()
        s["theme"] = color
        # Don't save yet — just preview
        cfg._preview_theme = color
        self.theme_changed.emit(color)

    def _start_key_capture(self):
        self._capture_key = True
        self.set_key_btn.setText("Press any key…")
        self.set_key_btn.setStyleSheet(
            f"QPushButton {{ background: {accent()}; border: none;"
            f"border-radius: 8px; color: white; padding: 0 12px; }}"
        )
        self.setFocus()

    def keyPressEvent(self, event):
        if self._capture_key:
            key_name = QKeySequence(event.key()).toString()
            if key_name:
                s = cfg.load_settings()
                s["bind_key"] = key_name
                cfg.save_settings(s)
                self.key_display.setText(key_name)
            self._capture_key = False
            self.set_key_btn.setText("Set Key")
            self.set_key_btn.setStyleSheet(
                f"QPushButton {{ background: {C['bg3']}; border: 1px solid {C['border']};"
                f"border-radius: 8px; color: {C['text']}; padding: 0 12px; }}"
                f"QPushButton:hover {{ background: {C['border']}; }}"
            )
        else:
            super().keyPressEvent(event)

    def _save(self):
        s = cfg.load_settings()
        if hasattr(cfg, "_preview_theme"):
            s["theme"] = cfg._preview_theme
            del cfg._preview_theme
        cfg.save_settings(s)
        self._style_save_btn()
        self.settings_saved.emit(s)

    def load(self):
        s = cfg.load_settings()
        self.key_display.setText(s.get("bind_key", "F9"))

    def set_update_status(self, text):
        self.ver_box.setText(text)

    def refresh_accent(self):
        self._style_save_btn()


# ══════════════════════════════════════════════════════════════════════════════
#  Main Overlay Window
# ══════════════════════════════════════════════════════════════════════════════
class RBXChatOverlay(QWidget):
    def __init__(self):
        super().__init__()
        self._drag_pos = None
        self._visible  = True

        self._setup_window()
        self._build_ui()
        self._load_profile()
        self._start_firebase()
        self._start_update_check()
        self._setup_hotkey_timer()

    # ── Window flags ──────────────────────────────────────────────────────────
    def _setup_window(self):
        self.setWindowFlags(
            Qt.FramelessWindowHint       |   # No native title bar
            Qt.WindowStaysOnTopHint      |   # Always above Roblox
            Qt.Tool                          # No taskbar entry
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)   # Don't steal focus from Roblox
        self.setMinimumSize(300, 380)
        self.resize(316, 460)

        # Restore saved position
        s = cfg.load_settings()
        pos = s.get("pos")
        if pos:
            self.move(pos[0], pos[1])
        else:
            # Default bottom-right
            screen = QApplication.primaryScreen().availableGeometry()
            self.move(screen.width() - 336, screen.height() - 480)

    # ── UI ────────────────────────────────────────────────────────────────────
    def _build_ui(self):
        # Root with rounded corners + shadow
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(0)

        self.card = QFrame()
        self.card.setObjectName("Card")
        self._style_card()

        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(24)
        shadow.setColor(QColor(0, 0, 0, 180))
        shadow.setOffset(0, 6)
        self.card.setGraphicsEffect(shadow)

        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.setSpacing(0)

        # Title bar
        self._build_titlebar(card_layout)

        # Update banner
        self.update_banner = QLabel("")
        self.update_banner.setFixedHeight(0)
        self.update_banner.setFont(QFont("Segoe UI", 8))
        self.update_banner.setStyleSheet(
            f"background: #4a2600; color: #ffd580; padding: 0 10px;"
            f"border-bottom: 1px solid {C['warn']};"
        )
        card_layout.addWidget(self.update_banner)

        # Nav
        self._build_nav(card_layout)

        # Panels
        self.stack = QStackedWidget()
        self.stack.setStyleSheet(f"background: {C['bg']};")

        self.msg_panel  = MessagesPanel()
        self.prof_panel = ProfilePanel()
        self.set_panel  = SettingsPanel()

        self.stack.addWidget(self.msg_panel)   # 0
        self.stack.addWidget(self.prof_panel)  # 1
        self.stack.addWidget(self.set_panel)   # 2

        card_layout.addWidget(self.stack)

        root.addWidget(self.card)

        # Wire signals
        self.msg_panel.send_requested.connect(self._send_message)
        self.prof_panel.profile_saved.connect(self._on_profile_saved)
        self.prof_panel.logged_out.connect(self._on_logout)
        self.set_panel.settings_saved.connect(self._on_settings_saved)
        self.set_panel.theme_changed.connect(self._apply_theme_preview)

        # Welcome message
        self.msg_panel.add_message({
            "username": "RBXChat",
            "text": f"v{VERSION} — Connecting to Firebase…",
            "avatar": "",
            "timestamp": datetime.now().isoformat(),
        })

    def _style_card(self):
        self.card.setStyleSheet(
            f"QFrame#Card {{ background: {C['bg']}; border: 1px solid {C['border']};"
            f"border-radius: 14px; }}"
        )

    def _build_titlebar(self, layout):
        bar = QFrame()
        bar.setObjectName("TitleBar")
        bar.setFixedHeight(42)
        bar.setStyleSheet(
            f"QFrame#TitleBar {{ background: {C['bg2']}; border-radius: 14px 14px 0 0;"
            f"border-bottom: 1px solid {C['border']}; }}"
        )
        bar_row = QHBoxLayout(bar)
        bar_row.setContentsMargins(10, 0, 10, 0)
        bar_row.setSpacing(7)

        grab = QLabel("⠿")
        grab.setStyleSheet(f"color: {C['text2']}; font-size: 12px;")
        bar_row.addWidget(grab)

        title = QLabel("RBXChat")
        title.setFont(QFont("Segoe UI", 11, QFont.Bold))
        title.setStyleSheet(f"color: {C['text']};")
        bar_row.addWidget(title)

        self.ver_badge = QLabel(f"v{VERSION}")
        self.ver_badge.setFont(QFont("Courier New", 7, QFont.Bold))
        self.ver_badge.setStyleSheet(
            f"background: {C['bg3']}; color: {C['text2']};"
            f"border: 1px solid {C['border']}; border-radius: 8px; padding: 1px 6px;"
        )
        bar_row.addWidget(self.ver_badge)
        bar_row.addStretch()

        self.status_dot = QLabel("●")
        self.status_dot.setStyleSheet(f"color: {C['text2']}; font-size: 9px;")
        bar_row.addWidget(self.status_dot)

        close_btn = QPushButton("✕")
        close_btn.setFixedSize(20, 20)
        close_btn.setCursor(QCursor(Qt.PointingHandCursor))
        close_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; border: none;"
            f"color: {C['text2']}; font-size: 11px; border-radius: 10px; }}"
            f"QPushButton:hover {{ background: {C['danger']}; color: white; }}"
        )
        close_btn.clicked.connect(self._toggle_visible)
        bar_row.addWidget(close_btn)

        layout.addWidget(bar)

        # Make bar draggable
        bar.mousePressEvent   = self._drag_start
        bar.mouseMoveEvent    = self._drag_move
        bar.mouseReleaseEvent = self._drag_end
        title.mousePressEvent = bar.mousePressEvent
        title.mouseMoveEvent  = bar.mouseMoveEvent
        grab.mousePressEvent  = bar.mousePressEvent
        grab.mouseMoveEvent   = bar.mouseMoveEvent

    def _build_nav(self, layout):
        nav = QFrame()
        nav.setFixedHeight(36)
        nav.setStyleSheet(
            f"background: {C['bg2']}; border-bottom: 1px solid {C['border']};"
        )
        nav_row = QHBoxLayout(nav)
        nav_row.setContentsMargins(0, 0, 0, 0)
        nav_row.setSpacing(0)

        self._nav_btns = []
        tabs = [("💬", "Messages", 0), ("👤", "Profile", 1), ("⚙️", "Settings", 2)]
        for icon, tip, idx in tabs:
            btn = QPushButton(icon)
            btn.setToolTip(tip)
            btn.setFixedHeight(36)
            btn.setCursor(QCursor(Qt.PointingHandCursor))
            btn.setFont(QFont("Segoe UI Emoji", 13))
            btn.clicked.connect(lambda _, i=idx: self._switch_tab(i))
            nav_row.addWidget(btn)
            self._nav_btns.append(btn)

        layout.addWidget(nav)
        self._switch_tab(0)

    def _switch_tab(self, idx):
        self.stack.setCurrentIndex(idx)
        for i, btn in enumerate(self._nav_btns):
            active = (i == idx)
            a = accent()
            btn.setStyleSheet(
                f"QPushButton {{ background: {'rgba(255,255,255,0.05)' if active else 'transparent'};"
                f"border: none; color: {a if active else C['text2']};"
                f"border-bottom: {f'2px solid {a}' if active else '2px solid transparent'}; }}"
                f"QPushButton:hover {{ color: {C['text']}; background: {C['bg3']}; }}"
            )

    # ── Dragging ──────────────────────────────────────────────────────────────
    def _drag_start(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPos() - self.frameGeometry().topLeft()

    def _drag_move(self, event):
        if event.buttons() == Qt.LeftButton and self._drag_pos:
            self.move(event.globalPos() - self._drag_pos)

    def _drag_end(self, event):
        self._drag_pos = None
        # Save position
        s = cfg.load_settings()
        s["pos"] = [self.x(), self.y()]
        cfg.save_settings(s)

    # ── Toggle visibility ─────────────────────────────────────────────────────
    def _toggle_visible(self):
        self._visible = not self._visible
        if self._visible:
            self.show()
            self.setWindowOpacity(1.0)
        else:
            self.setWindowOpacity(0.0)
            # We don't hide() so hotkey still works

    # ── Hotkey polling (no system-wide hook needed) ───────────────────────────
    def _setup_hotkey_timer(self):
        """Poll keyboard state every 100ms — works without admin or pynput."""
        try:
            import ctypes
            self._user32 = ctypes.WinDLL("user32")
            self._hotkey_timer = QTimer(self)
            self._hotkey_timer.timeout.connect(self._poll_hotkey)
            self._hotkey_timer.start(150)
            self._key_was_down = False
        except Exception:
            pass  # Non-Windows fallback — use close button

    def _poll_hotkey(self):
        try:
            import ctypes
            s = cfg.load_settings()
            key_name = s.get("bind_key", "F9")
            vk = self._key_name_to_vk(key_name)
            if vk is None:
                return
            state = self._user32.GetAsyncKeyState(vk)
            is_down = bool(state & 0x8000)
            if is_down and not self._key_was_down:
                self._toggle_visible()
            self._key_was_down = is_down
        except Exception:
            pass

    @staticmethod
    def _key_name_to_vk(name):
        vk_map = {
            "F1":0x70,"F2":0x71,"F3":0x72,"F4":0x73,"F5":0x74,
            "F6":0x75,"F7":0x76,"F8":0x77,"F9":0x78,"F10":0x79,
            "F11":0x7A,"F12":0x7B,
            "Insert":0x2D,"Delete":0x2E,"Home":0x24,"End":0x23,
            "Prior":0x21,"Next":0x22,  # PageUp/Down
            "Tab":0x09,"Escape":0x1B,"Return":0x0D,"Space":0x20,
        }
        if name in vk_map:
            return vk_map[name]
        if len(name) == 1:
            return ord(name.upper())
        return None

    # ── Firebase ──────────────────────────────────────────────────────────────
    def _start_firebase(self):
        self.fb_thread = FirebaseThread()
        self.fb_thread.message_received.connect(self.msg_panel.add_message)
        self.fb_thread.connection_status.connect(self._set_status)
        self.fb_thread.start()

    @pyqtSlot(bool)
    def _set_status(self, ok):
        if ok:
            self.status_dot.setStyleSheet(f"color: {C['success']}; font-size: 9px;")
            self.status_dot.setToolTip("Connected")
        else:
            self.status_dot.setStyleSheet(f"color: {C['danger']}; font-size: 9px;")
            self.status_dot.setToolTip("Disconnected")

    def _send_message(self, text):
        profile = self.prof_panel.get_profile()
        data = {
            "username":  profile.get("username", "RobloxPlayer"),
            "avatar":    profile.get("avatar", ""),
            "text":      text,
            "timestamp": int(time.time() * 1000),
        }
        # Optimistic local add
        self.msg_panel.add_message(dict(data, _key=f"local_{time.time()}"))
        # Push to Firebase
        threading.Thread(target=fb.send_message, args=(data,), daemon=True).start()

    # ── Profile ───────────────────────────────────────────────────────────────
    def _load_profile(self):
        pass  # ProfilePanel handles its own load on init

    def _on_profile_saved(self, profile):
        self.msg_panel.add_message({
            "username": "RBXChat",
            "text": f"Profile saved! Hello, {profile['username']} 👋",
            "avatar": "", "timestamp": datetime.now().isoformat(),
        })

    def _on_logout(self):
        self.msg_panel.add_message({
            "username": "RBXChat",
            "text": "You've been logged out.",
            "avatar": "", "timestamp": datetime.now().isoformat(),
        })

    # ── Settings ──────────────────────────────────────────────────────────────
    def _on_settings_saved(self, s):
        self._apply_theme(s.get("theme", "#5865f2"))

    def _apply_theme_preview(self, color):
        self._apply_theme(color)

    def _apply_theme(self, color):
        # Update all panels
        self.msg_panel.refresh_accent()
        self.prof_panel.refresh_accent()
        self.set_panel.refresh_accent()
        self._switch_tab(self.stack.currentIndex())

    # ── Update check ──────────────────────────────────────────────────────────
    def _start_update_check(self):
        self.upd_thread = UpdateThread()
        self.upd_thread.update_available.connect(self._show_update)
        self.upd_thread.start()
        QTimer.singleShot(3000, lambda: self.set_panel.set_update_status(
            f"RBXChat v{VERSION} — {'Set UPDATE_CHECK_URL to enable auto-updates.' if not UPDATE_CHECK_URL else 'Up to date ✓'}"
        ))

    def _show_update(self, version, url, notes):
        self.ver_badge.setText(f"v{VERSION}→v{version}")
        self.ver_badge.setStyleSheet(
            f"background: {C['warn']}22; color: {C['warn']};"
            f"border: 1px solid {C['warn']}; border-radius: 8px; padding: 1px 6px;"
        )
        self.update_banner.setText(f"  🆕 v{version} available — {notes}  Click to download")
        self.update_banner.setFixedHeight(24)
        self.update_banner.setCursor(QCursor(Qt.PointingHandCursor))
        self.update_banner.mousePressEvent = lambda _: (
            __import__("webbrowser").open(url) if url else None
        )
        self.set_panel.set_update_status(
            f"RBXChat v{VERSION} — ⚠️ v{version} is available!"
        )

    # ── Close ─────────────────────────────────────────────────────────────────
    def closeEvent(self, event):
        self.fb_thread.stop()
        event.accept()


# ══════════════════════════════════════════════════════════════════════════════
#  Entry point
# ══════════════════════════════════════════════════════════════════════════════
def main():
    app = QApplication(sys.argv)
    app.setApplicationName("RBXChat")
    app.setApplicationVersion(VERSION)

    # Global dark palette
    palette = QPalette()
    palette.setColor(QPalette.Window,          QColor(C["bg"]))
    palette.setColor(QPalette.WindowText,      QColor(C["text"]))
    palette.setColor(QPalette.Base,            QColor(C["bg2"]))
    palette.setColor(QPalette.AlternateBase,   QColor(C["bg3"]))
    palette.setColor(QPalette.Text,            QColor(C["text"]))
    palette.setColor(QPalette.Button,          QColor(C["bg3"]))
    palette.setColor(QPalette.ButtonText,      QColor(C["text"]))
    palette.setColor(QPalette.Highlight,       QColor(accent()))
    palette.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    app.setPalette(palette)

    window = RBXChatOverlay()
    window.show()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
