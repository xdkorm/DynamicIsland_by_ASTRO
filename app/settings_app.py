"""Dynamic Island — приложение настроек (отдельное окно).
Запускается с острова (шестерёнка) или напрямую:  python settings_app.py
Каждое изменение сразу пишется в конфиг, остров подхватывает его за долю секунды.
"""
import os
import sys
from pathlib import Path

from PySide6.QtCore import QLockFile, QPointF, QRect, QRectF, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QAbstractButton, QApplication, QColorDialog, QFileDialog, QFrame, QGridLayout, QHBoxLayout,
                               QLabel, QLineEdit, QPushButton, QScrollArea, QSlider, QStackedWidget,
                               QVBoxLayout, QWidget)

import common as C
from mascots import MASCOTS, draw_mascot, set_repo_color

QSS = """
* { font-family: 'Segoe UI Variable Text','Segoe UI'; color:#f2f2f7; font-size:14px; }
QWidget#root { background:#141418; }
QWidget#side { background:#101014; }
QScrollArea, QScrollArea > QWidget > QWidget { background:transparent; border:none; }
QFrame#card { background:#1d1d23; border:1px solid #2a2a32; border-radius:14px; }
QLabel#title { font-size:26px; font-weight:700; }
QLabel#sect { font-size:11px; font-weight:600; color:#6e6e73; }
QLabel#desc { color:#8e8e93; font-size:13px; }
QLabel#rowt { font-size:15px; font-weight:600; }
QPushButton#nav { text-align:left; padding:10px 14px; border:none; border-radius:10px;
                  background:transparent; font-size:15px; }
QPushButton#nav:hover { background:#1a1a20; }
QPushButton#nav:checked { background:#25252d; }
QLineEdit { background:#25252d; border:1px solid #3a3a44; border-radius:8px; padding:6px 10px; }
QLineEdit:focus { border-color:#8b7cf6; }
QPushButton#seg { padding:6px 16px; border:none; border-radius:8px; background:transparent; color:#aeaeb2; }
QPushButton#seg:checked { background:#3a3a44; color:#fff; }
QFrame#segbar { background:#25252d; border-radius:10px; }
QSlider::groove:horizontal { height:4px; background:#3a3a44; border-radius:2px; }
QSlider::sub-page:horizontal { background:#8b7cf6; border-radius:2px; }
QSlider::handle:horizontal { background:#fff; width:16px; height:16px; margin:-6px 0; border-radius:8px; }
QScrollBar:vertical { width:8px; background:transparent; }
QScrollBar::handle:vertical { background:#3a3a44; border-radius:4px; min-height:30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }
"""


# ───────────────────────── виджеты ─────────────────────────
class Toggle(QAbstractButton):
    def __init__(self, checked=False):
        super().__init__()
        self.setCheckable(True)
        self.setChecked(checked)
        self.setFixedSize(44, 24)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._pos = 1.0 if checked else 0.0
        self.anim = QVariantAnimation(self, duration=160)
        self.anim.valueChanged.connect(self._set)
        self.toggled.connect(self._go)

    def _set(self, v):
        self._pos = float(v)
        self.update()

    def _go(self, on):
        self.anim.stop()
        self.anim.setStartValue(self._pos)
        self.anim.setEndValue(1.0 if on else 0.0)
        self.anim.start()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        off, on = QColor("#3a3a44"), QColor("#8b7cf6")
        col = QColor(int(off.red() + (on.red() - off.red()) * self._pos),
                     int(off.green() + (on.green() - off.green()) * self._pos),
                     int(off.blue() + (on.blue() - off.blue()) * self._pos))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)
        p.drawRoundedRect(self.rect(), 12, 12)
        p.setBrush(QColor("#ffffff"))
        x = 3 + (self.width() - 24) * self._pos
        p.drawEllipse(QPointF(x + 9, 12), 9, 9)


class Segmented(QFrame):
    changed = Signal(str)

    def __init__(self, options, current):
        super().__init__()
        self.setObjectName("segbar")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(2)
        self.btns = {}
        for key, label in options:
            b = QPushButton(label)
            b.setObjectName("seg")
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setChecked(key == current)
            b.clicked.connect(lambda _=False, k=key: self.select(k))
            self.btns[key] = b
            lay.addWidget(b)

    def select(self, key):
        for k, b in self.btns.items():
            b.setChecked(k == key)
        self.changed.emit(key)


class Swatch(QAbstractButton):
    def __init__(self, colors, selected=False, glyph=""):
        super().__init__()
        self.colors = colors
        self.glyph = glyph
        self.selected = selected
        self.setFixedSize(34, 34)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_selected(self, v):
        self.selected = v
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(5, 5, 24, 24)
        g = QLinearGradient(r.topLeft(), r.bottomRight())
        n = len(self.colors)
        for i, c in enumerate(self.colors):
            g.setColorAt(i / (n - 1) if n > 1 else 0, QColor(c))
        p.setPen(QPen(QColor("#3a3a44"), 1) if self.colors == ["#000000"] else Qt.PenStyle.NoPen)
        p.setBrush(g)
        p.drawEllipse(r)
        if self.glyph:
            p.setPen(QColor("#ffffff"))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, self.glyph)
        if self.selected:
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor("#ffffff"), 2))
            p.drawEllipse(QRectF(1.5, 1.5, 31, 31))


class MascotTile(QAbstractButton):
    palette_clicked = Signal()   # нажали на кнопку-палитру (есть только у R.E.P.O)

    def __init__(self, key, name, selected):
        super().__init__()
        self.key, self.selected = key, selected
        self.setFixedSize(62, 52)
        self.setToolTip(name)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._pal_hover = False
        self.setMouseTracking(key == "repo")

    def _pal_rect(self):
        return QRect(self.width() - 20, 3, 17, 17)

    def hitButton(self, pos):
        return not (self.key == "repo" and self._pal_rect().contains(pos))

    def mousePressEvent(self, e):
        if self.key == "repo" and self._pal_rect().contains(e.position().toPoint()):
            self.palette_clicked.emit()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self.key == "repo":
            h = self._pal_rect().contains(e.position().toPoint())
            if h != self._pal_hover:
                self._pal_hover = h
                self.setCursor(Qt.CursorShape.PointingHandCursor)
                self.setToolTip("Сменить цвет робота" if h else "Робот (R.E.P.O)")
                self.update()
        super().mouseMoveEvent(e)

    def leaveEvent(self, e):
        self._pal_hover = False
        self.update()
        super().leaveEvent(e)

    def set_selected(self, v):
        self.selected = v
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor("#8b7cf6" if self.selected else "#2a2a32"), 2))
        p.setBrush(QColor("#000000"))
        p.drawRoundedRect(QRectF(1, 1, self.width() - 2, self.height() - 2), 12, 12)
        if self.key == "none":
            p.setPen(QColor("#8e8e93"))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Нет")
        else:
            draw_mascot(p, self.key, self.width() / 2, self.height() / 2, QPointF(0.0, 0.25), 0.0)
        if self.key == "repo":  # мини-палитра в углу
            r = QRectF(self._pal_rect())
            p.setPen(QPen(QColor("#8b7cf6" if self._pal_hover else "#3a3a44"), 1))
            p.setBrush(QColor("#25252d" if self._pal_hover else "#1d1d23"))
            p.drawEllipse(r)
            p.setPen(Qt.PenStyle.NoPen)
            c = r.center()
            for dx, dy, col in ((-3.2, -2.6, "#ff5f57"), (3.2, -2.6, "#febc2e"),
                                (-3.6, 2.4, "#28c840"), (2.8, 3.0, "#3d8bff")):
                p.setBrush(QColor(col))
                p.drawEllipse(QPointF(c.x() + dx, c.y() + dy), 2.1, 2.1)


class StylePreview(QWidget):
    def __init__(self, kind):
        super().__init__()
        self.kind = kind
        self.setFixedHeight(84)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(self.rect()), 10, 10)
        p.setClipPath(clip)
        g = QLinearGradient(0, 0, self.width(), self.height())
        g.setColorAt(0, QColor("#15151d"))
        g.setColorAt(1, QColor("#3b3470"))
        p.fillRect(self.rect(), g)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#000000"))
        cx = self.width() / 2
        if self.kind == "notch":
            p.drawRoundedRect(QRectF(cx - 48, -12, 96, 38), 12, 12)
        else:
            rim = QLinearGradient(cx - 40, 0, cx + 40, 0)
            rim.setColorAt(0, QColor("#ff9a3c"))
            rim.setColorAt(0.5, QColor("#c44dff"))
            rim.setColorAt(1, QColor("#3d8bff"))
            p.setPen(QPen(rim, 2))
            p.drawRoundedRect(QRectF(cx - 40, 10, 80, 22), 11, 11)
            p.setPen(QColor("#ffffff"))
            f = QFont("Segoe UI", 1)
            f.setPixelSize(11)
            f.setBold(True)
            p.setFont(f)
            p.drawText(QRectF(cx - 40, 10, 80, 22), Qt.AlignmentFlag.AlignCenter, "9:41")


class StyleCard(QFrame):
    clicked = Signal(str)

    def __init__(self, key, title, desc, selected):
        super().__init__()
        self.key = key
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(6)
        prev = StylePreview(key)
        prev.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        t = QLabel(title)
        t.setObjectName("rowt")
        d = QLabel(desc)
        d.setObjectName("desc")
        d.setWordWrap(True)
        for w in (t, d):
            w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        lay.addWidget(prev)
        lay.addWidget(t)
        lay.addWidget(d)
        lay.addStretch()
        self.set_selected(selected)

    def set_selected(self, v):
        border = "#8b7cf6" if v else "#2a2a32"
        self.setStyleSheet(f"StyleCard{{background:#1d1d23;border:2px solid {border};border-radius:14px;}}")

    def mousePressEvent(self, _):
        self.clicked.emit(self.key)


def card():
    f = QFrame()
    f.setObjectName("card")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(20, 16, 20, 16)
    lay.setSpacing(14)
    return f, lay


def row(title, desc, widget):
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    left = QVBoxLayout()
    left.setSpacing(2)
    t = QLabel(title)
    t.setObjectName("rowt")
    left.addWidget(t)
    if desc:
        d = QLabel(desc)
        d.setObjectName("desc")
        d.setWordWrap(True)
        left.addWidget(d)
    lay.addLayout(left, 1)
    lay.addSpacing(16)
    lay.addWidget(widget, 0, Qt.AlignmentFlag.AlignVCenter)
    return w


def page(title):
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    body = QWidget()
    lay = QVBoxLayout(body)
    lay.setContentsMargins(36, 28, 36, 28)
    lay.setSpacing(16)
    t = QLabel(title)
    t.setObjectName("title")
    lay.addWidget(t)
    scroll.setWidget(body)
    return scroll, lay


def autostart_set(enabled: bool):
    """Автозапуск острова вместе с Windows (HKCU\\...\\Run)."""
    try:
        import winreg
    except ImportError:
        return
    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run",
                         0, winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE)
    try:
        if enabled:
            exe = Path(sys.executable)
            ai = exe.with_name("AstroIsland.exe")
            pw = exe.with_name("pythonw.exe")
            if ai.exists():
                exe = ai
            elif pw.exists():
                exe = pw
            script = Path(__file__).with_name("island.py")
            winreg.SetValueEx(key, C.APP_NAME, 0, winreg.REG_SZ, f'"{exe}" "{script}"')
        else:
            try:
                winreg.DeleteValue(key, C.APP_NAME)
            except FileNotFoundError:
                pass
    finally:
        winreg.CloseKey(key)


def autostart_get() -> bool:
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run")
        winreg.QueryValueEx(key, C.APP_NAME)
        winreg.CloseKey(key)
        return True
    except Exception:
        return False


# ───────────────────────── окно ─────────────────────────
class SettingsWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName("root")
        self.setWindowTitle("Dynamic Island by ASTRO · Настройки")
        self.resize(940, 700)
        self.setMinimumSize(760, 520)
        self.cfg = C.load_config()
        set_repo_color(self.cfg.get("repo_color"))

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        side = QWidget()
        side.setObjectName("side")
        side.setFixedWidth(220)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(14, 18, 14, 18)
        sl.setSpacing(4)
        head = QLabel("Dynamic Island<br><span style='font-size:12px;color:#8e8e93;font-weight:600'>by ASTRO</span>")
        head.setStyleSheet("font-size:15px;font-weight:700;padding:4px 8px 12px 8px;")
        sl.addWidget(head)

        self.stack = QStackedWidget()
        self.nav = []
        pages = [("Основные", self._general()), ("Внешний вид", self._appearance()),
                 ("Запись экрана", self._record()), ("Модули", self._modules()), ("О программе", self._about())]
        for i, (name, w) in enumerate(pages):
            b = QPushButton(name)
            b.setObjectName("nav")
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, idx=i: self.go(idx))
            sl.addWidget(b)
            self.nav.append(b)
            self.stack.addWidget(w)
        sl.addStretch()
        root.addWidget(side)
        root.addWidget(self.stack, 1)
        self.go(1)

    def go(self, i):
        self.stack.setCurrentIndex(i)
        for k, b in enumerate(self.nav):
            b.setChecked(k == i)

    def set(self, key, value):
        self.cfg[key] = value
        C.save_config(self.cfg)

    def set_module(self, key, value):
        self.cfg["modules"][key] = value
        C.save_config(self.cfg)

    # ---- Основные
    def _general(self):
        sc, lay = page("Основные")
        c, cl = card()

        t = Toggle(autostart_get())
        t.toggled.connect(autostart_set)
        cl.addWidget(row("Запускать вместе с Windows", "Остров появится сам после входа в систему.", t))

        seg = Segmented([("hover", "При наведении"), ("click", "По клику")], self.cfg["expand_on"])
        seg.changed.connect(lambda v: self.set("expand_on", v))
        cl.addWidget(row("Раскрывать остров", "Как открывать остров из спокойного состояния.", seg))

        mons = QApplication.screens()
        names = ", ".join(f"{i + 1}: {s.geometry().width()}×{s.geometry().height()}" for i, s in enumerate(mons))
        mseg = Segmented([("main", "Основной"), ("second", "Второй"), ("both", "На всех")],
                         self.cfg.get("monitor", "main"))
        mseg.changed.connect(lambda v: self.set("monitor", v))
        cl.addWidget(row("Монитор для острова",
                         f"Основной — главный монитор Windows, второй — другой. «На всех» — остров виден на каждом, "
                         f"а раскрывается там, куда наведёшь курсор. Найдено мониторов: {len(mons)} ({names}).", mseg))

        tw_on = Toggle(self.cfg.get("weather_on", True))
        tw_on.toggled.connect(lambda v: self.set("weather_on", v))
        cl.addWidget(row("Погода на вкладке «Часы»", "Температура и состояние неба справа от времени (данные Open-Meteo).", tw_on))

        city = QLineEdit(self.cfg.get("weather_city", ""))
        city.setPlaceholderText("Авто (по IP)")
        city.setFixedWidth(200)
        city.editingFinished.connect(lambda: self.set("weather_city", city.text().strip()))
        cl.addWidget(row("Город для погоды", "Например: Костанай. Пусто — определить автоматически.", city))

        t24 = Toggle(self.cfg["clock_24h"])
        t24.toggled.connect(lambda v: self.set("clock_24h", v))
        cl.addWidget(row("24-часовой формат", "Выключи, чтобы показывать AM / PM.", t24))

        ts = Toggle(self.cfg["show_seconds"])
        ts.toggled.connect(lambda v: self.set("show_seconds", v))
        cl.addWidget(row("Показывать секунды", "", ts))

        tw = Toggle(self.cfg["week_starts_monday"])
        tw.toggled.connect(lambda v: self.set("week_starts_monday", v))
        cl.addWidget(row("Неделя начинается с понедельника", "Иначе — с воскресенья.", tw))

        tl = Toggle(self.cfg["lyrics_on"])
        tl.toggled.connect(lambda v: self.set("lyrics_on", v))
        cl.addWidget(row("Бегущий текст песен на острове", "Пока играет музыка, остров превращается в мини-караоке. Тексты берутся из открытого сервиса LRCLIB.", tl))

        tah = Toggle(self.cfg["autohide"])
        tah.toggled.connect(lambda v: self.set("autohide", v))
        cl.addWidget(row("Скрывать остров в покое", "Остров уезжает за верх экрана и появляется, когда подвёл курсор к верхнему краю.", tah))

        sn = Toggle(self.cfg["alarm_sound"])
        sn.toggled.connect(lambda v: self.set("alarm_sound", v))
        cl.addWidget(row("Звук таймера и напоминаний", "Сигнал, пока не нажмёшь «Ок» на острове.", sn))

        tcp = Toggle(self.cfg["clip_persist"])
        tcp.toggled.connect(lambda v: self.set("clip_persist", v))
        trp = Toggle(self.cfg["tab_ripple"])
        trp.toggled.connect(lambda v: self.set("tab_ripple", v))
        tcr = Toggle(self.cfg["click_ripple"])
        tcr.toggled.connect(lambda v: self.set("click_ripple", v))
        cl.addWidget(row("Анимация при клике", "Под курсором расходится круг, когда нажимаешь на остров.", tcr))

        cl.addWidget(row("Анимация переключения вкладок", "Новая вкладка раскрывается кругом от места клика.", trp))

        cl.addWidget(row("Помнить историю буфера после перезапуска", "Выключи, чтобы история жила только пока запущен остров. Пароли из менеджеров паролей не записываются.", tcp))

        lay.addWidget(c)
        lay.addStretch()
        return sc

    # ---- Внешний вид
    def _appearance(self):
        sc, lay = page("Внешний вид")

        c, cl = card()
        t = QLabel("Стиль")
        t.setObjectName("rowt")
        d = QLabel("Как остров выглядит на экране.")
        d.setObjectName("desc")
        cl.addWidget(t)
        cl.addWidget(d)
        cards = QHBoxLayout()
        cards.setSpacing(12)
        self.style_cards = {}
        for key, title, desc in [
            ("notch", "Dynamic Island", "Свисает с верхнего края, как вырез на экране."),
            ("pill", "Windows Island", "Плавающая таблетка с часами и цветной рамкой."),
        ]:
            sc_ = StyleCard(key, title, desc, self.cfg["style"] == key)
            sc_.clicked.connect(self._pick_style)
            self.style_cards[key] = sc_
            cards.addWidget(sc_)
        cl.addLayout(cards)
        lay.addWidget(c)

        cm, cml = card()
        mt_ = QLabel("Кто живёт на острове")
        mt_.setObjectName("rowt")
        md_ = QLabel("Персонаж в покое смотрит за курсором и моргает.")
        md_.setObjectName("desc")
        cml.addWidget(mt_)
        cml.addWidget(md_)
        tiles = QGridLayout()
        tiles.setSpacing(8)
        self.mascot_tiles = {}
        for n, (key, name) in enumerate([("none", "Никого")] + [(k, nm) for k, nm, _ in MASCOTS]):
            tile = MascotTile(key, name, self.cfg["mascot"] == key)
            tile.clicked.connect(lambda _=False, k=key: self._pick_mascot(k))
            self.mascot_tiles[key] = tile
            if key == "repo":
                tile.palette_clicked.connect(self._pick_repo_color)
            tiles.addWidget(tile, n // 6, n % 6)
        tiles.setColumnStretch(6, 1)
        cml.addLayout(tiles)
        rs = Segmented([("both", "Персонаж и время"), ("mascot", "Только персонаж"), ("time", "Только время")],
                       self.cfg["rest_show"])
        rs.changed.connect(lambda v: self.set("rest_show", v))
        cml.addWidget(row("Что показывать в покое", "Остров можно оставить с одним глазком, без времени.", rs))
        lay.addWidget(cm)

        c2, c2l = card()
        top = QHBoxLayout()
        left = QVBoxLayout()
        left.setSpacing(2)
        bt = QLabel("Рамка")
        bt.setObjectName("rowt")
        bd = QLabel("Остров остаётся чёрным — меняется только цвет рамки.")
        bd.setObjectName("desc")
        left.addWidget(bt)
        left.addWidget(bd)
        top.addLayout(left, 1)
        self.border_name = QLabel(C.border_name(self.cfg))
        self.border_name.setObjectName("desc")
        top.addWidget(self.border_name)
        c2l.addLayout(top)

        sw_row = QHBoxLayout()
        sw_row.setSpacing(10)
        self.swatches = {}
        for key, (name, colors) in C.BORDERS.items():
            s = Swatch(colors, self.cfg["border"] == key)
            s.setToolTip(name)
            s.clicked.connect(lambda _=False, k=key: self._pick_border(k))
            self.swatches[key] = s
            sw_row.addWidget(s)
        custom_cols = C.border_colors({"border": "custom", "custom_color": self.cfg["custom_color"]})
        self.custom_sw = Swatch(custom_cols, self.cfg["border"] == "custom", "✎")
        self.custom_sw.setToolTip("Свой цвет")
        self.custom_sw.clicked.connect(self._pick_custom)
        sw_row.addWidget(self.custom_sw)
        sw_row.addStretch()
        c2l.addLayout(sw_row)

        thick = Segmented([("thin", "Тонкая"), ("medium", "Средняя"), ("thick", "Толстая")],
                          self.cfg["thickness"])
        thick.changed.connect(lambda v: self.set("thickness", v))
        c2l.addWidget(row("Толщина", "", thick))

        mg = Toggle(self.cfg["moving_gradient"])
        mg.toggled.connect(lambda v: self.set("moving_gradient", v))
        c2l.addWidget(row("Бегущий градиент", "Цвета медленно кружат вокруг острова. Чуть больше нагружает процессор.", mg))
        lay.addWidget(c2)

        c3, c3l = card()
        self.op_label = QLabel(f"{self.cfg['opacity']}%")
        self.op_label.setObjectName("desc")
        op = QSlider(Qt.Orientation.Horizontal)
        op.setRange(50, 100)
        op.setValue(int(self.cfg["opacity"]))
        op.setFixedWidth(200)
        op.valueChanged.connect(lambda v: (self.op_label.setText(f"{v}%"),
                                           None if op.isSliderDown() else self.set("opacity", v)))
        op.sliderReleased.connect(lambda: self.set("opacity", op.value()))
        box = QWidget()
        bl = QHBoxLayout(box)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.addWidget(op)
        bl.addWidget(self.op_label)
        c3l.addWidget(row("Непрозрачность фона", "Меньше — остров полупрозрачный.", box))

        mt = QSlider(Qt.Orientation.Horizontal)
        mt.setRange(0, 40)
        mt.setValue(int(self.cfg["top_margin"]))
        mt.setFixedWidth(200)
        self.mt_label = QLabel(f"{self.cfg['top_margin']} px")
        self.mt_label.setObjectName("desc")
        mt.valueChanged.connect(lambda v: (self.mt_label.setText(f"{v} px"),
                                           None if mt.isSliderDown() else self.set("top_margin", v)))
        mt.sliderReleased.connect(lambda: self.set("top_margin", mt.value()))
        box2 = QWidget()
        b2 = QHBoxLayout(box2)
        b2.setContentsMargins(0, 0, 0, 0)
        b2.addWidget(mt)
        b2.addWidget(self.mt_label)
        c3l.addWidget(row("Отступ от верха", "Только для стиля Windows Island.", box2))
        lay.addWidget(c3)
        lay.addStretch()
        return sc

    def _pick_repo_color(self):
        col = QColorDialog.getColor(QColor(self.cfg.get("repo_color", "#6e7f6e")), self, "Цвет робота R.E.P.O")
        if col.isValid():
            self.set("repo_color", col.name())
            set_repo_color(col.name())
            self.mascot_tiles["repo"].update()

    def _pick_mascot(self, key):
        for k, tl in self.mascot_tiles.items():
            tl.set_selected(k == key)
        self.set("mascot", key)

    def _pick_style(self, key):
        for k, c in self.style_cards.items():
            c.set_selected(k == key)
        self.set("style", key)

    def _refresh_border_ui(self):
        for k, s in self.swatches.items():
            s.set_selected(self.cfg["border"] == k)
        self.custom_sw.set_selected(self.cfg["border"] == "custom")
        self.border_name.setText(C.border_name(self.cfg))

    def _pick_border(self, key):
        self.cfg["border"] = key
        C.save_config(self.cfg)
        self._refresh_border_ui()

    def _pick_custom(self):
        col = QColorDialog.getColor(QColor(self.cfg["custom_color"]), self, "Цвет рамки")
        if col.isValid():
            self.cfg["custom_color"] = col.name()
            self.cfg["border"] = "custom"
            C.save_config(self.cfg)
            self.custom_sw.colors = C.border_colors(self.cfg)
            self._refresh_border_ui()

    # ---- Запись экрана
    def _record(self):
        sc, lay = page("Запись экрана")
        c, cl = card()
        btn_qss = ("QPushButton{background:#3a3a44;border:none;border-radius:8px;padding:7px 14px}"
                   "QPushButton:hover{background:#4a4a56}")

        fps = Segmented([("24", "24"), ("30", "30"), ("60", "60")], str(self.cfg["rec_fps"]))
        fps.changed.connect(lambda v: self.set("rec_fps", int(v)))
        cl.addWidget(row("Кадров в секунду", "60 плавнее, но сильнее грузит процессор и делает файл больше.", fps))

        cur = Toggle(self.cfg["rec_cursor"])
        cur.toggled.connect(lambda v: self.set("rec_cursor", v))
        cl.addWidget(row("Показывать курсор в записи", "", cur))

        box = QWidget()
        bl = QHBoxLayout(box)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(8)
        self.rec_dir_lbl = QLabel()
        self.rec_dir_lbl.setObjectName("desc")
        self.rec_dir_lbl.setWordWrap(True)
        pick = QPushButton("Выбрать…")
        pick.setStyleSheet(btn_qss)
        pick.setCursor(Qt.CursorShape.PointingHandCursor)
        pick.clicked.connect(self._pick_rec_dir)
        reset = QPushButton("Сбросить")
        reset.setStyleSheet(btn_qss)
        reset.setCursor(Qt.CursorShape.PointingHandCursor)
        reset.clicked.connect(lambda: self._set_rec_dir(""))
        bl.addWidget(pick)
        bl.addWidget(reset)
        cl.addWidget(row("Папка для записей и скриншотов", "", box))
        cl.addWidget(self.rec_dir_lbl)
        self._show_rec_dir()

        hint = QLabel("Записи — в формате mp4. Звук компьютера пишется тем, что играет в колонках или наушниках по "
                      "умолчанию. Остров во время скриншота прячется, чтобы не попасть в кадр.")
        hint.setObjectName("desc")
        hint.setWordWrap(True)
        cl.addWidget(hint)
        lay.addWidget(c)
        lay.addStretch()
        return sc

    def _show_rec_dir(self):
        shown = C.media_dir(self.cfg, "video") if self.cfg.get("rec_dir") else \
            f"{C.media_dir(self.cfg, 'video')}   (скриншоты — {C.media_dir(self.cfg, 'shot')})"
        self.rec_dir_lbl.setText(f"Сейчас: {shown}")

    def _set_rec_dir(self, path):
        self.set("rec_dir", path)
        self._show_rec_dir()

    def _pick_rec_dir(self):
        start = str(C.media_dir(self.cfg, "video"))
        d = QFileDialog.getExistingDirectory(self, "Папка для записей и скриншотов", start)
        if d:
            self._set_rec_dir(d)

    # ---- Модули
    def _modules(self):
        sc, lay = page("Модули")
        c, cl = card()
        for key, name, desc in [
            ("clock", "Часы и календарь", "Большие часы, дата, номер недели и календарь на месяц."),
            ("timer", "Таймер и секундомер", "Обратный отсчёт со звуком и секундомер с кругами."),
            ("reminders", "Напоминания", "Напоминания на нужное время — остров сам раскроется и подаст сигнал."),
            ("calc", "Калькулятор", "Обычный калькулятор, работает и с клавиатуры."),
            ("system", "Система", "Загрузка процессора и памяти, скорость сети и диска в реальном времени. Нужна библиотека psutil."),
            ("eye", "Большой глаз (вкладка)", "Отдельная вкладка с большим глазом. Персонажа на острове выбирают в «Внешнем виде»."),
            ("music", "Музыка и тексты", "Вкладка с текстом песни, который идёт в такт музыке. Нужна библиотека winsdk."),
            ("mixer", "Микшер громкости", "Громкость каждого приложения отдельно, как в Windows. Нужны pycaw, comtypes и psutil — вкладка поставит их сама."),
            ("rec", "Запись экрана и ножницы", "Запись экрана со звуком, скриншот всего экрана и «Ножницы»: выдели область, порисуй и напиши текст. Для записи нужен ffmpeg (pip install imageio-ffmpeg), для звука — PyAudioWPatch."),
            ("clip", "История буфера", "Всё, что копировалось: текст, ссылки, файлы, картинки. Поиск, закрепление, возврат в буфер одним кликом."),
            ("shelf", "Полка", "Временное хранилище файлов: перетащи на остров, потом вытащи куда нужно."),
        ]:
            t = Toggle(self.cfg["modules"].get(key, True))
            t.toggled.connect(lambda v, k=key: self.set_module(k, v))
            cl.addWidget(row(name, desc, t))
        lay.addWidget(c)
        lay.addStretch()
        return sc

    # ---- О программе
    def _about(self):
        sc, lay = page("О программе")
        c, cl = card()
        l = QLabel("Dynamic Island by ASTRO\n\nОстров и эти настройки — два отдельных приложения. "
                   "Настройки хранятся в файле:\n" + str(C.CFG_FILE) +
                   "\n\nЗакрыть остров можно через значок в трее (правой кнопкой → «Выход»).")
        l.setWordWrap(True)
        l.setObjectName("desc")
        cl.addWidget(l)
        lay.addWidget(c)
        lay.addStretch()
        return sc


def main():
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ASTRO.DynamicIsland.Settings")
        except Exception:
            pass
    app = QApplication(sys.argv)
    from PySide6.QtGui import QIcon
    ico = Path(__file__).with_name("icon.png")
    if ico.exists():
        app.setWindowIcon(QIcon(str(ico)))
    app.setStyleSheet(QSS)
    C.CFG_DIR.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(C.CFG_DIR / "settings.lock"))
    if not lock.tryLock(200):
        return 0  # окно настроек уже открыто
    w = SettingsWindow()
    w.show()
    code = app.exec()
    lock.unlock()
    return code


if __name__ == "__main__":
    sys.exit(main())
