"""Dynamic Island для Windows — сам остров.
Запуск:  python island.py
Настройки живут в отдельном приложении (settings_app.py): шестерёнка на острове
или пункт «Настройки» в меню значка в трее. Изменения применяются на лету.
"""
import datetime as dt
import json
import math
import os
import random
import subprocess
import sys
import time
from pathlib import Path

from PySide6.QtCore import (QEasingCurve, QEvent, QFileInfo, QLockFile, QMimeData, QPointF,
                            QPropertyAnimation, QRect, QRectF, Qt, QTimer, QUrl, QVariantAnimation, Signal)
from PySide6.QtGui import (QAction, QColor, QConicalGradient, QCursor, QDesktopServices, QDrag,
                           QFont, QFontMetrics, QGuiApplication, QIcon, QPainter, QPainterPath,
                           QPen, QPixmap)
from PySide6.QtWidgets import (QApplication, QFileIconProvider, QGraphicsOpacityEffect,
                               QFrame, QGridLayout, QHBoxLayout, QLabel, QMenu, QPushButton, QScrollArea,
                               QStackedWidget, QSystemTrayIcon, QVBoxLayout, QWidget)

import common as C
from mascots import draw_mascot, set_repo_color
from island_clip import ClipPage, ClipStore
from island_extras import LyricsEngine, LyricsPage, draw_eq, mix
from island_mixer import MixerPage
from island_rec import RecordPage
from island_weather import WeatherService
from island_modules import (INPUT_QSS, Alarm, CalcPage, EyePage, RemindersPage, SystemPage, TimerPage,
                            btn_style, fmt_hms)

EXP_W, EXP_H = 700, 292
LYR_W = 460  # ширина пилюли в режиме бегущего текста
# автоскрытие
HIDE_EDGE = 3          # px от верха экрана, где курсор «будит» остров
HIDE_ZONE_PAD = 120    # насколько зона пробуждения шире острова с каждой стороны, px
HIDE_DELAY_TICKS = 10  # сколько тиков (по 60 мс) ждать перед тем, как спрятаться
BUSY = {"v": False}  # True, пока файл тащат с полки наружу — остров не должен схлопнуться

MODULES = [  # ключ, иконка, название вкладки
    ("clock", "🕒", "Часы"),
    ("timer", "⏱", "Таймер"),
    ("reminders", "🔔", "Напоминания"),
    ("calc", "🧮", "Калькулятор"),
    ("system", "📊", "Система"),
    ("rec", "🎬", "Запись"),
    ("music", "🎵", "Музыка"),
    ("mixer", "🎚", "Микшер"),
    ("clip", "📋", "Буфер"),
    ("eye", "👁", "Глазок"),
    ("shelf", "📁", "Полка"),
]


def lerp(a, b, t):
    return a + (b - a) * t


class ClickRipple(QWidget):
    """Расходящийся круг в месте клика: появляется под курсором, растёт и тает."""
    DURATION = 0.55

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.drops = []  # (время старта, x, y, макс. радиус)
        self.timer = QTimer(self, interval=16, timeout=self._tick)
        self.hide()

    def spawn(self, x, y, big):
        self.setGeometry(self.parent().rect())
        self.drops.append((time.monotonic(), x, y, 30.0 if big else 17.0))
        self.show()
        self.raise_()
        self.timer.start()

    def _tick(self):
        now = time.monotonic()
        self.drops = [d for d in self.drops if now - d[0] < self.DURATION]
        if not self.drops:
            self.timer.stop()
            self.hide()
            return
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        clip = QPainterPath()  # не выходим за скруглённые углы острова
        r = min(self.height() / 2, 30)
        clip.addRoundedRect(QRectF(self.rect()), r, r)
        p.setClipPath(clip)
        now = time.monotonic()
        for t0, x, y, rmax in self.drops:
            k = min(1.0, (now - t0) / self.DURATION)
            e = 1 - (1 - k) ** 3
            fade = (1 - k) ** 1.5
            rad = 4 + (rmax - 4) * e
            fill = QColor(255, 255, 255, int(46 * fade))
            ring = QColor(255, 255, 255, int(150 * fade))
            p.setBrush(fill)
            p.setPen(QPen(ring, 1.6))
            p.drawEllipse(QPointF(x, y), rad, rad)


class RevealOverlay(QWidget):
    """Переключение вкладки кругом: новая страница уже лежит под этим слоем, а снимок старой «стирается»
    кругом, который растёт от места клика. По краю круга бежит тонкое цветное кольцо."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.pm, self.origin, self.r, self.r_max = None, QPointF(), 0.0, 1.0
        self.color = QColor("#ffffff")
        self.anim = QVariantAnimation(self)
        self.anim.setDuration(440)
        self.anim.setStartValue(0.0)
        self.anim.setEndValue(1.0)
        self.anim.setEasingCurve(QEasingCurve(QEasingCurve.Type.OutCubic))
        self.anim.valueChanged.connect(self._step)
        self.anim.finished.connect(self.stop)
        self.hide()

    def play(self, pm, origin, rect, color):
        self.anim.stop()
        self.setGeometry(rect)
        self.pm, self.origin, self.color, self.r = pm, QPointF(origin), QColor(color), 0.0
        w, h = rect.width(), rect.height()
        self.r_max = max(math.hypot(cx - origin.x(), cy - origin.y()) for cx, cy in ((0, 0), (w, 0), (0, h), (w, h))) + 6
        self.show()
        self.raise_()
        self.anim.start()

    def stop(self):
        self.anim.stop()
        self.pm = None
        self.hide()

    def _step(self, t):
        self.r = float(t) * self.r_max
        self.update()

    def paintEvent(self, _):
        if self.pm is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        full, hole = QPainterPath(), QPainterPath()
        full.addRect(QRectF(self.rect()))
        hole.addEllipse(self.origin, self.r, self.r)
        p.setClipPath(full.subtracted(hole))
        p.drawPixmap(0, 0, self.pm)
        p.setClipping(False)
        ring = QColor(self.color)
        ring.setAlphaF(max(0.0, min(1.0, (1 - self.r / self.r_max) * 1.1)))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(ring, 2.2))
        p.drawEllipse(self.origin, self.r, self.r)


# ───────────────────────────── Календарь ─────────────────────────────
class CalendarWidget(QWidget):
    HEAD, WD, ROW = 26, 20, 27

    def __init__(self, get_cfg):
        super().__init__()
        self.get_cfg = get_cfg
        self.setFixedSize(290, self.HEAD + self.WD + self.ROW * 6)
        t = dt.date.today()
        self.year, self.month = t.year, t.month

    def shift(self, d):
        m = self.month - 1 + d
        self.year += m // 12
        self.month = m % 12 + 1
        self.update()

    def reset(self):
        t = dt.date.today()
        self.year, self.month = t.year, t.month
        self.update()

    def _arrows(self):
        return QRectF(self.width() - 66, 0, 32, self.HEAD), QRectF(self.width() - 34, 0, 32, self.HEAD)

    def mousePressEvent(self, e):
        pos = e.position()
        left, right = self._arrows()
        if left.contains(pos):
            self.shift(-1)
        elif right.contains(pos):
            self.shift(1)
        elif pos.y() < self.HEAD:
            self.reset()

    def wheelEvent(self, e):
        self.shift(-1 if e.angleDelta().y() > 0 else 1)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        mon = self.get_cfg()["week_starts_monday"]
        w = self.width()
        cw = w / 7

        f = QFont("Segoe UI", 1)
        f.setPixelSize(15)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor("#ffffff"))
        p.drawText(QRectF(0, 0, 200, self.HEAD), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   f"{C.MONTHS[self.month - 1]} {self.year}")
        p.setPen(QColor("#c7c7cc"))
        f.setPixelSize(18)
        p.setFont(f)
        la, ra = self._arrows()
        p.drawText(la, Qt.AlignmentFlag.AlignCenter, "‹")
        p.drawText(ra, Qt.AlignmentFlag.AlignCenter, "›")

        names = C.WEEKDAYS_SHORT if mon else [C.WEEKDAYS_SHORT[6]] + C.WEEKDAYS_SHORT[:6]
        f.setPixelSize(12)
        p.setFont(f)
        p.setPen(QColor("#6e6e73"))
        for i, n in enumerate(names):
            p.drawText(QRectF(i * cw, self.HEAD, cw, self.WD), Qt.AlignmentFlag.AlignCenter, n)

        first = dt.date(self.year, self.month, 1)
        offset = first.weekday() if mon else (first.weekday() + 1) % 7
        start = first - dt.timedelta(days=offset)
        today = dt.date.today()
        f.setPixelSize(14)
        p.setFont(f)
        for i in range(42):
            d = start + dt.timedelta(days=i)
            r, c = divmod(i, 7)
            cell = QRectF(c * cw, self.HEAD + self.WD + r * self.ROW, cw, self.ROW)
            if d == today:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor("#ff453a"))
                p.drawRoundedRect(cell.adjusted(4, 2, -4, -2), 9, 9)
                p.setPen(QColor("#ffffff"))
            elif d.month != self.month:
                p.setPen(QColor("#48484a"))
            elif d.weekday() >= 5:
                p.setPen(QColor("#8e8e93"))
            else:
                p.setPen(QColor("#f2f2f7"))
            p.drawText(cell, Qt.AlignmentFlag.AlignCenter, str(d.day))


# ───────────────────────────── Часы ─────────────────────────────
class ClockPage(QWidget):
    def __init__(self, get_cfg, weather=None):
        super().__init__()
        self.get_cfg = get_cfg
        self.weather = weather
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 0, 0, 0)

        left = QVBoxLayout()
        left.setSpacing(2)
        left.addStretch()
        row = QHBoxLayout()
        row.setSpacing(4)
        self.time = QLabel()
        self.time.setStyleSheet("font-size:52px;font-weight:700;color:#fff;")
        self.sec = QLabel()
        self.sec.setStyleSheet("font-size:22px;font-weight:600;color:#555;padding-bottom:8px;")
        row.addWidget(self.time, 0, Qt.AlignmentFlag.AlignBottom)
        row.addWidget(self.sec, 0, Qt.AlignmentFlag.AlignBottom)
        row.addStretch()
        # погода над временем: значок + градусы, рядом описание и город
        self.wbox = QWidget()
        wl = QHBoxLayout(self.wbox)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.setSpacing(10)
        self.w_temp = QLabel()
        self.w_temp.setStyleSheet("font-size:26px;font-weight:700;color:#fff;font-family:'Segoe UI','Segoe UI Emoji';")
        wt = QVBoxLayout()
        wt.setContentsMargins(0, 0, 0, 0)
        wt.setSpacing(0)
        self.w_desc = QLabel()
        self.w_desc.setStyleSheet("font-size:12px;color:#8e8e93;")
        self.w_place = QLabel()
        self.w_place.setStyleSheet("font-size:12px;color:#636366;")
        wt.addWidget(self.w_desc)
        wt.addWidget(self.w_place)
        wl.addWidget(self.w_temp)
        wl.addLayout(wt)
        wl.addStretch()
        self.date = QLabel()
        self.date.setStyleSheet("font-size:19px;font-weight:700;color:#8e8e93;")
        self.week = QLabel()
        self.week.setStyleSheet("font-size:15px;color:#636366;")
        left.addWidget(self.wbox)
        left.addLayout(row)
        left.addWidget(self.date)
        left.addWidget(self.week)
        left.addStretch()
        lay.addLayout(left, 1)

        self.cal = CalendarWidget(get_cfg)
        lay.addWidget(self.cal, 0, Qt.AlignmentFlag.AlignTop)
        self.refresh()

    def refresh(self):
        now = dt.datetime.now()
        cfg = self.get_cfg()
        if cfg["clock_24h"]:
            txt = now.strftime("%H:%M")
        else:
            txt = f"{now.hour % 12 or 12}:{now.minute:02d} {'AM' if now.hour < 12 else 'PM'}"
        self.time.setText(txt)
        self.sec.setText(f"{now.second:02d}" if cfg["show_seconds"] else "")
        self.date.setText(f"{C.WEEKDAYS[now.weekday()]}, {now.day} {C.MONTHS_GEN[now.month - 1]}")
        self.week.setText(f"Неделя {now.isocalendar()[1]}")
        w = self.weather
        on = bool(cfg.get("weather_on", True)) and w is not None
        self.wbox.setVisible(on)
        if on:
            self.w_temp.setText(f"{w.icon()} {w.temp_text()}")
            self.w_desc.setText(w.desc_text())
            self.w_place.setText(w.place_text())


# ───────────────────────────── Полка ─────────────────────────────
IMG_EXT = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
_icons = QFileIconProvider()


class IslandMirror(QWidget):
    """Копия острова на другом мониторе (режим «на всех»). Показывает снимок настоящего острова.
    Наведёшь курсор или кликнешь — настоящий остров переезжает на этот монитор."""

    def __init__(self, island, screen):
        super().__init__()
        self.island = island
        self.scr = screen
        self.pm = None
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.Tool | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

    def sync(self, pm, g, home_geo):
        sg = self.scr.geometry()
        self.pm = pm
        new = QRect(sg.left() + (sg.width() - g.width()) // 2, sg.top() + (g.y() - home_geo.top()),
                    g.width(), g.height())
        if not self.isVisible():
            self.show()
            if self.windowHandle() is not None:
                self.windowHandle().setScreen(self.scr)
        if self.geometry() != new:
            self.setGeometry(new)
        self.update()

    def mousePressEvent(self, e):
        self.island.move_home(self.scr)
        if self.island.cfg["expand_on"] == "click":
            self.island.set_expanded(True)

    def paintEvent(self, _):
        if self.pm is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.drawPixmap(self.rect(), self.pm)


class ShelfTile(QWidget):
    removed = Signal(str)

    def __init__(self, path):
        super().__init__()
        self.path = path
        self.setFixedSize(104, 112)
        self.setMouseTracking(True)
        self.setToolTip(path)
        self._hover = False
        self._press = None
        self.pix = self._make_pix()
        self.btn = QPushButton("✕", self)
        self.btn.setGeometry(78, 4, 22, 22)
        self.btn.setStyleSheet("QPushButton{background:#3a3a3c;color:#fff;border:none;border-radius:11px;"
                               "font-size:11px}QPushButton:hover{background:#ff453a}")
        self.btn.clicked.connect(lambda: self.removed.emit(self.path))
        self.btn.hide()

    def _make_pix(self):
        if Path(self.path).suffix.lower() in IMG_EXT:
            pm = QPixmap(self.path)
            if not pm.isNull():
                return pm.scaled(64, 64, Qt.AspectRatioMode.KeepAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation)
        return _icons.icon(QFileInfo(self.path)).pixmap(48, 48)

    def enterEvent(self, _):
        self._hover = True
        self.btn.show()
        self.update()

    def leaveEvent(self, _):
        self._hover = False
        self.btn.hide()
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._press = e.position().toPoint()

    def mouseMoveEvent(self, e):
        if self._press is None or not (e.buttons() & Qt.MouseButton.LeftButton):
            return
        if (e.position().toPoint() - self._press).manhattanLength() < QApplication.startDragDistance():
            return
        self._press = None
        BUSY["v"] = True
        try:
            drag = QDrag(self)
            md = QMimeData()
            md.setUrls([QUrl.fromLocalFile(self.path)])
            drag.setMimeData(md)
            drag.setPixmap(self.pix)
            drag.exec(Qt.DropAction.CopyAction | Qt.DropAction.LinkAction)
        finally:
            BUSY["v"] = False

    def mouseReleaseEvent(self, _):
        self._press = None

    def mouseDoubleClickEvent(self, _):
        QDesktopServices.openUrl(QUrl.fromLocalFile(self.path))

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        if self._hover:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#1c1c1e"))
            p.drawRoundedRect(self.rect(), 14, 14)
        sz = self.pix.deviceIndependentSize()
        p.drawPixmap(int((self.width() - sz.width()) / 2), int(12 + (64 - sz.height()) / 2), self.pix)
        f = QFont("Segoe UI", 1)
        f.setPixelSize(12)
        p.setFont(f)
        p.setPen(QColor("#e5e5ea"))
        name = QFontMetrics(f).elidedText(Path(self.path).name, Qt.TextElideMode.ElideMiddle, self.width() - 10)
        p.drawText(QRectF(5, 84, self.width() - 10, 22), Qt.AlignmentFlag.AlignCenter, name)


class ShelfPage(QWidget):
    COLS = 5

    def __init__(self):
        super().__init__()
        self.paths = []
        try:
            data = json.loads(C.SHELF_FILE.read_text("utf-8"))
            self.paths = [x for x in data if os.path.exists(x)]
        except Exception:
            pass

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        head = QHBoxLayout()
        hint = QLabel("Перетащи, чтобы использовать · двойной клик открывает")
        hint.setStyleSheet("color:#8e8e93;font-size:13px;")
        clear = QPushButton("Очистить")
        clear.setCursor(Qt.CursorShape.PointingHandCursor)
        clear.setStyleSheet("QPushButton{color:#8e8e93;background:transparent;border:none;font-size:13px}"
                            "QPushButton:hover{color:#fff}")
        clear.clicked.connect(self.clear)
        head.addWidget(hint)
        head.addStretch()
        head.addWidget(clear)
        lay.addLayout(head)

        self.empty = QLabel("Полка пуста\nПеретащи сюда файлы — они будут под рукой")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setStyleSheet("border:1.5px dashed #3a3a3c;border-radius:14px;color:#8e8e93;font-size:14px;")
        lay.addWidget(self.empty, 1)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet(
            "QScrollArea,QScrollArea>QWidget>QWidget{background:transparent;border:none}"
            "QScrollBar:vertical{width:6px;background:transparent}"
            "QScrollBar::handle:vertical{background:#3a3a3c;border-radius:3px}"
            "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0}")
        self.inner = QWidget()
        self.grid = QGridLayout(self.inner)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(6)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.scroll.setWidget(self.inner)
        lay.addWidget(self.scroll, 1)
        self.rebuild()

    def add(self, new_paths):
        for p in new_paths:
            if p and os.path.exists(p) and p not in self.paths:
                self.paths.append(p)
        self.rebuild()

    def remove(self, path):
        if path in self.paths:
            self.paths.remove(path)
        self.rebuild()

    def clear(self):
        self.paths = []
        self.rebuild()

    def rebuild(self):
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for i, path in enumerate(self.paths):
            tile = ShelfTile(path)
            tile.removed.connect(self.remove)
            self.grid.addWidget(tile, i // self.COLS, i % self.COLS)
        self.empty.setVisible(not self.paths)
        self.scroll.setVisible(bool(self.paths))
        try:
            C.CFG_DIR.mkdir(parents=True, exist_ok=True)
            C.SHELF_FILE.write_text(json.dumps(self.paths, ensure_ascii=False), "utf-8")
        except Exception:
            pass


# ───────────────────────────── Остров ─────────────────────────────
TAB_ON = ("QPushButton{background:#1c1c1e;color:#fff;border:none;border-radius:22px;"
          "padding:0 18px;font-size:16px;font-weight:600}")
TAB_OFF = ("QPushButton{background:transparent;color:#fff;border:none;border-radius:22px;"
           "padding:0 9px;font-size:19px}QPushButton:hover{background:#151517}")


class Island(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Dynamic Island by ASTRO")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAcceptDrops(True)

        self.cfg = C.load_config()
        self._mtime = C.config_mtime()
        self.expanded = False
        self._angle = 45.0
        self._drag_in = False
        self.hold = False
        self.eye_look = QPointF(0, 0)
        self.eye_blink = 0.0
        self._eye_dir = 0
        self._eye_next = time.time() + 3
        self._in_ticks = self._out_ticks = 0
        self.lyrics = LyricsEngine()            # Live Lyrics
        self.clip = ClipStore()                 # история буфера обмена
        self.weather = WeatherService()         # погода
        self.home = QGuiApplication.primaryScreen()   # монитор, где сейчас настоящий остров
        self.mirrors = {}                       # QScreen → IslandMirror (режим «на всех»)
        self._ly_state = (-2, "", 0.0)          # (индекс строки, текст, прогресс)
        self._ly_prev, self._ly_t0 = "", 0.0    # для плавной смены строк
        self._hidden = False      # автоскрытие: остров сейчас спрятан (или уезжает)
        self._hide_k = 0.0        # 0 — на месте, 1 — полностью за краем экрана
        self._hide_ticks = 0
        self.page_key = "clock"
        self.cur_w, self.cur_h = self.collapsed_size()
        self._from = self._to = (self.cur_w, self.cur_h)

        self._build_ui()
        self.click_fx = ClickRipple(self)
        QApplication.instance().installEventFilter(self)  # ловим клики, чтобы показать круг

        self.anim = QVariantAnimation(self)
        self.anim.valueChanged.connect(self._on_anim)
        self.hide_anim = QVariantAnimation(self)
        self.hide_anim.valueChanged.connect(self._on_hide_anim)

        self.t_hover = QTimer(self, interval=60, timeout=self._tick_hover)
        self.t_clock = QTimer(self, interval=500, timeout=self._tick_clock)
        self.t_cfg = QTimer(self, interval=500, timeout=self._tick_cfg)
        self.t_spin = QTimer(self, interval=33, timeout=self._tick_spin)
        self.t_eye = QTimer(self, interval=33, timeout=self._tick_eye)
        self.t_hide = QTimer(self, interval=60, timeout=self._tick_autohide)
        self.t_mirror = QTimer(self, interval=33, timeout=self._tick_mirrors)
        self.weather.updated.connect(self.clock_page.refresh)
        QGuiApplication.instance().screenAdded.connect(lambda *_: self.apply_config())
        QGuiApplication.instance().screenRemoved.connect(lambda *_: self.apply_config())
        self.t_lyr = QTimer(self, interval=33, timeout=self._tick_lyrics)
        for t in (self.t_hover, self.t_clock, self.t_cfg, self.t_hide):
            t.start()

        self.apply_config()
        self.show()

    # ---- интерфейс
    def _build_ui(self):
        self.content = QWidget(self)
        self.content.setFixedSize(EXP_W, EXP_H)
        self.fx = QGraphicsOpacityEffect(self.content)
        self.content.setGraphicsEffect(self.fx)
        self.fade = QPropertyAnimation(self.fx, b"opacity", self)
        self.fade.setDuration(220)
        self.fade.finished.connect(lambda: self.fx.setEnabled(False))
        self.content.hide()

        lay = QVBoxLayout(self.content)
        lay.setContentsMargins(24, 14, 24, 14)
        lay.setSpacing(8)

        bar = QHBoxLayout()
        bar.setSpacing(2)
        self.tabs = {}
        for key, icon, title in MODULES:
            b = QPushButton()
            b.setFixedHeight(44)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: self.show_page(k, animate=True))
            self.tabs[key] = (b, icon, title)
            bar.addWidget(b)
        bar.addStretch()
        gear = QPushButton("⚙")
        gear.setFixedSize(40, 40)
        gear.setToolTip("Настройки")
        gear.setCursor(Qt.CursorShape.PointingHandCursor)
        gear.setStyleSheet("QPushButton{background:transparent;color:#8e8e93;border:none;font-size:22px;"
                           "border-radius:20px}QPushButton:hover{color:#fff;background:#151517}")
        gear.clicked.connect(self.open_settings)
        bar.addWidget(gear)
        lay.addLayout(bar)

        self.stack = QStackedWidget()
        self.clock_page = ClockPage(lambda: self.cfg, self.weather)
        self.shelf_page = ShelfPage()
        self.timer_page = TimerPage()
        self.rem_page = RemindersPage()
        self.lyrics_page = LyricsPage(self.lyrics)
        self.lyrics_page.offset_step.connect(self._on_offset_step)
        self.lyrics_page.island_toggled.connect(self._on_island_toggled)
        self.clip_page = ClipPage(self.clip)
        self.clip_page.pause_toggled.connect(self._on_clip_pause)
        self.rec_page = RecordPage(lambda: self.cfg)
        self.rec_page.mode_changed.connect(lambda m: self.set_cfg("rec_mode", m))
        self.pages = {"clock": self.clock_page, "timer": self.timer_page, "reminders": self.rem_page,
                      "calc": CalcPage(), "system": SystemPage(), "rec": self.rec_page, "music": self.lyrics_page, "mixer": MixerPage(), "clip": self.clip_page,
                      "eye": EyePage(), "shelf": self.shelf_page}
        for w in self.pages.values():
            self.stack.addWidget(w)
        lay.addWidget(self.stack, 1)
        self.content.setStyleSheet(INPUT_QSS)
        self.reveal = RevealOverlay(self.content)

        # плашка-уведомление поверх страницы (таймер / напоминание)
        self.banner = QFrame(self.content)
        self.banner.setObjectName("banner")
        self.banner.setStyleSheet("#banner{background:#ff9f0a;border-radius:16px}"
                                  "QLabel{background:transparent;color:#000;font-size:15px;font-weight:700}")
        bl = QHBoxLayout(self.banner)
        bl.setContentsMargins(18, 6, 8, 6)
        self.banner_lbl = QLabel()
        bl.addWidget(self.banner_lbl, 1)
        ok = QPushButton("Ок")
        ok.setFixedSize(64, 32)
        ok.setCursor(Qt.CursorShape.PointingHandCursor)
        ok.setStyleSheet(btn_style("#000000", "#ffffff", "#2c2c2e", 12, 14))
        ok.clicked.connect(self.dismiss_alert)
        bl.addWidget(ok)
        self.banner.setGeometry(24, EXP_H - 62, EXP_W - 48, 48)
        self.banner.hide()
        self.timer_page.finished.connect(lambda: self.alert("timer", "Таймер", "время вышло"))
        self.rem_page.fired.connect(lambda t: self.alert("reminders", "Напоминание", t))

    def alert(self, key, title, text):
        if self.cfg["modules"].get(key, True):
            self.show_page(key)
        fm = QFontMetrics(self.banner_lbl.font())
        self.banner_lbl.setText(fm.elidedText(f"{title}: {text}", Qt.TextElideMode.ElideRight, EXP_W - 190))
        self.banner.show()
        self.banner.raise_()
        self.hold = True
        self.set_expanded(True)
        if self.cfg.get("alarm_sound", True):
            Alarm.start()

    def dismiss_alert(self):
        Alarm.stop()
        self.hold = False
        self.banner.hide()

    def _snapshot_stack(self):
        """Снимок текущей страницы на чёрном фоне — для круговой анимации."""
        dpr = self.stack.devicePixelRatioF()
        pm = QPixmap(max(1, int(self.stack.width() * dpr)), max(1, int(self.stack.height() * dpr)))
        pm.setDevicePixelRatio(dpr)
        pm.fill(QColor(0, 0, 0))
        self.stack.render(pm)
        return pm

    def show_page(self, key, animate=False):
        ripple = (animate and key != self.page_key and self.expanded and self.stack.isVisible()
                  and bool(self.cfg.get("tab_ripple", True)))
        if ripple:
            snap = self._snapshot_stack()
            origin = self.stack.mapFromGlobal(QCursor.pos())  # клик был по вкладке выше — круг растёт «из неё»
        self.page_key = key
        self.stack.setCurrentWidget(self.pages[key])
        if ripple:
            cols = C.border_colors(self.cfg)
            self.reveal.play(snap, origin, self.stack.geometry(), "#8b7cf6" if C.is_black(cols) else cols[0])
            if self.banner.isVisible():
                self.banner.raise_()
        for k, (b, icon, title) in self.tabs.items():
            on = k == key
            b.setText(f"{icon}  {title}" if on else icon)
            b.setStyleSheet(TAB_ON if on else TAB_OFF)

    def open_settings(self):
        script = Path(__file__).with_name("settings_app.py")
        exe = Path(sys.executable)
        flags = 0
        if os.name == "nt":
            sw = exe.with_name("AstroIslandSettings.exe")   # свой exe — в Диспетчере задач «Astro Island»
            pw = exe.with_name("pythonw.exe")
            if sw.exists():
                exe = sw
            elif pw.exists():
                exe = pw
            flags = 0x08000000  # CREATE_NO_WINDOW
        try:
            subprocess.Popen([str(exe), str(script)], creationflags=flags)
        except Exception as e:
            print("Не удалось открыть настройки:", e)

    # ---- конфиг
    def apply_config(self):
        mods = self.cfg["modules"]
        for key, (b, _, _) in self.tabs.items():
            b.setVisible(mods.get(key, True))
        enabled = [k for k in self.tabs if mods.get(k, True)]
        if enabled and (self.page_key not in enabled):
            self.page_key = enabled[0]
        if enabled:
            self.show_page(self.page_key)
        elif self.expanded:
            self.set_expanded(False)
        if not self.expanded:
            self.cur_w, self.cur_h = self.collapsed_size()
        self.t_spin.start() if (self.cfg["moving_gradient"] and not C.is_black(C.border_colors(self.cfg))) \
            else self.t_spin.stop()
        self.t_eye.start() if self.rest_parts()[0] else self.t_eye.stop()
        self.clock_page.refresh()
        if not self.cfg.get("autohide", False):
            self._set_hidden(False)
        lyr_on = bool(self.cfg.get("lyrics_on", True))
        self.lyrics.offset = int(self.cfg.get("lyrics_offset_ms", 0)) / 1000.0
        self.lyrics.set_enabled(lyr_on or mods.get("music", True))
        self.t_lyr.start() if lyr_on else self.t_lyr.stop()
        self.lyrics_page.set_island_on(lyr_on)
        paused = bool(self.cfg.get("clip_pause", False))
        self.clip.enabled = bool(mods.get("clip", True)) and not paused
        self.clip.set_persist(bool(self.cfg.get("clip_persist", True)))
        self.clip_page.set_paused(paused)
        set_repo_color(self.cfg.get("repo_color"))
        self._pick_screens()
        self.place()
        self.update()

    def _tick_cfg(self):
        m = C.config_mtime()
        if m != self._mtime:
            self._mtime = m
            self.cfg = C.load_config()
            self.apply_config()

    def _tick_clock(self):
        if self.cfg.get("weather_on", True):
            self.weather.poll(self.cfg.get("weather_city", ""))
        self.clock_page.refresh()
        want = 250 if self._activity() else 500
        if self.t_clock.interval() != want:
            self.t_clock.setInterval(want)
        self._sync_rest_size()
        self.update()

    def _tick_eye(self):
        if self.expanded or self.anim.state() == QVariantAnimation.State.Running:
            return
        c = self.mapFromGlobal(QCursor.pos())
        dx, dy = c.x() - self._mascot_x(), c.y() - self.height() / 2
        k = min(1.0, math.hypot(dx, dy) / 220)
        ang = math.atan2(dy, dx)
        target = QPointF(math.cos(ang) * k, math.sin(ang) * k)
        self.eye_look += (target - self.eye_look) * 0.35
        if self._eye_dir == 0 and time.time() >= self._eye_next:
            self._eye_dir = 1
        if self._eye_dir == 1:
            self.eye_blink += 0.34
            if self.eye_blink >= 1:
                self.eye_blink, self._eye_dir = 1.0, -1
        elif self._eye_dir == -1:
            self.eye_blink -= 0.25
            if self.eye_blink <= 0:
                self.eye_blink, self._eye_dir = 0.0, 0
                self._eye_next = time.time() + (0.18 if random.random() < 0.2 else random.uniform(2.0, 5.0))
        self.update()

    def _tick_spin(self):
        self._angle = (time.time() * 45) % 360
        self.update()

    # ---- размеры / положение
    def rest_text(self):
        now, c = dt.datetime.now(), self.cfg
        sec = f":{now.second:02d}" if c["show_seconds"] else ""
        if c["clock_24h"]:
            return f"{now.hour:02d}:{now.minute:02d}{sec}"
        return f"{now.hour % 12 or 12}:{now.minute:02d}{sec} {'AM' if now.hour < 12 else 'PM'}"

    def _rest_font(self):
        f = QFont("Segoe UI Semibold", 1)
        f.setPixelSize(14)
        f.setBold(True)
        return f

    def rest_parts(self):
        """(показывать ли персонажа, показывать ли время) в состоянии покоя."""
        has = self.cfg.get("mascot", "eye") != "none"
        mode = self.cfg.get("rest_show", "both")
        act = self._activity() is not None
        if mode == "mascot" and has and not act:
            return True, False
        if mode == "time" or not has:
            return False, True
        return True, True

    def _mascot_x(self):
        if self.lyrics_active():
            return 16 + 2 + 11
        return (16 + 2 + 11) if self.rest_parts()[1] else self.width() / 2

    def collapsed_size(self):
        mascot, show_time = self.rest_parts()
        notch = self.cfg["style"] == "notch"
        if self.lyrics_active():
            return (LYR_W + 30, 40) if notch else (LYR_W, 46)
        # ширина всегда как у острова с часами, даже если часы скрыты — остров не «схлопывается»
        base, act = self.rest_text(), self._activity()
        if act and len(act[0]) > len(base):
            base = act[0]
        adv = QFontMetrics(self._rest_font()).horizontalAdvance(base.replace("1", "0"))
        extra = (30 if (mascot and show_time) else 0) + (18 if act else 0)
        return (adv + 70 + extra, 32) if notch else (adv + 44 + extra, 36)

    # ---- мониторы
    def _pick_screens(self):
        """Выбрать монитор(ы) по настройке «monitor»: main | second | both."""
        screens = QGuiApplication.screens()
        primary = QGuiApplication.primaryScreen()
        others = [s for s in screens if s is not primary]
        mode = self.cfg.get("monitor", "main")
        if mode == "second" and others:
            self.home = others[0]
        elif mode == "both":
            if self.home not in screens:
                self.home = primary
        else:
            self.home = primary
        want = [s for s in screens if s is not self.home] if mode == "both" else []
        for s in list(self.mirrors):
            if s not in want:
                self.mirrors.pop(s).close()
        for s in want:
            if s not in self.mirrors:
                self.mirrors[s] = IslandMirror(self, s)
        self.t_mirror.start() if self.mirrors else self.t_mirror.stop()

    def move_home(self, screen):
        """Перенести настоящий остров на другой монитор (режим «на всех»)."""
        if screen is self.home or screen not in QGuiApplication.screens():
            return
        self.home = screen
        self._pick_screens()
        self.place()
        self._tick_mirrors()

    def _tick_mirrors(self):
        if not self.mirrors:
            return
        if self.expanded:  # пока остров раскрыт — на других мониторах пусто
            for m in self.mirrors.values():
                m.hide()
            return
        pm = self.grab()
        g, hg = self.geometry(), self.home.geometry()
        for m in self.mirrors.values():
            m.sync(pm, g, hg)

    def place(self):
        scr = self.home.geometry()
        if self.windowHandle() is not None and self.windowHandle().screen() is not self.home:
            self.windowHandle().setScreen(self.home)
        w, h = max(1, round(self.cur_w)), max(1, round(self.cur_h))
        y = scr.top() + (0 if self.cfg["style"] == "notch" else int(self.cfg["top_margin"]))
        y -= round(self._hide_k * (h + (y - scr.top()) + 8))  # автоскрытие: уезжаем за верх экрана
        self.setGeometry(scr.left() + (scr.width() - w) // 2, y, w, h)
        self.content.move((w - EXP_W) // 2, 0)

    # ---- анимация
    def animate_to(self, target):
        expanding = target[0] > self.cur_w
        self._from, self._to = (self.cur_w, self.cur_h), target
        self.anim.stop()
        self.anim.setDuration(460 if expanding else 280)
        curve = QEasingCurve(QEasingCurve.Type.OutBack if expanding else QEasingCurve.Type.OutCubic)
        if expanding:
            curve.setOvershoot(0.8)
        self.anim.setEasingCurve(curve)
        self.anim.setStartValue(0.0)
        self.anim.setEndValue(1.0)
        self.anim.start()

    def _on_anim(self, t):
        t = float(t)
        self.cur_w = lerp(self._from[0], self._to[0], t)
        self.cur_h = lerp(self._from[1], self._to[1], t)
        self.place()
        self.update()

    def set_expanded(self, v):
        if v == self.expanded:
            return
        if v and not any(self.cfg["modules"].values()):
            return
        self.expanded = v
        self._in_ticks = self._out_ticks = 0
        if v:
            self._set_hidden(False)
            self.fx.setEnabled(True)
            self.fade.stop()
            self.fade.setStartValue(0.0)
            self.fade.setEndValue(1.0)
            self.fade.start()
            self.reveal.stop()
            self.content.show()
            self.animate_to((EXP_W, EXP_H))
        else:
            self.fade.stop()
            self.content.hide()
            self.animate_to(self.collapsed_size())

    # ---- конфиг-хелпер
    def set_cfg(self, key, value):
        self.cfg[key] = value
        C.save_config(self.cfg)
        self._mtime = C.config_mtime()

    # ---- Live Lyrics
    def pill_mode(self):
        """Что пилюля показывает в покое вместо времени: 'card' (название трека), 'lyrics' (строка песни) или None."""
        if not self.cfg.get("lyrics_on", True):
            return None
        if self.lyrics.card_text():
            return "card"
        return "lyrics" if self.lyrics.live() else None

    def lyrics_active(self):
        return self.pill_mode() is not None

    # ---- таймер / секундомер на пилюле
    def _activity(self):
        """Идущий таймер или секундомер: (текст, цвет, 'timer'|'sw', доля) — показывается вместо времени.
        Если идут оба, они сменяют друг друга каждые 3 секунды."""
        tp = getattr(self, "timer_page", None)  # страницы создаются позже, чем первый расчёт размера
        if tp is None or not self.cfg["modules"].get("timer", True):
            tp = None
        items = []
        rp = getattr(self, "rec_page", None)
        rec_t = rp.rec.pill_text() if rp is not None else None
        if rec_t:  # идёт запись экрана — красная точка и время записи
            items.append((rec_t, "#ff453a", "rec", 0.0))
        if tp is not None and tp.running:
            rem = max(0.0, tp._end - time.monotonic())
            frac = rem / tp.total if tp.total > 0 else 0.0
            items.append((fmt_hms(rem), "#ff453a" if rem <= 10 else "#ff9f0a", "timer", frac))
        if tp is not None and tp.sw_t0 is not None:
            sec = int(tp.sw_elapsed())
            hh, rest = divmod(sec, 3600)
            mm, ss = divmod(rest, 60)
            items.append((f"{hh}:{mm:02d}:{ss:02d}" if hh else f"{mm:02d}:{ss:02d}", "#30d158", "sw", 0.0))
        if not items:
            return None
        return items[int(time.time() // 3) % len(items)]

    def _draw_activity(self, p, x, cy, act, fm):
        """Значок (кольцо обратного отсчёта или пульсирующая точка) и время таймера/секундомера."""
        text, col, kind, frac = act
        c = QColor(col)
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        ring = QRectF(x + 1, cy - 6, 12, 12)
        track = QColor(c)
        track.setAlpha(70)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(track, 2.2))
        p.drawEllipse(ring)
        if kind == "timer":
            p.setPen(QPen(c, 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(ring, 90 * 16, -int(max(0.03, min(1.0, frac)) * 360 * 16))
        else:
            dot = QColor(c)
            dot.setAlpha(int(140 + 115 * (0.5 + 0.5 * math.sin(time.time() * 5))))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(dot)
            p.drawEllipse(QPointF(x + 7, cy), 2.8, 2.8)
        p.setFont(self._rest_font())
        p.setPen(c)
        p.drawText(QRectF(x + 18, 0, fm.horizontalAdvance(text) + 4, cy * 2),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
        p.restore()

    def _sync_rest_size(self):
        """Плавно подгоняем размер пилюли под то, что в ней сейчас (часы, таймер или текст песни)."""
        if self.expanded or self.anim.state() == QVariantAnimation.State.Running:
            return
        size = self.collapsed_size()
        if size != (self.cur_w, self.cur_h):
            self.animate_to(size)

    def _tick_lyrics(self):
        if self.expanded:
            return
        mode = self.pill_mode()
        if mode:
            if mode == "card":
                st = (-3, self.lyrics.card_text() or "", 0.0)
            else:
                st = self.lyrics.line_state()[:3]
            if (st[0], st[1]) != (self._ly_state[0], self._ly_state[1]):
                self._ly_prev, self._ly_t0 = self._ly_state[1], time.time()
            self._ly_state = st
        else:
            self._ly_state, self._ly_prev = (-2, "", 0.0), ""
        self._sync_rest_size()
        if mode:
            self.update()

    def _paint_lyrics(self, p, w, h, th):
        mascot = self.rest_parts()[0]
        if mascot:
            draw_mascot(p, self.cfg.get("mascot", "eye"), 16 + th + 11, h / 2, self.eye_look, self.eye_blink)
        x0 = (16 + th + 22 + 12) if mascot else 22
        right = w - 46  # справа — эквалайзер
        act = self._activity()
        if act:  # идёт таймер/секундомер — он остаётся виден справа от текста
            fm_a = QFontMetrics(self._rest_font())
            cw = 18 + fm_a.horizontalAdvance(act[0])
            self._draw_activity(p, right - cw, h / 2, act, fm_a)
            right -= cw + 14
        draw_eq(p, w - 26, h / 2, self.lyrics.accent, self.lyrics.playing, 12.0)
        avail = max(40, right - x0)
        lit = mix(self.lyrics.accent, QColor("#ffffff"), 0.6)
        idx, text, prog = self._ly_state
        t = min(1.0, (time.time() - self._ly_t0) / 0.34)
        e = 1 - (1 - t) ** 3  # плавное замедление

        def draw(txt, dy, alpha, wipe):
            if not txt or alpha <= 0.01:
                return
            f = QFont("Segoe UI")
            f.setBold(True)
            size = 15
            f.setPixelSize(size)
            fm = QFontMetrics(f)
            while fm.horizontalAdvance(txt) > avail and size > 11:  # длинную строку слегка ужимаем
                size -= 1
                f.setPixelSize(size)
                fm = QFontMetrics(f)
            txt = fm.elidedText(txt, Qt.TextElideMode.ElideRight, int(avail))
            tw = fm.horizontalAdvance(txt)
            left = x0 + (avail - tw) / 2
            rect = QRectF(left, dy, tw + 2, h)
            flags = Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft
            p.setFont(f)
            p.setOpacity(alpha)
            if wipe is None:
                p.setPen(QColor("#ffffff"))
                p.drawText(rect, flags, txt)
                return
            p.setPen(QColor("#6e6e73"))
            p.drawText(rect, flags, txt)
            if wipe > 0:  # караоке-заливка слева направо цветом обложки
                p.save()
                p.setClipRect(QRectF(left, 0, tw * wipe, h), Qt.ClipOperation.IntersectClip)
                p.setPen(lit)
                p.drawText(rect, flags, txt)
                p.restore()

        p.save()
        p.setClipRect(QRectF(x0 - 4, 0, avail + 8, h), Qt.ClipOperation.IntersectClip)
        draw(self._ly_prev, -e * 14, 1 - e, None)
        draw(text, (1 - e) * 14, e, None if idx == -3 else (prog if idx >= 0 else 0.0))
        p.restore()

    def shutdown(self):
        for m in self.mirrors.values():
            m.close()
        self.rec_page.shutdown()

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Type.MouseButtonPress and self.isVisible() and self.cfg.get("click_ripple", True):
            try:
                pt = self.mapFromGlobal(ev.globalPosition().toPoint())
                if self.rect().contains(pt):
                    self.click_fx.spawn(pt.x(), pt.y(), self.expanded)
            except Exception:
                pass
        return False

    def _on_island_toggled(self, on):
        self.set_cfg("lyrics_on", bool(on))
        self.apply_config()

    def _on_clip_pause(self, paused):
        self.set_cfg("clip_pause", bool(paused))
        self.apply_config()

    def _on_offset_step(self, delta):
        off = max(-10.0, min(10.0, round(self.lyrics.offset + delta, 2)))
        self.lyrics.offset = off
        self.set_cfg("lyrics_offset_ms", int(off * 1000))
        self.lyrics.changed.emit()

    # ---- автоскрытие
    def _set_hidden(self, hidden):
        """Плавно спрятать остров за верх экрана или вернуть на место."""
        if hidden == self._hidden:
            return
        self._hidden = hidden
        self._hide_ticks = 0
        a = self.hide_anim
        a.stop()
        a.setDuration(260 if hidden else 220)
        a.setEasingCurve(QEasingCurve(QEasingCurve.Type.InCubic if hidden else QEasingCurve.Type.OutCubic))
        a.setStartValue(self._hide_k)
        a.setEndValue(1.0 if hidden else 0.0)
        a.start()

    def _on_hide_anim(self, v):
        self._hide_k = float(v)
        self.place()

    def _cursor_at_top_edge(self, screen=None):
        """Курсор прижат к верху экрана напротив острова (с запасом по бокам)."""
        scr = (screen or self.home).geometry()
        pos = QCursor.pos()
        if pos.y() > scr.top() + HIDE_EDGE:
            return False
        half = max(self.cur_w, self.collapsed_size()[0]) / 2 + HIDE_ZONE_PAD
        return abs(pos.x() - (scr.left() + scr.width() / 2)) <= half

    def _tick_autohide(self):
        """Автоскрытие: в покое остров прячется, у верхнего края экрана — появляется."""
        if not self.cfg.get("autohide", False):
            return
        busy = self.expanded or self.hold or self._drag_in or BUSY["v"]
        if not busy:  # режим «на всех»: курсор у верха другого монитора — остров переезжает туда
            for s in list(self.mirrors):
                if self._cursor_at_top_edge(s):
                    self.move_home(s)
                    break
        if busy or self._cursor_at_top_edge() or (not self._hidden and self._cursor_inside()):
            self._hide_ticks = 0
            self._set_hidden(False)
        elif not self._hidden:
            self._hide_ticks += 1
            if self._hide_ticks >= HIDE_DELAY_TICKS:
                self._set_hidden(True)

    # ---- наведение / клик (опрашиваем курсор — надёжнее enter/leave при перетаскивании)
    def _cursor_inside(self):
        g = self.frameGeometry()
        top = -(int(self.cfg["top_margin"]) + 2) if self.cfg["style"] == "pill" else -2
        g = g.adjusted(-12, top, 12, 18) if self.expanded else g.adjusted(-8, top, 8, 8)
        return g.contains(QCursor.pos())

    def _tick_hover(self):
        if self.mirrors and not self.expanded and self.cfg["expand_on"] == "hover" \
                and not QApplication.mouseButtons():
            pos = QCursor.pos()
            for s, m in self.mirrors.items():
                if m.isVisible() and m.frameGeometry().adjusted(-8, -20, 8, 8).contains(pos) and self._hide_k < 0.05:
                    self.move_home(s)
                    break
        inside = self._cursor_inside()
        if self.expanded:
            if inside or self._drag_in or BUSY["v"] or self.hold:
                self._out_ticks = 0
            else:
                self._out_ticks += 1
                if self._out_ticks >= 7:
                    self.set_expanded(False)
        elif self.cfg["expand_on"] == "hover" and inside and not QApplication.mouseButtons() and self._hide_k < 0.05:
            self._in_ticks += 1
            if self._in_ticks >= 2:
                self.set_expanded(True)
        else:
            self._in_ticks = 0

    def mousePressEvent(self, e):
        if not self.expanded and e.button() == Qt.MouseButton.LeftButton:
            self.set_expanded(True)

    # ---- перетаскивание файлов на остров
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() and self.cfg["modules"].get("shelf", True):
            e.acceptProposedAction()
            self._drag_in = True
            self.show_page("shelf")
            self.set_expanded(True)

    def dragMoveEvent(self, e):
        e.acceptProposedAction()

    def dragLeaveEvent(self, e):
        self._drag_in = False

    def dropEvent(self, e):
        self._drag_in = False
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        self.shelf_page.add(paths)
        e.acceptProposedAction()

    # ---- рисование
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        notch = self.cfg["style"] == "notch"
        cw, ch = self.collapsed_size()
        k = max(0.0, min(1.0, (h - ch) / max(1, EXP_H - ch)))
        rest_r = 14 if notch else ch / 2
        r = lerp(rest_r, 30, k)

        colors = C.border_colors(self.cfg)
        rim = not C.is_black(colors)
        th = C.THICKNESS.get(self.cfg["thickness"], 2.5) if rim else 0
        inset = th / 2
        if notch:
            rect = QRectF(inset, -r - 6, w - 2 * inset, h + r + 6 - inset)
        else:
            rect = QRectF(inset, inset, w - 2 * inset, h - 2 * inset)
        path = QPainterPath()
        path.addRoundedRect(rect, r, r)

        alpha = round(255 * max(50, min(100, int(self.cfg["opacity"]))) / 100)
        p.fillPath(path, QColor(0, 0, 0, alpha))

        if rim:
            grad = QConicalGradient(QPointF(w / 2, h / 2), self._angle if self.cfg["moving_gradient"] else 45)
            n = len(colors)
            for i, c in enumerate(colors):
                grad.setColorAt(i / (n - 1) if n > 1 else 0, QColor(c))
            p.setPen(QPen(grad, th))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)

        if not self.expanded and k < 0.25 and self.lyrics_active():
            self._paint_lyrics(p, w, h, th)
        elif not self.expanded and k < 0.25:
            mascot, show_time = self.rest_parts()
            off = 30 if (mascot and show_time) else 0
            if mascot:
                mx = (16 + th + 11) if show_time else w / 2
                draw_mascot(p, self.cfg.get("mascot", "eye"), mx, h / 2, self.eye_look, self.eye_blink)
            if show_time:
                act = self._activity()
                p.setFont(self._rest_font())
                if act:  # идёт таймер или секундомер — показываем его вместо времени
                    fm_a = QFontMetrics(self._rest_font())
                    total = 18 + fm_a.horizontalAdvance(act[0])
                    self._draw_activity(p, off + (w - off - total) / 2, h / 2, act, fm_a)
                else:
                    p.setPen(QColor("#ffffff"))
                    p.drawText(QRectF(off, 0, w - off, h), Qt.AlignmentFlag.AlignCenter, self.rest_text())


def make_tray_icon():
    ico = Path(__file__).with_name("icon.png")   # фирменная иконка, если лежит рядом
    if ico.exists():
        return QIcon(str(ico))
    pm = QPixmap(64, 64)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#111111"))
    p.drawRoundedRect(4, 18, 56, 28, 14, 14)
    p.setBrush(QColor("#ffffff"))
    p.drawEllipse(40, 26, 12, 12)
    p.end()
    return QIcon(pm)


def main():
    if os.name == "nt":  # своя иконка в панели задач, а не питоновская
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ASTRO.DynamicIsland")
        except Exception:
            pass
    app = QApplication(sys.argv)
    app.setWindowIcon(make_tray_icon())
    app.setQuitOnLastWindowClosed(False)
    app.setFont(QFont("Segoe UI", 10))

    C.CFG_DIR.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(C.CFG_DIR / "island.lock"))
    if not lock.tryLock(200):
        return 0  # остров уже запущен

    island = Island()
    app.aboutToQuit.connect(island.shutdown)

    tray = QSystemTrayIcon(make_tray_icon(), app)
    tray.setToolTip("Dynamic Island by ASTRO")
    menu = QMenu()
    act_settings = QAction("Настройки", menu)
    act_settings.triggered.connect(island.open_settings)
    act_quit = QAction("Выход", menu)
    act_quit.triggered.connect(app.quit)
    menu.addAction(act_settings)
    menu.addSeparator()
    menu.addAction(act_quit)
    tray.setContextMenu(menu)
    tray.show()

    code = app.exec()
    lock.unlock()
    return code


if __name__ == "__main__":
    sys.exit(main())
