"""
RBXChat  v2.0.0
Sharp gaming-culture overlay — PyQt5
• Custom loading/splash animation
• Smooth spring animations throughout
• Right-click context menu → view profile / add friend / DM
• DM system with per-user chat rooms
• Friend requests (send / accept / decline)
• Firebase config loaded from file — never in source code
"""

import sys, os, json, threading, time, base64, math, re, webbrowser
from datetime import datetime
from io import BytesIO

from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QLineEdit, QTextEdit, QStackedWidget, QFrame,
    QFileDialog, QColorDialog, QScrollArea, QGraphicsDropShadowEffect,
    QMenu, QAction, QDialog, QListWidget, QListWidgetItem, QSplitter,
    QSizePolicy, QMessageBox
)
from PyQt5.QtCore import (
    Qt, QThread, pyqtSignal, QTimer, QPoint, QPropertyAnimation,
    QEasingCurve, QRect, QSize, pyqtSlot, QSequentialAnimationGroup,
    QParallelAnimationGroup, QVariantAnimation
)
from PyQt5.QtGui import (
    QColor, QFont, QFontDatabase, QPainter, QPainterPath,
    QPixmap, QImage, QIcon, QPalette, QCursor, QLinearGradient,
    QRadialGradient, QBrush, QPen
)

import firebase_db as fb
import config as cfg

VERSION = "2.0.0"
UPDATE_URL = ""  # set to your raw github version.json URL

# ── Palette ────────────────────────────────────────────────────────────────────
P = {
    "bg":      "#0a0b0d",
    "bg2":     "#111318",
    "bg3":     "#1a1c23",
    "bg4":     "#22252f",
    "border":  "#2a2d38",
    "text":    "#edeef2",
    "text2":   "#6b7280",
    "text3":   "#9ca3af",
    "danger":  "#ef4444",
    "success": "#22c55e",
    "warn":    "#f59e0b",
    "online":  "#22c55e",
}

def A(): return cfg.load_settings().get("theme", "#e84a2e")

# ══════════════════════════════════════════════════════════════════════════════
#  Animated widgets
# ══════════════════════════════════════════════════════════════════════════════

class FadeWidget(QWidget):
    """Widget that fades in on show."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._opacity = 0.0

    def fadeIn(self, duration=250):
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setDuration(duration)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(lambda v: self.setWindowOpacity(v))
        self._anim.start()


class PulsingDot(QWidget):
    """Animated pulsing online indicator."""
    def __init__(self, color="#22c55e", size=8, parent=None):
        super().__init__(parent)
        self._color = QColor(color)
        self._size  = size
        self._pulse = 0.0
        self.setFixedSize(size + 4, size + 4)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(30)
        self._t = 0.0

    def _tick(self):
        self._t += 0.06
        self._pulse = (math.sin(self._t) + 1) / 2
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        cx = self.width() / 2
        cy = self.height() / 2
        r  = self._size / 2
        # Glow
        glow = QRadialGradient(cx, cy, r + 3 * self._pulse)
        c = QColor(self._color)
        c.setAlpha(int(80 * self._pulse))
        glow.setColorAt(0, c)
        glow.setColorAt(1, QColor(0,0,0,0))
        p.setBrush(QBrush(glow))
        p.setPen(Qt.NoPen)
        p.drawEllipse(int(cx - r - 3), int(cy - r - 3),
                      int((r+3)*2), int((r+3)*2))
        # Dot
        p.setBrush(QBrush(self._color))
        p.drawEllipse(int(cx-r), int(cy-r), self._size, self._size)


class LoadingSpinner(QWidget):
    """Custom arc spinner — not a gif, fully painted."""
    def __init__(self, size=48, color="#e84a2e", parent=None):
        super().__init__(parent)
        self._color = QColor(color)
        self._angle = 0
        self._arc   = 280
        self.setFixedSize(size, size)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(12)

    def _tick(self):
        self._angle = (self._angle + 4) % 360
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w = self.width()
        pad = 4
        rect = QRect(pad, pad, w - pad*2, w - pad*2)
        # Track
        pen = QPen(QColor(255,255,255,18), 3, Qt.SolidLine, Qt.RoundCap)
        p.setPen(pen)
        p.drawArc(rect, 0, 360*16)
        # Arc
        pen2 = QPen(self._color, 3, Qt.SolidLine, Qt.RoundCap)
        p.setPen(pen2)
        p.drawArc(rect, -self._angle * 16, -self._arc * 16)

    def setColor(self, color: str):
        self._color = QColor(color)
        self.update()



import socket as _socket

# ══════════════════════════════════════════════════════════════════════════════
#  Boot thread
# ══════════════════════════════════════════════════════════════════════════════
class BootThread(QThread):
    step        = pyqtSignal(int, str)   # pct 0-100, label
    no_internet = pyqtSignal()
    done        = pyqtSignal()

    def run(self):
        self.step.emit(15, "Loading config...")
        time.sleep(0.2)

        self.step.emit(35, "Checking connection...")
        ok = self._online()
        if not ok:
            self.no_internet.emit()
            return

        self.step.emit(60, "Connecting to Firebase...")
        time.sleep(0.2)

        self.step.emit(80, "Loading profile...")
        time.sleep(0.15)

        self.step.emit(100, "Ready!")
        time.sleep(0.3)
        self.done.emit()

    @staticmethod
    def _online():
        targets = [
            ("rbxchat-34268-default-rtdb.firebaseio.com", 443),
            ("8.8.8.8", 53),
            ("1.1.1.1", 53),
        ]
        for host, port in targets:
            try:
                s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
                s.settimeout(3)
                s.connect((host, port))
                s.close()
                return True
            except Exception:
                pass
        return False


# ══════════════════════════════════════════════════════════════════════════════
#  Loading Screen  — pure QPainter, single QTimer, no child widgets
# ══════════════════════════════════════════════════════════════════════════════
class LoadingScreen(QWidget):
    finished = pyqtSignal()

    CW = 320   # card width
    CH = 300   # card height
    PAD = 20   # shadow padding

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)

        TW = self.CW + self.PAD * 2
        TH = self.CH + self.PAD * 2
        self.setFixedSize(TW, TH)

        sc = QApplication.primaryScreen().availableGeometry()
        self.move((sc.width() - TW) // 2, (sc.height() - TH) // 2)

        # Animation state
        self._spin   = 0.0       # spinner angle degrees
        self._pct    = 0.0       # current bar fill 0-100
        self._tpct   = 0.0       # target bar fill
        self._label  = "Initialising..."
        self._error  = False
        self._fading = False
        self._alpha  = 1.0
        self._drag   = None
        self._rbtn   = None      # retry button rect (set in paintEvent)

        # Single master timer — 60 fps
        self._t = QTimer(self)
        self._t.timeout.connect(self._tick)
        self._t.start(16)

        # Delay boot by one event-loop cycle so the window is
        # fully shown and the event loop is running before the
        # thread fires its first signal.
        QTimer.singleShot(80, self._startBoot)

    def _startBoot(self):
        self._boot = BootThread()
        self._boot.step.connect(self._onStep, Qt.QueuedConnection)
        self._boot.no_internet.connect(self._onNoInternet, Qt.QueuedConnection)
        self._boot.done.connect(self._onDone, Qt.QueuedConnection)
        self._boot.start()

    # ── Slots (always arrive on main thread via QueuedConnection) ─────────────
    @pyqtSlot(int, str)
    def _onStep(self, pct, label):
        self._tpct  = pct
        self._label = label
        self._error = False

    @pyqtSlot()
    def _onNoInternet(self):
        self._error  = True
        self._tpct   = 0
        self._pct    = 0
        self._label  = ""

    @pyqtSlot()
    def _onDone(self):
        self._fading = True

    # ── Master tick ───────────────────────────────────────────────────────────
    def _tick(self):
        if self._fading:
            self._alpha = max(0.0, self._alpha - 0.05)
            self.setWindowOpacity(self._alpha)
            if self._alpha <= 0:
                self._t.stop()
                self.hide()
                self.finished.emit()
            self.update()
            return

        if not self._error:
            self._spin = (self._spin + 5) % 360

        if self._pct < self._tpct:
            self._pct = min(self._pct + 3, self._tpct)

        self.update()

    def _retry(self):
        self._error  = False
        self._pct    = 0
        self._tpct   = 0
        self._label  = "Retrying..."
        self._startBoot()

    # ── All drawing ───────────────────────────────────────────────────────────
    def paintEvent(self, _):
        pr = QPainter(self)
        pr.setRenderHint(QPainter.Antialiasing)
        pr.setRenderHint(QPainter.TextAntialiasing)

        P2  = self.PAD
        CW  = self.CW
        CH  = self.CH
        cx  = P2 + CW // 2      # horizontal centre of card

        # ── Shadow ────────────────────────────────────────────────────────────
        for i in range(14, 0, -2):
            c = QColor(0, 0, 0, int(90 * (i/14)**2))
            pr.setBrush(QBrush(c))
            pr.setPen(Qt.NoPen)
            pr.drawRoundedRect(P2-i//2, P2+i//2, CW+i, CH+i, 22, 22)

        # ── Card ──────────────────────────────────────────────────────────────
        bg  = QColor("#130d0d") if self._error else QColor("#111318")
        brd = QColor("#ef4444") if self._error else QColor("#2a2d38")
        pr.setBrush(QBrush(bg))
        pr.setPen(QPen(brd, 1.5))
        pr.drawRoundedRect(P2, P2, CW, CH, 22, 22)

        # ── Logo square ───────────────────────────────────────────────────────
        LS   = 64          # logo size
        lx   = cx - LS//2
        ly   = P2 + 30
        lrct = QRect(lx, ly, LS, LS)

        if self._error:
            g = QLinearGradient(lx, ly, lx+LS, ly+LS)
            g.setColorAt(0, QColor("#991b1b"))
            g.setColorAt(1, QColor("#7f1d1d"))
        else:
            g = QLinearGradient(lx, ly, lx+LS, ly+LS)
            g.setColorAt(0, QColor(A()))
            g.setColorAt(1, QColor("#ff6b35"))

        pr.setBrush(QBrush(g))
        pr.setPen(Qt.NoPen)
        pr.drawRoundedRect(lrct, 18, 18)

        pr.setFont(QFont("Segoe UI Emoji", 28))
        pr.setPen(QColor(255, 255, 255, 240))
        pr.drawText(lrct, Qt.AlignCenter, "26a0" if self._error else "💬")

        # ── Title ─────────────────────────────────────────────────────────────
        ty = ly + LS + 14
        pr.setFont(QFont("Segoe UI", 20, QFont.Bold))
        pr.setPen(QColor("#edeef2"))
        pr.drawText(QRect(P2, ty, CW, 32), Qt.AlignHCenter | Qt.AlignVCenter, "RBXChat")

        # ── Subtitle ─────────────────────────────────────────────────────────
        pr.setFont(QFont("Segoe UI", 8))
        pr.setPen(QColor("#4b5563"))
        pr.drawText(QRect(P2, ty+32, CW, 18), Qt.AlignHCenter | Qt.AlignVCenter,
                    "Loading Screen Only  \u2022  v" + VERSION)

        if self._error:
            # ── Error state ───────────────────────────────────────────────────
            ey = ty + 62
            pr.setFont(QFont("Segoe UI", 11, QFont.Bold))
            pr.setPen(QColor("#ef4444"))
            pr.drawText(QRect(P2, ey, CW, 26), Qt.AlignHCenter | Qt.AlignVCenter,
                        "No Internet Connection")

            pr.setFont(QFont("Segoe UI", 9))
            pr.setPen(QColor("#9ca3af"))
            pr.drawText(QRect(P2+20, ey+28, CW-40, 22),
                        Qt.AlignHCenter | Qt.AlignVCenter,
                        "Please Check Your Internet!")

            # Retry button
            bw, bh = 120, 36
            bx = cx - bw//2
            by = ey + 66
            self._rbtn = QRect(bx, by, bw, bh)

            # Hover highlight
            mp = self.mapFromGlobal(QCursor.pos())
            hover = self._rbtn.contains(mp)
            pr.setBrush(QBrush(QColor("#dc2626") if hover else QColor("#ef4444")))
            pr.setPen(Qt.NoPen)
            pr.drawRoundedRect(self._rbtn, 10, 10)
            pr.setFont(QFont("Segoe UI", 9, QFont.Bold))
            pr.setPen(QColor("#ffffff"))
            pr.drawText(self._rbtn, Qt.AlignCenter, "\u21bb  Retry")

        else:
            # ── Normal state ──────────────────────────────────────────────────
            # Spinner
            SR   = 20          # spinner radius
            scx  = cx
            scy  = ty + 66
            srct = QRect(scx-SR, scy-SR, SR*2, SR*2)

            pr.setPen(QPen(QColor(255,255,255,18), 3, Qt.SolidLine, Qt.RoundCap))
            pr.setBrush(Qt.NoBrush)
            pr.drawEllipse(srct)

            pr.setPen(QPen(QColor(A()), 3, Qt.SolidLine, Qt.RoundCap))
            pr.drawArc(srct, int(-self._spin*16), -270*16)

            # Status text
            pr.setFont(QFont("Segoe UI", 8))
            pr.setPen(QColor("#6b7280"))
            pr.drawText(QRect(P2, scy+SR+8, CW, 18),
                        Qt.AlignHCenter | Qt.AlignVCenter, self._label)

            # Progress bar
            bx  = P2 + 32
            by  = scy + SR + 34
            bw  = CW - 64
            bh  = 4
            pr.setBrush(QBrush(QColor("#22252f")))
            pr.setPen(Qt.NoPen)
            pr.drawRoundedRect(bx, by, bw, bh, 2, 2)

            fw = int(bw * self._pct / 100)
            if fw > 2:
                fg = QLinearGradient(bx, 0, bx+bw, 0)
                fg.setColorAt(0, QColor(A()))
                fg.setColorAt(1, QColor("#ff6b35"))
                pr.setBrush(QBrush(fg))
                pr.drawRoundedRect(bx, by, fw, bh, 2, 2)

            # Percentage
            pr.setFont(QFont("Courier New", 7))
            pr.setPen(QColor("#374151"))
            pr.drawText(QRect(P2, by+12, CW, 14),
                        Qt.AlignHCenter | Qt.AlignVCenter, f"{int(self._pct)}%")

    # ── Mouse events ──────────────────────────────────────────────────────────
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = e.globalPos() - self.frameGeometry().topLeft()
            if self._error and self._rbtn and self._rbtn.contains(e.pos()):
                self._retry()

    def mouseMoveEvent(self, e):
        if e.buttons() == Qt.LeftButton and self._drag:
            self.move(e.globalPos() - self._drag)
        if self._error:
            self.update()   # repaint for hover on retry btn

    def mouseReleaseEvent(self, e):
        self._drag = None

    def keyPressEvent(self, e):
        # Allow Escape or Alt+F4 to close
        if e.key() in (Qt.Key_Escape,):
            QApplication.quit()

class AvatarLabel(QLabel):
    def __init__(self, size=32, parent=None):
        super().__init__(parent)
        self._sz = size
        self._pm = None
        self._initials = "?"
        self.setFixedSize(size, size)

    def setAvatar(self, b64: str, name: str = "?"):
        self._initials = (name[0].upper() if name else "?")
        if not b64:
            self._pm = None
            self.update()
            return
        try:
            raw = b64.split(",", 1)[-1]
            data = base64.b64decode(raw)
            pm = QPixmap()
            pm.loadFromData(data)
            self._pm = pm.scaled(self._sz, self._sz,
                Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        except Exception:
            self._pm = None
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addEllipse(0, 0, self._sz, self._sz)
        p.setClipPath(path)
        if self._pm:
            p.drawPixmap(0, 0, self._pm)
        else:
            grad = QLinearGradient(0, 0, self._sz, self._sz)
            grad.setColorAt(0, QColor(A()))
            grad.setColorAt(1, QColor("#ff6b35"))
            p.fillPath(path, QBrush(grad))
            p.setClipping(False)
            p.setPen(QColor(255,255,255,220))
            p.setFont(QFont("Segoe UI", max(8, self._sz//3), QFont.Bold))
            p.drawText(QRect(0,0,self._sz,self._sz), Qt.AlignCenter, self._initials)
            return
        p.setClipping(False)
        p.setPen(QPen(QColor(P["border"]), 1.5))
        p.drawEllipse(1, 1, self._sz-2, self._sz-2)



# ══════════════════════════════════════════════════════════════════════════════
#  GIF Picker — Tenor search
# ══════════════════════════════════════════════════════════════════════════════
TENOR_KEY = "AIzaSyB0k5HjfCYIXKFpGe8CnpMExampleKey"  # free Tenor API v2 key

class GifTile(QLabel):
    clicked = pyqtSignal(str)  # gif url

    def __init__(self, gif_url: str, preview_url: str, parent=None):
        super().__init__(parent)
        self._url = gif_url
        self.setFixedSize(100, 80)
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet(
            f"background:{P['bg4']};border-radius:6px;"
            f"border:1px solid {P['border']};"
        )
        self.setText("...")
        self.setCursor(QCursor(Qt.PointingHandCursor))
        threading.Thread(target=self._load, args=(preview_url,), daemon=True).start()

    def _load(self, url):
        pm = _load_image_from_url(url, max_w=100)
        if pm:
            QTimer.singleShot(0, lambda: self._show(pm))

    def _show(self, pm):
        self.setPixmap(pm.scaled(100, 80, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation))
        self.setStyleSheet("border-radius:6px;border:1px solid " + P["border"] + ";")

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit(self._url)


class GifPicker(QWidget):
    gif_selected = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(340, 360)
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8,8,8,8)
        lay.setSpacing(6)

        card = QFrame()
        card.setStyleSheet(
            f"QFrame{{background:{P['bg2']};border:1px solid {P['border']};"
            f"border-radius:14px;}}"
        )
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(24)
        shadow.setColor(QColor(0,0,0,180))
        shadow.setOffset(0,4)
        card.setGraphicsEffect(shadow)
        lay.addWidget(card)

        inner = QVBoxLayout(card)
        inner.setContentsMargins(10,10,10,10)
        inner.setSpacing(8)

        # Header
        hdr = QHBoxLayout()
        title = QLabel("GIF")
        title.setFont(QFont("Segoe UI", 11, QFont.Bold))
        title.setStyleSheet(f"color:{P['text']};")
        hdr.addWidget(title)
        hdr.addStretch()
        close = QPushButton("✕")
        close.setFixedSize(20,20)
        close.setStyleSheet(
            f"QPushButton{{background:transparent;border:none;color:{P['text2']};border-radius:10px;}}"
            f"QPushButton:hover{{background:{P['danger']};color:white;}}"
        )
        close.clicked.connect(self.close)
        hdr.addWidget(close)
        inner.addLayout(hdr)

        # Search box
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search GIFs...")
        self._search.setFont(QFont("Segoe UI", 9))
        self._search.setStyleSheet(
            f"QLineEdit{{background:{P['bg3']};border:1px solid {P['border']};"
            f"border-radius:8px;color:{P['text']};padding:4px 10px;}}"
            f"QLineEdit:focus{{border-color:{A()};}}"
        )
        self._search.returnPressed.connect(self._doSearch)
        inner.addWidget(self._search)

        # Grid scroll area
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet(
            f"QScrollArea{{border:none;background:{P['bg2']}}}"
            f"QScrollBar:vertical{{width:3px;background:transparent}}"
            f"QScrollBar::handle:vertical{{background:{P['border']};border-radius:2px}}"
            f"QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{{height:0}}"
        )
        self._grid_w = QWidget()
        self._grid_w.setStyleSheet(f"background:{P['bg2']}")
        self._grid = QHBoxLayout(self._grid_w)
        self._grid.setContentsMargins(0,0,0,0)
        self._grid.setSpacing(6)
        self._grid.addStretch()
        self._scroll.setWidget(self._grid_w)
        inner.addWidget(self._scroll)

        # Status
        self._status = QLabel("Type to search GIFs")
        self._status.setFont(QFont("Segoe UI", 8))
        self._status.setStyleSheet(f"color:{P['text2']};")
        self._status.setAlignment(Qt.AlignCenter)
        inner.addWidget(self._status)

        # Load trending on open
        threading.Thread(target=self._fetchTrending, daemon=True).start()

    def _doSearch(self):
        q = self._search.text().strip()
        if not q:
            threading.Thread(target=self._fetchTrending, daemon=True).start()
        else:
            threading.Thread(target=self._fetchSearch, args=(q,), daemon=True).start()
        self._status.setText("Searching...")

    def _fetchTrending(self):
        results = self._query("https://tenor.googleapis.com/v2/featured?key={key}&limit=12&media_filter=gif,tinygif")
        QTimer.singleShot(0, lambda: self._showResults(results))

    def _fetchSearch(self, q):
        import urllib.parse
        enc = urllib.parse.quote(q)
        results = self._query(f"https://tenor.googleapis.com/v2/search?q={enc}&key={{key}}&limit=12&media_filter=gif,tinygif")
        QTimer.singleShot(0, lambda: self._showResults(results))

    def _query(self, url_template):
        import urllib.request
        # Use Tenor v2 public demo key
        key = "AIzaSyAyimkuYQYF1t1mB8Fk7PQFDqkEoolYQdE"
        url = url_template.replace("{key}", key)
        try:
            req = urllib.request.Request(url, headers={"User-Agent":"RBXChat/2.0"})
            with urllib.request.urlopen(req, timeout=6) as r:
                data = json.loads(r.read())
            results = []
            for item in data.get("results", []):
                media = item.get("media_formats", {})
                gif   = media.get("gif",     {}).get("url","")
                tiny  = media.get("tinygif", {}).get("url","")
                if gif:
                    results.append((gif, tiny or gif))
            return results
        except Exception as e:
            print(f"[GIF] {e}")
            return []

    def _showResults(self, results):
        # Clear grid
        while self._grid.count() > 1:
            item = self._grid.takeAt(0)
            if item.widget(): item.widget().deleteLater()

        if not results:
            self._status.setText("No results found")
            return

        self._status.setText(f"{len(results)} GIFs")
        # Use a flow layout — two rows of tiles
        col1 = QVBoxLayout()
        col1.setSpacing(6)
        col2 = QVBoxLayout()
        col2.setSpacing(6)
        for i, (gif_url, preview_url) in enumerate(results):
            tile = GifTile(gif_url, preview_url)
            tile.clicked.connect(self._onGifClick)
            (col1 if i % 2 == 0 else col2).addWidget(tile)
        col1.addStretch()
        col2.addStretch()
        self._grid.insertLayout(0, col1)
        self._grid.insertLayout(1, col2)

    def _onGifClick(self, url: str):
        self.gif_selected.emit(url)
        self.close()


# ══════════════════════════════════════════════════════════════════════════════
#  Helpers — link detection, image loading
# ══════════════════════════════════════════════════════════════════════════════
_URL_RE = re.compile(r'https?://[^\s]+', re.IGNORECASE)
_IMG_EXT = ('.png','.jpg','.jpeg','.gif','.webp','.gifv')

def _is_image_url(url: str) -> bool:
    low = url.lower().split('?')[0]
    return any(low.endswith(e) for e in _IMG_EXT) or 'tenor.com/view' in low or 'media.tenor' in low

def _make_link_text(text: str) -> str:
    """Convert URLs in text to HTML links."""
    def repl(m):
        url = m.group(0)
        short = url if len(url) <= 40 else url[:37] + '...'
        return f'<a href="{url}" style="color:{A()};text-decoration:underline;">{short}</a>'
    return _URL_RE.sub(repl, text)

def _load_image_from_url(url: str, max_w=220) -> QPixmap:
    try:
        import urllib.request
        req = urllib.request.Request(url, headers={"User-Agent":"RBXChat/2.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = r.read()
        pm = QPixmap()
        pm.loadFromData(data)
        if pm.width() > max_w:
            pm = pm.scaledToWidth(max_w, Qt.SmoothTransformation)
        return pm
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════════════════════
#  Image embed widget
# ══════════════════════════════════════════════════════════════════════════════
class ImageEmbed(QLabel):
    def __init__(self, url: str, parent=None):
        super().__init__(parent)
        self._url = url
        self.setFixedSize(220, 80)
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet(
            f"background:{P['bg4']};border:1px solid {P['border']};"
            f"border-radius:8px;color:{P['text2']};font-size:8pt;"
        )
        self.setText("Loading image...")
        self.setCursor(QCursor(Qt.PointingHandCursor))
        threading.Thread(target=self._load, daemon=True).start()

    def _load(self):
        pm = _load_image_from_url(self._url)
        if pm:
            QTimer.singleShot(0, lambda: self._show(pm))
        else:
            QTimer.singleShot(0, lambda: self.setText("Could not load image"))

    def _show(self, pm: QPixmap):
        self.setPixmap(pm)
        self.setFixedSize(pm.width(), pm.height())
        self.setStyleSheet("background:transparent;border:none;border-radius:8px;")

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            webbrowser.open(self._url)


# ══════════════════════════════════════════════════════════════════════════════
#  Message bubble
# ══════════════════════════════════════════════════════════════════════════════
class MsgBubble(QFrame):
    right_clicked = pyqtSignal(str, str)

    def __init__(self, data: dict, is_dm=False, parent=None):
        super().__init__(parent)
        self._username = data.get("username", "?")
        self._avatar   = data.get("avatar", "")
        self._build(data, is_dm)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(
            lambda pos: self.right_clicked.emit(self._username, self._avatar)
        )

    def _build(self, data, is_dm):
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 3, 8, 3)
        row.setSpacing(9)
        row.setAlignment(Qt.AlignTop)

        av = AvatarLabel(28)
        av.setAvatar(data.get("avatar",""), data.get("username","?"))
        row.addWidget(av, 0, Qt.AlignTop)

        body = QVBoxLayout()
        body.setSpacing(4)
        body.setContentsMargins(0,0,0,0)

        # Name + time
        header = QHBoxLayout()
        header.setSpacing(7)
        name = QLabel(data.get("username","?"))
        name.setFont(QFont("Segoe UI", 9, QFont.Bold))
        name.setStyleSheet(f"color:{A()};")
        header.addWidget(name)
        ts = data.get("timestamp","")
        try:
            dt = datetime.fromtimestamp(float(ts)/1000) if isinstance(ts,(int,float)) else datetime.fromisoformat(str(ts))
            ts_str = dt.strftime("%H:%M")
        except: ts_str = datetime.now().strftime("%H:%M")
        t = QLabel(ts_str)
        t.setFont(QFont("Segoe UI", 7))
        t.setStyleSheet(f"color:{P['text2']};")
        header.addWidget(t)
        header.addStretch()
        body.addLayout(header)

        raw_text = data.get("text","")
        urls     = _URL_RE.findall(raw_text)
        img_urls = [u for u in urls if _is_image_url(u)]
        # If message is ONLY an image URL with no other text, skip text bubble
        only_img = len(img_urls) == 1 and raw_text.strip() == img_urls[0]

        if not only_img:
            # Text bubble with clickable links
            html = _make_link_text(raw_text)
            txt = QLabel(html)
            txt.setFont(QFont("Segoe UI", 9))
            txt.setWordWrap(True)
            txt.setOpenExternalLinks(True)
            txt.setTextFormat(Qt.RichText)
            txt.setTextInteractionFlags(Qt.TextBrowserInteraction)
            txt.setStyleSheet(
                f"color:{P['text']};background:{P['bg3']};"
                f"border:1px solid {P['border']};"
                f"border-radius:2px 10px 10px 10px;"
                f"padding:6px 10px;"
            )
            body.addWidget(txt)

        # Image embeds
        for url in img_urls:
            embed = ImageEmbed(url)
            body.addWidget(embed)

        row.addLayout(body)
        self.setStyleSheet("background:transparent;")


# ══════════════════════════════════════════════════════════════════════════════
#  Chat widget (reused for global and DMs)
# ══════════════════════════════════════════════════════════════════════════════
class ChatWidget(QWidget):
    send_requested    = pyqtSignal(str)
    user_right_clicked = pyqtSignal(str, str)

    def __init__(self, placeholder="Message…", parent=None):
        super().__init__(parent)
        self._seen = set()
        self._build(placeholder)

    def _build(self, placeholder):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0,0,0,0)
        lay.setSpacing(0)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet(
            f"QScrollArea{{border:none;background:{P['bg']}}}"
            f"QScrollBar:vertical{{background:transparent;width:3px;border-radius:2px}}"
            f"QScrollBar::handle:vertical{{background:{P['border']};border-radius:2px}}"
            f"QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{{height:0}}"
        )
        self._container = QWidget()
        self._container.setStyleSheet(f"background:{P['bg']}")
        self._lay = QVBoxLayout(self._container)
        self._lay.setContentsMargins(0,4,0,4)
        self._lay.setSpacing(2)
        self._lay.addStretch()
        self.scroll.setWidget(self._container)
        lay.addWidget(self.scroll)

        # Input bar
        bar = QFrame()
        bar.setFixedHeight(50)
        bar.setStyleSheet(
            f"background:{P['bg2']};border-top:1px solid {P['border']};"
        )
        bar_lay = QHBoxLayout(bar)
        bar_lay.setContentsMargins(8,8,8,8)
        bar_lay.setSpacing(6)

        # GIF button
        gif_btn = QPushButton("GIF")
        gif_btn.setFixedSize(36, 32)
        gif_btn.setCursor(QCursor(Qt.PointingHandCursor))
        gif_btn.setFont(QFont("Segoe UI", 7, QFont.Bold))
        gif_btn.setStyleSheet(
            f"QPushButton{{background:{P['bg3']};border:1px solid {P['border']};"
            f"border-radius:8px;color:{P['text2']};}}"
            f"QPushButton:hover{{border-color:{A()};color:{A()};}}"
        )
        gif_btn.clicked.connect(self._openGifPicker)
        bar_lay.addWidget(gif_btn)

        self.inp = QLineEdit()
        self.inp.setPlaceholderText(placeholder)
        self.inp.setMaxLength(500)
        self.inp.setFont(QFont("Segoe UI", 9))
        self.inp.setStyleSheet(
            f"QLineEdit{{background:{P['bg3']};border:1px solid {P['border']};"
            f"border-radius:8px;color:{P['text']};padding:0 10px;}}"
            f"QLineEdit:focus{{border-color:{A()};}}"
        )
        self.inp.returnPressed.connect(self._send)
        bar_lay.addWidget(self.inp)

        send = QPushButton("➤")
        send.setFixedSize(32, 32)
        send.setCursor(QCursor(Qt.PointingHandCursor))
        send.setFont(QFont("Segoe UI", 12))
        send.setStyleSheet(
            f"QPushButton{{background:{A()};border:none;border-radius:8px;color:white;}}"
            f"QPushButton:hover{{background:{A()}cc;}}"
            f"QPushButton:pressed{{background:{P['bg4']};}}"
        )
        send.clicked.connect(self._send)
        bar_lay.addWidget(send)
        lay.addWidget(bar)

    def _send(self):
        t = self.inp.text().strip()
        if not t: return
        self.inp.clear()
        self.send_requested.emit(t)

    def _openGifPicker(self):
        picker = GifPicker(self)
        picker.gif_selected.connect(self._onGifSelected)
        # Position above the input bar
        pos = self.mapToGlobal(self.rect().bottomLeft())
        picker.move(pos.x(), pos.y() - picker.height() - 60)
        picker.show()

    def _onGifSelected(self, url: str):
        self.send_requested.emit(url)

    def addMessage(self, data: dict, is_dm=False):
        uid = data.get("_key") or f"{data.get('username')}|{data.get('text')}|{data.get('timestamp','')}"
        if uid in self._seen: return
        self._seen.add(uid)

        bubble = MsgBubble(data, is_dm)
        bubble.right_clicked.connect(self.user_right_clicked)
        self._lay.insertWidget(self._lay.count()-1, bubble)

        # Slide-in animation
        anim = QVariantAnimation(bubble)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setDuration(180)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.valueChanged.connect(lambda v: bubble.setMaximumHeight(int(v * 200)))
        bubble.setMaximumHeight(0)
        anim.start(QVariantAnimation.DeleteWhenStopped)

        QTimer.singleShot(200, self._scrollBottom)

    def _scrollBottom(self):
        sb = self.scroll.verticalScrollBar()
        sb.setValue(sb.maximum())

    def clear(self):
        self._seen.clear()
        while self._lay.count() > 1:
            item = self._lay.takeAt(0)
            if item.widget(): item.widget().deleteLater()


# ══════════════════════════════════════════════════════════════════════════════
#  DM Panel (sidebar + chat)
# ══════════════════════════════════════════════════════════════════════════════
class DMPanel(QWidget):
    openDM_requested = pyqtSignal(str, str)
    def __init__(self, get_profile_fn, parent=None):
        super().__init__(parent)
        self._get_profile = get_profile_fn
        self._active_user = None
        self._chats = {}       # username -> ChatWidget
        self._last_keys = {}   # username -> last firebase key
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll)
        self._poll_timer.start(2500)
        self._build()

    def _build(self):
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0,0,0,0)
        lay.setSpacing(0)

        # Left sidebar
        side = QFrame()
        side.setFixedWidth(110)
        side.setStyleSheet(
            f"background:{P['bg2']};border-right:1px solid {P['border']};"
        )
        side_lay = QVBoxLayout(side)
        side_lay.setContentsMargins(0,8,0,8)
        side_lay.setSpacing(2)

        lbl = QLabel("  DMs")
        lbl.setFont(QFont("Segoe UI", 8, QFont.Bold))
        lbl.setStyleSheet(f"color:{P['text2']};letter-spacing:1px;")
        side_lay.addWidget(lbl)
        side_lay.addSpacing(4)

        self._dm_list = QVBoxLayout()
        self._dm_list.setSpacing(2)
        self._dm_list.setContentsMargins(4,0,4,0)
        side_lay.addLayout(self._dm_list)
        side_lay.addStretch()
        lay.addWidget(side)

        # Right chat stack
        self._stack = QStackedWidget()
        self._stack.setStyleSheet(f"background:{P['bg']}")

        empty = QLabel("Select a DM\nor right-click\na user in chat")
        empty.setAlignment(Qt.AlignCenter)
        empty.setFont(QFont("Segoe UI", 9))
        empty.setStyleSheet(f"color:{P['text2']};")
        self._stack.addWidget(empty)  # index 0 = empty state
        lay.addWidget(self._stack)

    def openDM(self, username: str, avatar: str = ""):
        if username not in self._chats:
            chat = ChatWidget(f"Message {username}…")
            chat.send_requested.connect(lambda t: self._send(username, t))
            chat.user_right_clicked.connect(lambda u, av: None)
            self._chats[username] = chat
            self._stack.addWidget(chat)
            # Load history
            threading.Thread(target=self._loadHistory, args=(username,), daemon=True).start()
            # Sidebar button
            self._addSidebarBtn(username, avatar)

        self._active_user = username
        self._stack.setCurrentWidget(self._chats[username])

    def _addSidebarBtn(self, username: str, avatar: str):
        btn = QFrame()
        btn.setFixedHeight(44)
        btn.setCursor(QCursor(Qt.PointingHandCursor))
        btn_lay = QHBoxLayout(btn)
        btn_lay.setContentsMargins(6,4,6,4)
        btn_lay.setSpacing(6)

        av = AvatarLabel(28)
        av.setAvatar(avatar, username)
        btn_lay.addWidget(av)

        nm = QLabel(username[:10])
        nm.setFont(QFont("Segoe UI", 8, QFont.Bold))
        nm.setStyleSheet(f"color:{P['text']};")
        btn_lay.addWidget(nm)
        btn_lay.addStretch()

        btn.setStyleSheet(
            f"QFrame{{background:transparent;border-radius:8px;}}"
            f"QFrame:hover{{background:{P['bg3']};}}"
        )
        btn.mousePressEvent = lambda e, u=username, av=avatar: self.openDM(u, av)
        self._dm_list.addWidget(btn)

    def _send(self, to_user: str, text: str):
        p = cfg.load_profile()
        me = p.get("username","")
        av = p.get("avatar","")
        if not me: return
        data = {"username": me, "recipient": to_user, "text": text,
                "avatar": av, "timestamp": int(time.time()*1000)}
        self._chats[to_user].addMessage(dict(data, _key=f"local_{time.time()}"))
        threading.Thread(target=fb.send_dm, args=(me, to_user, text, av), daemon=True).start()

    def _loadHistory(self, username: str):
        p = cfg.load_profile()
        me = p.get("username","")
        if not me: return
        msgs = fb.get_dm_messages(me, username, limit=40)
        for key, msg in msgs.items():
            msg["_key"] = key
            if username in self._chats:
                QTimer.singleShot(0, lambda m=msg: self._chats.get(username) and self._chats[username].addMessage(m, True))
            self._last_keys[username] = key

    def _poll(self):
        if not self._active_user or self._active_user not in self._chats:
            return
        p = cfg.load_profile()
        me = p.get("username","")
        if not me: return
        lk = self._last_keys.get(self._active_user)
        if lk:
            threading.Thread(target=self._fetchNew,
                args=(self._active_user, me, lk), daemon=True).start()

    def _fetchNew(self, other, me, lk):
        msgs = fb.get_dm_messages_after(me, other, lk)
        for key, msg in msgs.items():
            msg["_key"] = key
            self._last_keys[other] = key
            if other in self._chats:
                QTimer.singleShot(0, lambda m=msg: self._chats.get(other) and self._chats[other].addMessage(m, True))


# ══════════════════════════════════════════════════════════════════════════════
#  Profile view dialog (right-click → View Profile)
# ══════════════════════════════════════════════════════════════════════════════
class ProfileDialog(QDialog):
    dm_requested     = pyqtSignal(str, str)
    friend_requested = pyqtSignal(str)

    def __init__(self, username: str, avatar: str, my_username: str, parent=None):
        super().__init__(parent)
        self._username    = username
        self._avatar      = avatar
        self._my_username = my_username
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(260, 300)
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10,10,10,10)

        card = QFrame()
        card.setStyleSheet(
            f"QFrame{{background:{P['bg2']};border:1px solid {P['border']};"
            f"border-radius:16px;}}"
        )
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(30)
        shadow.setColor(QColor(0,0,0,180))
        shadow.setOffset(0,4)
        card.setGraphicsEffect(shadow)
        lay.addWidget(card)

        inner = QVBoxLayout(card)
        inner.setContentsMargins(20,20,20,20)
        inner.setSpacing(10)

        # Banner + avatar
        banner = QFrame()
        banner.setFixedHeight(60)
        banner.setStyleSheet(
            f"background: qlineargradient(x1:0,y1:0,x2:1,y2:1,"
            f"stop:0 {A()}, stop:1 #ff6b35);"
            f"border-radius:10px;"
        )
        inner.addWidget(banner)

        av = AvatarLabel(52)
        av.setAvatar(self._avatar, self._username)
        av.setParent(card)
        av.move(20, 40)
        av.raise_()
        av.setStyleSheet("border: 3px solid " + P["bg2"] + "; border-radius: 26px;")

        inner.addSpacing(14)

        name = QLabel(self._username)
        name.setFont(QFont("Segoe UI", 13, QFont.Bold))
        name.setStyleSheet(f"color:{P['text']};")
        inner.addWidget(name)

        # Fetch bio
        self._bio_lbl = QLabel("Loading profile…")
        self._bio_lbl.setFont(QFont("Segoe UI", 8))
        self._bio_lbl.setStyleSheet(f"color:{P['text2']};")
        self._bio_lbl.setWordWrap(True)
        inner.addWidget(self._bio_lbl)
        inner.addStretch()

        if self._username != self._my_username:
            btn_row = QHBoxLayout()
            btn_row.setSpacing(8)

            dm_btn = QPushButton("💬 Message")
            dm_btn.setFixedHeight(30)
            dm_btn.setFont(QFont("Segoe UI", 8, QFont.Bold))
            dm_btn.setCursor(QCursor(Qt.PointingHandCursor))
            dm_btn.setStyleSheet(
                f"QPushButton{{background:{A()};border:none;border-radius:8px;color:white;}}"
                f"QPushButton:hover{{background:{A()}cc;}}"
            )
            dm_btn.clicked.connect(lambda: (self.dm_requested.emit(self._username, self._avatar), self.accept()))
            btn_row.addWidget(dm_btn)

            fr_btn = QPushButton("➕ Add Friend")
            fr_btn.setFixedHeight(30)
            fr_btn.setFont(QFont("Segoe UI", 8, QFont.Bold))
            fr_btn.setCursor(QCursor(Qt.PointingHandCursor))
            fr_btn.setStyleSheet(
                f"QPushButton{{background:{P['bg3']};border:1px solid {P['border']};"
                f"border-radius:8px;color:{P['text']};}}"
                f"QPushButton:hover{{border-color:{A()};color:{A()};}}"
            )
            fr_btn.clicked.connect(lambda: (self.friend_requested.emit(self._username), self.accept()))
            btn_row.addWidget(fr_btn)
            inner.addLayout(btn_row)

        # Close btn
        close = QPushButton("✕")
        close.setFixedSize(22,22)
        close.setParent(card)
        close.move(card.width()-32, 10)
        close.setStyleSheet(
            f"QPushButton{{background:transparent;border:none;color:{P['text2']};font-size:11px;border-radius:11px;}}"
            f"QPushButton:hover{{background:{P['danger']};color:white;}}"
        )
        close.clicked.connect(self.reject)
        close.raise_()

        threading.Thread(target=self._fetchBio, daemon=True).start()

    def _fetchBio(self):
        prof = fb.get_profile(self._username)
        bio = prof.get("bio","") or "No bio set."
        QTimer.singleShot(0, lambda: self._bio_lbl.setText(bio))

    def showEvent(self, e):
        super().showEvent(e)
        # Find and move close button
        for c in self.findChildren(QPushButton):
            if c.text() == "✕":
                c.move(self.width()-42, 20)


# ══════════════════════════════════════════════════════════════════════════════
#  Friends Panel
# ══════════════════════════════════════════════════════════════════════════════
class FriendsPanel(QWidget):
    openDM_requested = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10,10,10,10)
        lay.setSpacing(10)

        # Pending requests
        req_lbl = QLabel("FRIEND REQUESTS")
        req_lbl.setFont(QFont("Segoe UI", 7, QFont.Bold))
        req_lbl.setStyleSheet(f"color:{P['text2']};letter-spacing:1px;")
        lay.addWidget(req_lbl)

        self._req_area = QVBoxLayout()
        self._req_area.setSpacing(4)
        lay.addLayout(self._req_area)

        self._no_req = QLabel("No pending requests")
        self._no_req.setFont(QFont("Segoe UI", 8))
        self._no_req.setStyleSheet(f"color:{P['text2']};")
        self._req_area.addWidget(self._no_req)

        # Divider
        div = QFrame()
        div.setFixedHeight(1)
        div.setStyleSheet(f"background:{P['border']};")
        lay.addWidget(div)

        # Friends list
        fr_lbl = QLabel("FRIENDS")
        fr_lbl.setFont(QFont("Segoe UI", 7, QFont.Bold))
        fr_lbl.setStyleSheet(f"color:{P['text2']};letter-spacing:1px;")
        lay.addWidget(fr_lbl)

        self._fr_scroll = QScrollArea()
        self._fr_scroll.setWidgetResizable(True)
        self._fr_scroll.setStyleSheet(
            f"QScrollArea{{border:none;background:{P['bg']}}}"
            f"QScrollBar:vertical{{width:3px;background:transparent}}"
            f"QScrollBar::handle:vertical{{background:{P['border']};border-radius:2px}}"
            f"QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{{height:0}}"
        )
        self._fr_container = QWidget()
        self._fr_container.setStyleSheet(f"background:{P['bg']}")
        self._fr_lay = QVBoxLayout(self._fr_container)
        self._fr_lay.setContentsMargins(0,0,0,0)
        self._fr_lay.setSpacing(2)
        self._fr_lay.addStretch()
        self._fr_scroll.setWidget(self._fr_container)
        lay.addWidget(self._fr_scroll)

        self._no_friends = QLabel("No friends yet.\nRight-click a user in chat\nto add them!")
        self._no_friends.setAlignment(Qt.AlignCenter)
        self._no_friends.setFont(QFont("Segoe UI", 8))
        self._no_friends.setStyleSheet(f"color:{P['text2']};")
        lay.addWidget(self._no_friends)

        lay.addStretch()

        # Refresh
        refresh = QPushButton("↻ Refresh")
        refresh.setFixedHeight(28)
        refresh.setFont(QFont("Segoe UI", 8))
        refresh.setCursor(QCursor(Qt.PointingHandCursor))
        refresh.setStyleSheet(
            f"QPushButton{{background:{P['bg3']};border:1px solid {P['border']};"
            f"border-radius:8px;color:{P['text3']};}}"
            f"QPushButton:hover{{border-color:{A()};color:{A()};}}"
        )
        refresh.clicked.connect(self.refresh)
        lay.addWidget(refresh)

    def refresh(self):
        p = cfg.load_profile()
        me = p.get("username","")
        if not me: return
        threading.Thread(target=self._fetchAll, args=(me,), daemon=True).start()

    def _fetchAll(self, me):
        reqs    = fb.get_friend_requests(me)
        friends = fb.get_friends(me)
        QTimer.singleShot(0, lambda: self._populate(reqs, friends))

    def _populate(self, reqs, friends):
        # Clear requests
        while self._req_area.count():
            item = self._req_area.takeAt(0)
            if item.widget(): item.widget().deleteLater()

        pending = {k:v for k,v in reqs.items() if v.get("status")=="pending"}
        if pending:
            self._no_req.setVisible(False)
            for key, req in pending.items():
                self._req_area.addWidget(self._makeReqRow(req))
        else:
            self._no_req = QLabel("No pending requests")
            self._no_req.setFont(QFont("Segoe UI", 8))
            self._no_req.setStyleSheet(f"color:{P['text2']};")
            self._req_area.addWidget(self._no_req)

        # Clear friends
        while self._fr_lay.count() > 1:
            item = self._fr_lay.takeAt(0)
            if item.widget(): item.widget().deleteLater()

        active_friends = {k:v for k,v in friends.items() if not v.get("removed")}
        self._no_friends.setVisible(len(active_friends)==0)
        for key, fr in active_friends.items():
            self._fr_lay.insertWidget(self._fr_lay.count()-1, self._makeFriendRow(fr))

    def _makeReqRow(self, req):
        row = QFrame()
        row.setStyleSheet(
            f"background:{P['bg3']};border:1px solid {P['border']};"
            f"border-radius:8px;"
        )
        r = QHBoxLayout(row)
        r.setContentsMargins(8,6,8,6)
        r.setSpacing(8)

        av = AvatarLabel(24)
        av.setAvatar("", req.get("from","?"))
        r.addWidget(av)

        nm = QLabel(req.get("from","?"))
        nm.setFont(QFont("Segoe UI", 8, QFont.Bold))
        nm.setStyleSheet(f"color:{P['text']};")
        r.addWidget(nm)
        r.addStretch()

        acc = QPushButton("✓")
        acc.setFixedSize(24,24)
        acc.setCursor(QCursor(Qt.PointingHandCursor))
        acc.setStyleSheet(
            f"QPushButton{{background:{P['success']};border:none;border-radius:6px;color:white;font-weight:bold;}}"
        )
        from_user = req.get("from","")
        acc.clicked.connect(lambda: self._accept(from_user, row))
        r.addWidget(acc)

        dec = QPushButton("✕")
        dec.setFixedSize(24,24)
        dec.setCursor(QCursor(Qt.PointingHandCursor))
        dec.setStyleSheet(
            f"QPushButton{{background:{P['danger']};border:none;border-radius:6px;color:white;font-weight:bold;}}"
        )
        dec.clicked.connect(lambda: self._decline(from_user, row))
        r.addWidget(dec)
        return row

    def _accept(self, from_user, row):
        p = cfg.load_profile()
        me = p.get("username","")
        threading.Thread(target=fb.accept_friend_request, args=(me, from_user), daemon=True).start()
        row.deleteLater()
        self.refresh()

    def _decline(self, from_user, row):
        p = cfg.load_profile()
        me = p.get("username","")
        threading.Thread(target=fb.decline_friend_request, args=(me, from_user), daemon=True).start()
        row.deleteLater()

    def _makeFriendRow(self, fr):
        row = QFrame()
        row.setFixedHeight(42)
        row.setStyleSheet(
            f"QFrame{{background:transparent;border-radius:8px;}}"
            f"QFrame:hover{{background:{P['bg3']};}}"
        )
        r = QHBoxLayout(row)
        r.setContentsMargins(6,4,6,4)
        r.setSpacing(8)

        av = AvatarLabel(26)
        av.setAvatar("", fr.get("username","?"))
        r.addWidget(av)

        nm = QLabel(fr.get("username","?"))
        nm.setFont(QFont("Segoe UI", 9, QFont.Bold))
        nm.setStyleSheet(f"color:{P['text']};")
        r.addWidget(nm)
        r.addStretch()

        dm_btn = QPushButton("💬")
        dm_btn.setFixedSize(26,26)
        dm_btn.setCursor(QCursor(Qt.PointingHandCursor))
        dm_btn.setToolTip("Send DM")
        dm_btn.setStyleSheet(
            f"QPushButton{{background:{P['bg4']};border:1px solid {P['border']};"
            f"border-radius:6px;color:{P['text3']};}}"
            f"QPushButton:hover{{border-color:{A()};color:{A()};}}"
        )
        uname = fr.get("username","")
        dm_btn.clicked.connect(lambda: self.openDM_requested.emit(uname, ""))
        r.addWidget(dm_btn)
        return row


# ══════════════════════════════════════════════════════════════════════════════
#  Profile Panel
# ══════════════════════════════════════════════════════════════════════════════
class ProfilePanel(QWidget):
    saved = pyqtSignal(dict)
    logged_out = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._avatar_b64 = ""
        self._build()
        self.load()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14,14,14,14)
        lay.setSpacing(10)

        # Avatar
        av_frame = QFrame()
        av_frame.setStyleSheet(
            f"background:{P['bg2']};border-radius:12px;border:1px solid {P['border']};"
        )
        av_inner = QVBoxLayout(av_frame)
        av_inner.setAlignment(Qt.AlignCenter)
        av_inner.setContentsMargins(16,16,16,16)
        av_inner.setSpacing(8)

        self.av_lbl = AvatarLabel(64)
        av_inner.addWidget(self.av_lbl, 0, Qt.AlignCenter)

        ch = QPushButton("Change Avatar")
        ch.setFixedHeight(26)
        ch.setFont(QFont("Segoe UI", 8))
        ch.setCursor(QCursor(Qt.PointingHandCursor))
        ch.setStyleSheet(
            f"QPushButton{{background:{P['bg3']};border:1px solid {P['border']};"
            f"border-radius:6px;color:{P['text2']};padding:0 10px;}}"
            f"QPushButton:hover{{color:{P['text']};border-color:{A()};}}"
        )
        ch.clicked.connect(self._pickAvatar)
        av_inner.addWidget(ch, 0, Qt.AlignCenter)

        self.disp_name = QLabel("RobloxPlayer")
        self.disp_name.setFont(QFont("Segoe UI", 12, QFont.Bold))
        self.disp_name.setStyleSheet(f"color:{P['text']};border:none;")
        self.disp_name.setAlignment(Qt.AlignCenter)
        av_inner.addWidget(self.disp_name)

        self.disp_bio = QLabel("No bio set.")
        self.disp_bio.setFont(QFont("Segoe UI", 8))
        self.disp_bio.setStyleSheet(f"color:{P['text2']};border:none;")
        self.disp_bio.setAlignment(Qt.AlignCenter)
        self.disp_bio.setWordWrap(True)
        av_inner.addWidget(self.disp_bio)
        lay.addWidget(av_frame)

        def field(label, placeholder, multi=False):
            lay.addWidget(self._lbl(label))
            if multi:
                w = QTextEdit()
                w.setPlaceholderText(placeholder)
                w.setFixedHeight(52)
                w.setFont(QFont("Segoe UI", 9))
            else:
                w = QLineEdit()
                w.setPlaceholderText(placeholder)
                w.setFont(QFont("Segoe UI", 9))
            w.setStyleSheet(
                f"background:{P['bg3']};border:1px solid {P['border']};"
                f"border-radius:8px;color:{P['text']};padding:5px 10px;"
            )
            lay.addWidget(w)
            return w

        self.uname_inp = field("USERNAME", "YourRobloxName")
        self.uname_inp.setMaxLength(40)
        self.bio_inp = field("BIO", "Tell everyone about yourself…", multi=True)

        lay.addStretch()

        row = QHBoxLayout()
        row.setSpacing(8)
        self.save_btn = QPushButton("Save Profile")
        self.save_btn.setFixedHeight(32)
        self.save_btn.setFont(QFont("Segoe UI", 9, QFont.Bold))
        self.save_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self._styleSaveBtn()
        self.save_btn.clicked.connect(self._save)
        row.addWidget(self.save_btn)

        out = QPushButton("Logout")
        out.setFixedHeight(32)
        out.setFont(QFont("Segoe UI", 9, QFont.Bold))
        out.setCursor(QCursor(Qt.PointingHandCursor))
        out.setStyleSheet(
            f"QPushButton{{background:{P['danger']};border:none;border-radius:8px;color:white;}}"
            f"QPushButton:hover{{background:#c0393b;}}"
        )
        out.clicked.connect(self._logout)
        row.addWidget(out)
        lay.addLayout(row)

    def _lbl(self, text):
        l = QLabel(text)
        l.setFont(QFont("Segoe UI", 7, QFont.Bold))
        l.setStyleSheet(f"color:{P['text2']};letter-spacing:1px;")
        return l

    def _styleSaveBtn(self):
        a = A()
        self.save_btn.setStyleSheet(
            f"QPushButton{{background:{a};border:none;border-radius:8px;color:white;}}"
            f"QPushButton:hover{{background:{a}cc;}}"
        )

    def _pickAvatar(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose Avatar", "", "Images (*.png *.jpg *.jpeg *.webp)")
        if not path: return
        with open(path,"rb") as f: raw = f.read()
        ext = os.path.splitext(path)[1].lower().strip(".")
        mime = {"jpg":"jpeg","jpeg":"jpeg","png":"png","gif":"gif","webp":"webp"}.get(ext,"png")
        self._avatar_b64 = f"data:image/{mime};base64,"+base64.b64encode(raw).decode()
        self.av_lbl.setAvatar(self._avatar_b64, self.uname_inp.text() or "?")

    def _save(self):
        u = self.uname_inp.text().strip() or "RobloxPlayer"
        b = self.bio_inp.toPlainText().strip()
        p = {"username":u,"bio":b,"avatar":self._avatar_b64}
        cfg.save_profile(p)
        self.disp_name.setText(u)
        self.disp_bio.setText(b or "No bio set.")
        threading.Thread(target=fb.save_profile, args=(p,), daemon=True).start()
        self.saved.emit(p)

    def _logout(self):
        if QMessageBox.question(self,"Logout","Clear profile and logout?",
            QMessageBox.Yes|QMessageBox.No) == QMessageBox.Yes:
            cfg.save_profile({"username":"","bio":"","avatar":""})
            self.uname_inp.setText("")
            self.bio_inp.setPlainText("")
            self._avatar_b64 = ""
            self.av_lbl.setAvatar("","?")
            self.disp_name.setText("RobloxPlayer")
            self.disp_bio.setText("No bio set.")
            self.logged_out.emit()

    def load(self):
        p = cfg.load_profile()
        self.uname_inp.setText(p.get("username",""))
        self.bio_inp.setPlainText(p.get("bio",""))
        self._avatar_b64 = p.get("avatar","")
        self.av_lbl.setAvatar(self._avatar_b64, p.get("username","?"))
        self.disp_name.setText(p.get("username","") or "RobloxPlayer")
        self.disp_bio.setText(p.get("bio","") or "No bio set.")

    def get(self): return cfg.load_profile()


# ══════════════════════════════════════════════════════════════════════════════
#  Settings Panel
# ══════════════════════════════════════════════════════════════════════════════
class SettingsPanel(QWidget):
    theme_changed = pyqtSignal(str)
    saved         = pyqtSignal(dict)

    PRESETS = [
        ("#e84a2e","Fire"),("#5865f2","Blurple"),("#22c55e","Green"),
        ("#f59e0b","Amber"),("#06b6d4","Cyan"),("#a855f7","Purple"),
        ("#ec4899","Pink"),("#f97316","Orange"),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._capture = False
        self._build()
        self.load()

    def _lbl(self, t):
        l = QLabel(t)
        l.setFont(QFont("Segoe UI", 7, QFont.Bold))
        l.setStyleSheet(f"color:{P['text2']};letter-spacing:1px;")
        return l

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14,14,14,14)
        lay.setSpacing(12)

        lay.addWidget(self._lbl("THEME COLOUR"))
        sw_row = QHBoxLayout()
        sw_row.setSpacing(6)
        for color, name in self.PRESETS:
            b = QPushButton()
            b.setFixedSize(22,22)
            b.setToolTip(name)
            b.setCursor(QCursor(Qt.PointingHandCursor))
            b.setStyleSheet(
                f"QPushButton{{background:{color};border-radius:11px;border:2px solid transparent;}}"
                f"QPushButton:hover{{border-color:white;}}"
            )
            b.clicked.connect(lambda _, c=color: self._setAccent(c))
            sw_row.addWidget(b)
        cust = QPushButton("+")
        cust.setFixedSize(22,22)
        cust.setCursor(QCursor(Qt.PointingHandCursor))
        cust.setStyleSheet(
            f"QPushButton{{background:{P['bg3']};border:2px dashed {P['border']};"
            f"border-radius:11px;color:{P['text2']};}}"
        )
        cust.clicked.connect(self._pickCustom)
        sw_row.addWidget(cust)
        sw_row.addStretch()
        lay.addLayout(sw_row)

        lay.addWidget(self._lbl("TOGGLE KEY"))
        key_row = QHBoxLayout()
        key_row.setSpacing(8)
        self.key_disp = QLabel("F9")
        self.key_disp.setFixedHeight(28)
        self.key_disp.setAlignment(Qt.AlignCenter)
        self.key_disp.setFont(QFont("Courier New", 11, QFont.Bold))
        self.key_disp.setStyleSheet(
            f"background:{P['bg3']};border:1px solid {P['border']};"
            f"border-radius:8px;color:{P['text']};padding:0 12px;"
        )
        key_row.addWidget(self.key_disp, 1)
        self.set_key_btn = QPushButton("Set Key")
        self.set_key_btn.setFixedHeight(28)
        self.set_key_btn.setFont(QFont("Segoe UI", 8, QFont.Bold))
        self.set_key_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.set_key_btn.setStyleSheet(
            f"QPushButton{{background:{P['bg3']};border:1px solid {P['border']};"
            f"border-radius:8px;color:{P['text3']};padding:0 12px;}}"
            f"QPushButton:hover{{border-color:{A()};color:{A()};}}"
        )
        self.set_key_btn.clicked.connect(self._startCapture)
        key_row.addWidget(self.set_key_btn)
        lay.addLayout(key_row)

        lay.addWidget(self._lbl("VERSION"))
        self.ver_lbl = QLabel(f"RBXChat v{VERSION}")
        self.ver_lbl.setFont(QFont("Segoe UI", 8))
        self.ver_lbl.setStyleSheet(
            f"background:{P['bg3']};border:1px solid {P['border']};"
            f"border-radius:8px;color:{P['text2']};padding:8px 10px;"
        )
        lay.addWidget(self.ver_lbl)

        lay.addWidget(self._lbl("FIREBASE"))
        db_lbl = QLabel(f"🔥 RTDB: {fb.DB_URL or 'Not configured'}")
        db_lbl.setFont(QFont("Segoe UI", 8))
        db_lbl.setWordWrap(True)
        db_lbl.setStyleSheet(
            f"background:{P['bg3']};border:1px solid {P['border']};"
            f"border-radius:8px;color:{P['text2']};padding:8px 10px;"
        )
        lay.addWidget(db_lbl)

        lay.addStretch()

        self.save_btn = QPushButton("Save Settings")
        self.save_btn.setFixedHeight(32)
        self.save_btn.setFont(QFont("Segoe UI", 9, QFont.Bold))
        self.save_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self._styleSaveBtn()
        self.save_btn.clicked.connect(self._save)
        lay.addWidget(self.save_btn)

    def _styleSaveBtn(self):
        a = A()
        self.save_btn.setStyleSheet(
            f"QPushButton{{background:{a};border:none;border-radius:8px;color:white;}}"
            f"QPushButton:hover{{background:{a}cc;}}"
        )

    def _setAccent(self, color):
        s = cfg.load_settings(); s["theme"] = color; cfg.save_settings(s)
        self.theme_changed.emit(color)

    def _pickCustom(self):
        c = QColorDialog.getColor(QColor(A()), self)
        if c.isValid(): self._setAccent(c.name())

    def _startCapture(self):
        self._capture = True
        self.set_key_btn.setText("Press a key…")
        self.set_key_btn.setStyleSheet(
            f"QPushButton{{background:{A()};border:none;border-radius:8px;color:white;padding:0 12px;}}"
        )
        self.setFocus()

    def keyPressEvent(self, e):
        if self._capture:
            k = QKeySequence(e.key()).toString()
            if k:
                s = cfg.load_settings(); s["bind_key"] = k; cfg.save_settings(s)
                self.key_disp.setText(k)
            self._capture = False
            self.set_key_btn.setText("Set Key")
            self.set_key_btn.setStyleSheet(
                f"QPushButton{{background:{P['bg3']};border:1px solid {P['border']};"
                f"border-radius:8px;color:{P['text3']};padding:0 12px;}}"
            )
        else: super().keyPressEvent(e)

    def _save(self):
        self._styleSaveBtn()
        self.saved.emit(cfg.load_settings())

    def load(self):
        s = cfg.load_settings()
        self.key_disp.setText(s.get("bind_key","F9"))

    def setUpdateStatus(self, text):
        self.ver_lbl.setText(text)

    def refreshAccent(self): self._styleSaveBtn()


# ══════════════════════════════════════════════════════════════════════════════
#  Firebase polling threads
# ══════════════════════════════════════════════════════════════════════════════
class MsgThread(QThread):
    received = pyqtSignal(dict)
    status   = pyqtSignal(bool)

    def __init__(self):
        super().__init__()
        self._running  = True
        self._last_key = None

    def run(self):
        retry = 0
        while self._running:
            try:
                if self._last_key is None:
                    msgs = fb.get_recent_messages(40)
                    for k, m in msgs.items():
                        m["_key"] = k
                        self.received.emit(m)
                        self._last_key = k
                else:
                    new = fb.get_messages_after(self._last_key)
                    for k, m in new.items():
                        m["_key"] = k
                        self.received.emit(m)
                        self._last_key = k
                self.status.emit(True)
                retry = 0
            except Exception:
                self.status.emit(False)
                retry = min(retry+1, 8)
            for _ in range(max(20, retry*10)):
                if not self._running: return
                time.sleep(0.1)

    def stop(self):
        self._running = False
        self.wait(2000)


# ══════════════════════════════════════════════════════════════════════════════
#  Main Overlay Window
# ══════════════════════════════════════════════════════════════════════════════
class RBXChat(QWidget):
    def __init__(self):
        super().__init__()
        self._drag_pos    = None
        self._visible     = True
        self._key_was_dn  = False
        self._setup_win()
        self._build_ui()
        self._start_fb()
        self._setup_hotkey()

    def _setup_win(self):
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setMinimumSize(360, 500)
        self.resize(380, 540)
        s = cfg.load_settings()
        pos = s.get("pos")
        if pos:
            self.move(pos[0], pos[1])
        else:
            sc = QApplication.primaryScreen().availableGeometry()
            self.move(sc.width()-360, sc.height()-500)

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8,8,8,8)
        root.setSpacing(0)

        self.card = QFrame()
        self.card.setObjectName("Card")
        self.card.setStyleSheet(
            f"QFrame#Card{{background:{P['bg']};border:1px solid {P['border']};"
            f"border-radius:16px;}}"
        )
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(32)
        shadow.setColor(QColor(0,0,0,200))
        shadow.setOffset(0,8)
        self.card.setGraphicsEffect(shadow)
        root.addWidget(self.card)

        cl = QVBoxLayout(self.card)
        cl.setContentsMargins(0,0,0,0)
        cl.setSpacing(0)

        self._buildTitlebar(cl)

        # Stack + panels
        self.stack = QStackedWidget()
        self.stack.setStyleSheet(f"background:{P['bg']}")

        self.chat_panel  = ChatWidget("Message everyone…")
        self.dm_panel    = DMPanel(fb.get_profile)
        self.friends_panel = FriendsPanel()
        self.profile_panel = ProfilePanel()
        self.settings_panel = SettingsPanel()

        self.stack.addWidget(self.chat_panel)     # 0
        self.stack.addWidget(self.dm_panel)       # 1
        self.stack.addWidget(self.friends_panel)  # 2
        self.stack.addWidget(self.profile_panel)  # 3
        self.stack.addWidget(self.settings_panel) # 4

        self._buildNav(cl)
        cl.addWidget(self.stack)

        # Wire
        self.chat_panel.send_requested.connect(self._sendGlobal)
        self.chat_panel.user_right_clicked.connect(self._onRightClick)
        self.dm_panel.openDM_requested.connect(self._openDM)
        self.friends_panel.openDM_requested.connect(self._openDM)
        self.profile_panel.saved.connect(self._onProfileSaved)
        self.profile_panel.logged_out.connect(lambda: None)
        self.settings_panel.theme_changed.connect(self._applyTheme)
        self.settings_panel.saved.connect(lambda _: self._applyTheme(A()))

    def _buildTitlebar(self, layout):
        bar = QFrame()
        bar.setObjectName("TitleBar")
        bar.setFixedHeight(44)
        bar.setStyleSheet(
            f"QFrame#TitleBar{{background:{P['bg2']};"
            f"border-radius:16px 16px 0 0;"
            f"border-bottom:1px solid {P['border']};}}"
        )
        row = QHBoxLayout(bar)
        row.setContentsMargins(12,0,12,0)
        row.setSpacing(8)

        # Logo
        logo = QLabel("💬")
        logo.setFixedSize(24,24)
        logo.setAlignment(Qt.AlignCenter)
        logo.setFont(QFont("Segoe UI Emoji", 14))
        logo.setStyleSheet(
            f"background:{A()};border-radius:7px;color:white;"
        )
        self._logo = logo
        row.addWidget(logo)

        title = QLabel("RBXChat")
        title.setFont(QFont("Segoe UI", 12, QFont.Bold))
        title.setStyleSheet(f"color:{P['text']};")
        row.addWidget(title)

        self.ver_badge = QLabel(f"v{VERSION}")
        self.ver_badge.setFont(QFont("Courier New", 7, QFont.Bold))
        self.ver_badge.setStyleSheet(
            f"background:{P['bg3']};color:{P['text2']};"
            f"border:1px solid {P['border']};border-radius:6px;padding:1px 6px;"
        )
        row.addWidget(self.ver_badge)
        row.addStretch()

        self._dot = PulsingDot(P["text2"], 8)
        row.addWidget(self._dot)

        close_btn = QPushButton("✕")
        close_btn.setFixedSize(22,22)
        close_btn.setCursor(QCursor(Qt.PointingHandCursor))
        close_btn.setStyleSheet(
            f"QPushButton{{background:transparent;border:none;"
            f"color:{P['text2']};font-size:12px;border-radius:11px;}}"
            f"QPushButton:hover{{background:{P['danger']};color:white;}}"
        )
        close_btn.clicked.connect(self._toggleVis)
        row.addWidget(close_btn)

        layout.addWidget(bar)

        for w in [bar, title, logo]:
            w.mousePressEvent   = self._dragStart
            w.mouseMoveEvent    = self._dragMove
            w.mouseReleaseEvent = self._dragEnd

        self.update_banner = QLabel("")
        self.update_banner.setFixedHeight(0)
        self.update_banner.setFont(QFont("Segoe UI", 8))
        self.update_banner.setStyleSheet(
            f"background:#4a2600;color:#ffd580;"
            f"border-bottom:1px solid {P['warn']};padding:0 10px;"
        )
        layout.addWidget(self.update_banner)

    def _buildNav(self, layout):
        nav = QFrame()
        nav.setFixedHeight(52)
        nav.setStyleSheet(
            f"background:{P['bg2']};border-bottom:1px solid {P['border']};"
        )
        row = QHBoxLayout(nav)
        row.setContentsMargins(0,0,0,0)
        row.setSpacing(0)

        self._nav_btns = []
        tabs = [("💬","Chat",0),("✉️","DMs",1),("👥","Friends",2),("👤","Profile",3),("⚙️","Settings",4)]
        for icon, label, idx in tabs:
            b = QPushButton()
            b.setFixedHeight(52)
            b.setCursor(QCursor(Qt.PointingHandCursor))
            b.setToolTip(label)
            # Stack icon + label vertically inside button
            b_lay = QVBoxLayout(b)
            b_lay.setContentsMargins(0, 5, 0, 4)
            b_lay.setSpacing(1)
            ico = QLabel(icon)
            ico.setFont(QFont("Segoe UI Emoji", 12))
            ico.setAlignment(Qt.AlignCenter)
            ico.setAttribute(Qt.WA_TransparentForMouseEvents)
            lbl = QLabel(label)
            lbl.setFont(QFont("Segoe UI", 7))
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setAttribute(Qt.WA_TransparentForMouseEvents)
            b_lay.addWidget(ico)
            b_lay.addWidget(lbl)
            b.clicked.connect(lambda _, i=idx: self._switchTab(i))
            row.addWidget(b)
            self._nav_btns.append((b, ico, lbl))

        layout.addWidget(nav)
        self._switchTab(0)

    def _switchTab(self, idx):
        self.stack.setCurrentIndex(idx)
        a = A()
        for i, (b, ico, lbl) in enumerate(self._nav_btns):
            active = (i == idx)
            bg = "rgba(255,255,255,0.05)" if active else "transparent"
            b.setStyleSheet(
                f"QPushButton{{background:{bg};border:none;"
                f"border-bottom:{'2px solid '+a if active else '2px solid transparent'};"
                f"border-radius:0px;}}"
                f"QPushButton:hover{{background:{P['bg3']};}}"
            )
            ico.setStyleSheet(f"color:{a if active else P['text2']};background:transparent;")
            lbl.setStyleSheet(
                f"color:{a if active else P['text2']};background:transparent;"
                f"{'font-weight:bold;' if active else ''}"
            )
        if idx == 2:
            QTimer.singleShot(100, self.friends_panel.refresh)

    # ── Right-click context menu ───────────────────────────────────────────────
    def _onRightClick(self, username: str, avatar: str):
        me = cfg.load_profile().get("username","")
        if username == me: return

        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu{{background:{P['bg2']};border:1px solid {P['border']};"
            f"border-radius:10px;padding:4px;}}"
            f"QMenu::item{{color:{P['text']};padding:7px 20px 7px 12px;"
            f"border-radius:6px;font-family:'Segoe UI';font-size:9pt;}}"
            f"QMenu::item:selected{{background:{P['bg3']};color:{A()};}}"
            f"QMenu::separator{{height:1px;background:{P['border']};margin:3px 8px;}}"
        )

        view_act   = QAction(f"  👤  View Profile", self)
        dm_act     = QAction(f"  💬  Send Message", self)
        friend_act = QAction(f"  ➕  Add Friend", self)

        view_act.triggered.connect(lambda: self._showProfile(username, avatar))
        dm_act.triggered.connect(lambda: self._openDM(username, avatar))
        friend_act.triggered.connect(lambda: self._sendFriendReq(username))

        menu.addAction(view_act)
        menu.addAction(dm_act)
        menu.addSeparator()
        menu.addAction(friend_act)
        menu.exec_(QCursor.pos())

    def _showProfile(self, username, avatar):
        me = cfg.load_profile().get("username","")
        dlg = ProfileDialog(username, avatar, me, self)
        dlg.dm_requested.connect(self._openDM)
        dlg.friend_requested.connect(self._sendFriendReq)
        # Centre on overlay
        dlg.move(self.x() + (self.width()-dlg.width())//2,
                 self.y() + (self.height()-dlg.height())//2)
        dlg.exec_()

    def _openDM(self, username, avatar=""):
        self._switchTab(1)
        self.dm_panel.openDM(username, avatar)

    def _sendFriendReq(self, username):
        me = cfg.load_profile().get("username","")
        if not me: return
        threading.Thread(target=fb.send_friend_request, args=(me, username), daemon=True).start()
        self.chat_panel.addMessage({
            "username": "RBXChat",
            "text": f"Friend request sent to {username}!",
            "timestamp": int(time.time()*1000)
        })

    # ── Send global message ───────────────────────────────────────────────────
    def _sendGlobal(self, text):
        p = cfg.load_profile()
        data = {
            "username": p.get("username","RobloxPlayer"),
            "avatar":   p.get("avatar",""),
            "text":     text,
            "timestamp": int(time.time()*1000)
        }
        self.chat_panel.addMessage(dict(data, _key=f"local_{time.time()}"))
        threading.Thread(target=fb.send_message, args=(data,), daemon=True).start()

    # ── Firebase ──────────────────────────────────────────────────────────────
    def _start_fb(self):
        self._msg_thread = MsgThread()
        self._msg_thread.received.connect(self.chat_panel.addMessage)
        self._msg_thread.status.connect(self._setStatus)
        self._msg_thread.start()

    @pyqtSlot(bool)
    def _setStatus(self, ok):
        color = P["online"] if ok else P["danger"]
        self._dot._color = QColor(color)

    def _onProfileSaved(self, p):
        threading.Thread(target=fb.set_online,
            args=(p.get("username",""), p.get("avatar","")), daemon=True).start()

    # ── Theme ─────────────────────────────────────────────────────────────────
    def _applyTheme(self, color):
        self._logo.setStyleSheet(f"background:{color};border-radius:7px;color:white;")
        self.profile_panel._styleSaveBtn()
        self.settings_panel.refreshAccent()
        self._switchTab(self.stack.currentIndex())

    # ── Drag ──────────────────────────────────────────────────────────────────
    def _dragStart(self, e):
        if e.button() == Qt.LeftButton:
            self._drag_pos = e.globalPos() - self.frameGeometry().topLeft()

    def _dragMove(self, e):
        if e.buttons() == Qt.LeftButton and self._drag_pos:
            self.move(e.globalPos() - self._drag_pos)

    def _dragEnd(self, e):
        self._drag_pos = None
        s = cfg.load_settings()
        s["pos"] = [self.x(), self.y()]
        cfg.save_settings(s)

    # ── Toggle ────────────────────────────────────────────────────────────────
    def _toggleVis(self):
        self._visible = not self._visible
        if self._visible:
            self.setWindowOpacity(1.0)
            self._slideIn()
        else:
            self._slideOut()

    def _slideIn(self):
        anim = QVariantAnimation(self)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setDuration(200)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.valueChanged.connect(self.setWindowOpacity)
        anim.start(QVariantAnimation.DeleteWhenStopped)

    def _slideOut(self):
        anim = QVariantAnimation(self)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.setDuration(180)
        anim.setEasingCurve(QEasingCurve.InCubic)
        anim.valueChanged.connect(self.setWindowOpacity)
        anim.start(QVariantAnimation.DeleteWhenStopped)

    # ── Hotkey polling ────────────────────────────────────────────────────────
    def _setup_hotkey(self):
        try:
            import ctypes
            self._u32 = ctypes.WinDLL("user32")
            self._hk_timer = QTimer(self)
            self._hk_timer.timeout.connect(self._pollHotkey)
            self._hk_timer.start(120)
        except Exception: pass

    def _pollHotkey(self):
        try:
            k = cfg.load_settings().get("bind_key","F9")
            vk = self._vkMap(k)
            if vk is None: return
            down = bool(self._u32.GetAsyncKeyState(vk) & 0x8000)
            if down and not self._key_was_dn: self._toggleVis()
            self._key_was_dn = down
        except Exception: pass

    @staticmethod
    def _vkMap(name):
        m = {"F1":0x70,"F2":0x71,"F3":0x72,"F4":0x73,"F5":0x74,
             "F6":0x75,"F7":0x76,"F8":0x77,"F9":0x78,"F10":0x79,
             "F11":0x7A,"F12":0x7B,"Insert":0x2D,"Delete":0x2E,
             "Home":0x24,"End":0x23,"Tab":0x09,"Escape":0x1B}
        if name in m: return m[name]
        if len(name)==1: return ord(name.upper())
        return None

    def closeEvent(self, e):
        self._msg_thread.stop()
        e.accept()





# ══════════════════════════════════════════════════════════════════════════════
#  Entry point
# ══════════════════════════════════════════════════════════════════════════════
def main():
    app = QApplication(sys.argv)
    app.setApplicationName("RBXChat")

    pal = QPalette()
    pal.setColor(QPalette.Window,          QColor(P["bg"]))
    pal.setColor(QPalette.WindowText,      QColor(P["text"]))
    pal.setColor(QPalette.Base,            QColor(P["bg2"]))
    pal.setColor(QPalette.Text,            QColor(P["text"]))
    pal.setColor(QPalette.Button,          QColor(P["bg3"]))
    pal.setColor(QPalette.ButtonText,      QColor(P["text"]))
    pal.setColor(QPalette.Highlight,       QColor(A()))
    pal.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    app.setPalette(pal)

    w = RBXChat()
    prof = cfg.load_profile()
    name = prof.get("username") or "stranger"
    w.chat_panel.addMessage({
        "username":  "RBXChat",
        "text":      f"Welcome back, {name}! 👋  Press F9 to toggle.",
        "timestamp": int(time.time() * 1000),
    })
    w.show()
    w._slideIn()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
