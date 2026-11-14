"""Дополнительные модули острова: таймер и секундомер, напоминания, калькулятор, глазок."""
import ast
import datetime as dt
import json
import math
import random
import re
import threading
import os
import time
from collections import deque

from PySide6.QtCore import QPointF, QRectF, Qt, QTime, QTimer, Signal
from PySide6.QtGui import (QColor, QCursor, QFont, QFontMetrics, QLinearGradient, QPainter, QPainterPath, QPen,
                           QRadialGradient)
from PySide6.QtWidgets import (QAbstractSpinBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QScrollArea, QStackedWidget, QTimeEdit, QVBoxLayout, QWidget)

import common as C

INPUT_QSS = (
    "QLabel{color:#ffffff;}"
    "QLineEdit,QTimeEdit{background:#1c1c1e;border:1px solid #2c2c2e;border-radius:10px;padding:6px 10px;"
    "color:#fff;font-size:14px;selection-background-color:#3a6df0}"
    "QLineEdit:focus,QTimeEdit:focus{border-color:#5b5bd6}"
)


def btn_style(bg="#2c2c2e", fg="#ffffff", hover="#3a3a3c", r=14, fs=15):
    return (f"QPushButton{{background:{bg};color:{fg};border:none;border-radius:{r}px;font-size:{fs}px;"
            f"font-weight:600}}QPushButton:hover{{background:{hover}}}QPushButton:pressed{{background:{hover}}}")


def mkbtn(text, style, cb=None, w=None, h=None):
    b = QPushButton(text)
    b.setStyleSheet(style)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    if w:
        b.setFixedWidth(w)
    if h:
        b.setFixedHeight(h)
    if cb:
        b.clicked.connect(lambda _=False: cb())
    return b


GREEN = btn_style("#30d158", "#000000", "#4be072", 14, 15)
ORANGE = btn_style("#ff9f0a", "#000000", "#ffb340", 14, 15)
GRAY = btn_style("#3a3a3c", "#ffffff", "#4a4a4c", 14, 15)
DARK = btn_style("#1c1c1e", "#ffffff", "#2c2c2e", 12, 13)
SEG = ("QPushButton{background:transparent;color:#8e8e93;border:none;font-size:14px;font-weight:600;"
       "padding:5px 14px;border-radius:10px}QPushButton:checked{background:#1c1c1e;color:#fff}"
       "QPushButton:hover{color:#fff}")


# ───────────────────────── Сигнал «как будильник на iPhone» ─────────────────────────
class Alarm:
    _stop = threading.Event()
    _running = False

    @classmethod
    def start(cls):
        if cls._running:
            return
        cls._stop.clear()
        cls._running = True

        def run():
            try:
                try:
                    import winsound
                    beep = winsound.Beep
                except ImportError:
                    beep = None
                for _ in range(40):
                    for f in (1760, 1760, 1760, 1760):
                        if cls._stop.is_set():
                            return
                        if beep:
                            beep(f, 70)
                        else:
                            time.sleep(0.07)
                        time.sleep(0.05)
                    if cls._stop.wait(0.75):
                        return
            finally:
                cls._running = False

        threading.Thread(target=run, daemon=True).start()

    @classmethod
    def stop(cls):
        cls._stop.set()


# ───────────────────────── Таймер и секундомер ─────────────────────────
def fmt_hms(sec):
    sec = max(0, int(math.ceil(sec - 1e-9)))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def fmt_sw(sec):
    cs = int(sec * 100)
    h, m, s, c = cs // 360000, (cs // 6000) % 60, (cs // 100) % 60, cs % 100
    return f"{h}:{m:02d}:{s:02d}.{c:02d}" if h else f"{m:02d}:{s:02d}.{c:02d}"


class TimerPage(QWidget):
    finished = Signal()

    def __init__(self):
        super().__init__()
        self.total = 300.0
        self.remaining = 300.0
        self.running = False
        self._end = 0.0
        self.sw_acc = 0.0
        self.sw_t0 = None
        self.laps = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)
        seg = QHBoxLayout()
        self.b_t = QPushButton("Таймер")
        self.b_s = QPushButton("Секундомер")
        for i, b in enumerate((self.b_t, self.b_s)):
            b.setCheckable(True)
            b.setStyleSheet(SEG)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.clicked.connect(lambda _=False, k=i: self.mode(k))
            seg.addWidget(b)
        seg.addStretch()
        root.addLayout(seg)
        self.stack = QStackedWidget()
        self.stack.addWidget(self._timer_panel())
        self.stack.addWidget(self._sw_panel())
        root.addWidget(self.stack, 1)
        self.mode(0)

        self.ticker = QTimer(self, interval=50, timeout=self._tick)
        self.ticker.start()
        self._refresh()

    def mode(self, i):
        self.stack.setCurrentIndex(i)
        self.b_t.setChecked(i == 0)
        self.b_s.setChecked(i == 1)

    # ---- таймер
    def _timer_panel(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        lay.addStretch()
        self.t_lbl = QLabel()
        self.t_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.t_lbl.setStyleSheet("font-size:60px;font-weight:700;color:#fff;")
        lay.addWidget(self.t_lbl)
        adds = QHBoxLayout()
        adds.addStretch()
        for text, sec in (("+10 с", 10), ("+1 мин", 60), ("+5 мин", 300), ("+15 мин", 900)):
            adds.addWidget(mkbtn(text, DARK, lambda s=sec: self.add(s), 84, 32))
        adds.addStretch()
        lay.addLayout(adds)
        ctl = QHBoxLayout()
        ctl.addStretch()
        self.t_start = mkbtn("Старт", GREEN, self.toggle, 140, 40)
        ctl.addWidget(self.t_start)
        ctl.addWidget(mkbtn("Сброс", GRAY, self.reset, 100, 40))
        ctl.addStretch()
        lay.addLayout(ctl)
        lay.addStretch()
        return w

    def add(self, sec):
        self.total = min(99 * 3600, self.total + sec)
        if self.running:
            self._end += sec
        else:
            self.remaining = self.total
        self._refresh()

    def toggle(self):
        if self.running:
            self.remaining = max(0.0, self._end - time.monotonic())
            self.running = False
        elif self.remaining > 0:
            self._end = time.monotonic() + self.remaining
            self.running = True
        self._refresh()

    def reset(self):
        self.running = False
        self.total = self.remaining = 0.0
        self._refresh()

    # ---- секундомер
    def _sw_panel(self):
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        left = QVBoxLayout()
        left.setSpacing(12)
        left.addStretch()
        self.s_lbl = QLabel()
        self.s_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.s_lbl.setStyleSheet("font-size:52px;font-weight:700;color:#fff;")
        left.addWidget(self.s_lbl)
        ctl = QHBoxLayout()
        ctl.addStretch()
        self.s_start = mkbtn("Старт", GREEN, self.sw_toggle, 110, 40)
        ctl.addWidget(self.s_start)
        ctl.addWidget(mkbtn("Круг", GRAY, self.sw_lap, 90, 40))
        ctl.addWidget(mkbtn("Сброс", GRAY, self.sw_reset, 90, 40))
        ctl.addStretch()
        left.addLayout(ctl)
        left.addStretch()
        lay.addLayout(left, 1)
        self.laps_lbl = QLabel()
        self.laps_lbl.setFixedWidth(190)
        self.laps_lbl.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.laps_lbl.setStyleSheet("font-family:Consolas,monospace;font-size:13px;color:#8e8e93;")
        lay.addWidget(self.laps_lbl)
        return w

    def sw_elapsed(self):
        return self.sw_acc + (time.monotonic() - self.sw_t0 if self.sw_t0 is not None else 0.0)

    def sw_toggle(self):
        if self.sw_t0 is None:
            self.sw_t0 = time.monotonic()
        else:
            self.sw_acc = self.sw_elapsed()
            self.sw_t0 = None
        self._refresh()

    def sw_lap(self):
        el = self.sw_elapsed()
        if el <= 0:
            return
        prev = self.laps[-1][1] if self.laps else 0.0
        self.laps.append((el - prev, el))
        self._refresh()

    def sw_reset(self):
        self.sw_acc, self.sw_t0, self.laps = 0.0, None, []
        self._refresh()

    # ---- общее
    def _tick(self):
        if self.running:
            self.remaining = self._end - time.monotonic()
            if self.remaining <= 0:
                self.running = False
                self.remaining = self.total
                self._refresh()
                self.finished.emit()
                return
        if self.isVisible():
            self._refresh()

    def _refresh(self):
        self.t_lbl.setText(fmt_hms(self.remaining if not self.running else self._end - time.monotonic()))
        self.t_start.setText("Пауза" if self.running else "Старт")
        self.t_start.setStyleSheet(ORANGE if self.running else GREEN)
        self.s_lbl.setText(fmt_sw(self.sw_elapsed()))
        self.s_start.setText("Стоп" if self.sw_t0 is not None else "Старт")
        self.s_start.setStyleSheet(ORANGE if self.sw_t0 is not None else GREEN)
        last = self.laps[-6:]
        start_n = len(self.laps) - len(last) + 1
        self.laps_lbl.setText("\n".join(f"{start_n + i:>2}  {fmt_sw(d)}  {fmt_sw(t)}"
                                        for i, (d, t) in enumerate(last)))


# ───────────────────────── Напоминания ─────────────────────────
class RemindersPage(QWidget):
    fired = Signal(str)

    def __init__(self):
        super().__init__()
        self.items = []
        try:
            data = json.loads(C.REMINDERS_FILE.read_text("utf-8"))
            self.items = [x for x in data if isinstance(x, dict) and "ts" in x and "text" in x]
        except Exception:
            pass

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        top = QHBoxLayout()
        top.setSpacing(6)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("О чём напомнить?")
        self.edit.returnPressed.connect(self.add)
        self.time = QTimeEdit()
        self.time.setDisplayFormat("HH:mm")
        self.time.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.time.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.time.setFixedWidth(72)
        self.time.setToolTip("Время: можно вводить с клавиатуры или крутить колесиком")
        self._preset(10)
        top.addWidget(self.edit, 1)
        top.addWidget(self.time)
        for text, m in (("+5 м", 5), ("+15 м", 15), ("+1 ч", 60)):
            top.addWidget(mkbtn(text, DARK, lambda mm=m: self._preset(mm), 52, 34))
        top.addWidget(mkbtn("Добавить", ORANGE, self.add, 92, 34))
        lay.addLayout(top)

        self.empty = QLabel("Напоминаний нет\nНапиши, о чём напомнить, выбери время и нажми «Добавить»")
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
        self.col = QVBoxLayout(self.inner)
        self.col.setContentsMargins(0, 0, 0, 0)
        self.col.setSpacing(6)
        self.col.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll.setWidget(self.inner)
        lay.addWidget(self.scroll, 1)
        self.rebuild()

        self.checker = QTimer(self, interval=1000, timeout=self._check)
        self.checker.start()

    def _preset(self, minutes):
        t = dt.datetime.now() + dt.timedelta(minutes=minutes)
        self.time.setTime(QTime(t.hour, t.minute))

    def add(self):
        now = dt.datetime.now()
        qt = self.time.time()
        when = now.replace(hour=qt.hour(), minute=qt.minute(), second=0, microsecond=0)
        if when <= now:
            when += dt.timedelta(days=1)
        text = self.edit.text().strip() or "Напоминание"
        self.items.append({"text": text, "ts": when.timestamp()})
        self.items.sort(key=lambda x: x["ts"])
        self.edit.clear()
        self._preset(10)
        self.rebuild()

    def remove(self, item):
        if item in self.items:
            self.items.remove(item)
        self.rebuild()

    def _check(self):
        now = time.time()
        due = [x for x in self.items if x["ts"] <= now]
        if not due:
            return
        for x in due:
            self.items.remove(x)
        self.rebuild()
        self.fired.emit("; ".join(x["text"] for x in due))

    @staticmethod
    def _when(ts):
        d = dt.datetime.fromtimestamp(ts)
        today = dt.date.today()
        if d.date() == today:
            day = "сегодня"
        elif d.date() == today + dt.timedelta(days=1):
            day = "завтра"
        else:
            day = d.strftime("%d.%m")
        return f"{day}, {d:%H:%M}"

    def rebuild(self):
        while self.col.count():
            it = self.col.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        for x in self.items:
            fr = QFrame()
            fr.setObjectName("rem")
            fr.setStyleSheet("#rem{background:#1c1c1e;border-radius:12px}QLabel{background:transparent}")
            h = QHBoxLayout(fr)
            h.setContentsMargins(14, 6, 8, 6)
            when = QLabel(self._when(x["ts"]))
            when.setFixedWidth(120)
            when.setStyleSheet("color:#ff9f0a;font-weight:600;font-size:14px;")
            txt = QLabel(x["text"])
            txt.setStyleSheet("color:#fff;font-size:14px;")
            h.addWidget(when)
            h.addWidget(txt, 1)
            h.addWidget(mkbtn("✕", btn_style("#3a3a3c", "#fff", "#ff453a", 11, 11), lambda it=x: self.remove(it), 22, 22))
            self.col.addWidget(fr)
        self.empty.setVisible(not self.items)
        self.scroll.setVisible(bool(self.items))
        try:
            C.CFG_DIR.mkdir(parents=True, exist_ok=True)
            C.REMINDERS_FILE.write_text(json.dumps(self.items, ensure_ascii=False), "utf-8")
        except Exception:
            pass


# ───────────────────────── Калькулятор ─────────────────────────
def safe_eval(expr: str):
    s = expr.replace("÷", "/").replace("×", "*").replace("−", "-").replace(",", ".")
    s = re.sub(r"(\d+(?:\.\d+)?)%", r"(\1/100)", s)

    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(n.value, bool):
            return n.value
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.USub, ast.UAdd)):
            v = ev(n.operand)
            return -v if isinstance(n.op, ast.USub) else v
        if isinstance(n, ast.BinOp):
            a, b = ev(n.left), ev(n.right)
            if isinstance(n.op, ast.Add):
                return a + b
            if isinstance(n.op, ast.Sub):
                return a - b
            if isinstance(n.op, ast.Mult):
                return a * b
            if isinstance(n.op, ast.Div):
                return a / b
        raise ValueError("bad expression")

    return ev(ast.parse(s, mode="eval"))


def fmt_num(v):
    if isinstance(v, float):
        if v.is_integer() and abs(v) < 1e15:
            v = int(v)
        else:
            return f"{v:.10g}".replace(".", ",")
    return str(v)


class CalcPage(QWidget):
    OPS = "÷×−+"

    def __init__(self):
        super().__init__()
        self.expr = ""
        self.result = "0"
        self.fresh = False
        self.history = []
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(20)
        left = QVBoxLayout()
        left.setSpacing(2)
        left.addStretch()
        self.hist_lbl = QLabel()
        self.hist_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.hist_lbl.setStyleSheet("color:#636366;font-size:13px;")
        self.expr_lbl = QLabel()
        self.expr_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.expr_lbl.setStyleSheet("color:#8e8e93;font-size:20px;")
        self.res_lbl = QLabel("0")
        self.res_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.res_lbl.setStyleSheet("color:#fff;font-size:46px;font-weight:700;")
        left.addWidget(self.hist_lbl)
        left.addWidget(self.expr_lbl)
        left.addWidget(self.res_lbl)
        lay.addLayout(left, 1)

        grid = QGridLayout()
        grid.setSpacing(5)
        layout = [
            [("C", 1), ("⌫", 1), ("%", 1), ("÷", 1)],
            [("7", 1), ("8", 1), ("9", 1), ("×", 1)],
            [("4", 1), ("5", 1), ("6", 1), ("−", 1)],
            [("1", 1), ("2", 1), ("3", 1), ("+", 1)],
            [("0", 2), (",", 1), ("=", 1)],
        ]
        for r, rowk in enumerate(layout):
            c = 0
            for key, span in rowk:
                if key in self.OPS or key == "=":
                    st = btn_style("#ff9f0a", "#000", "#ffb340", 12, 17)
                elif key in ("C", "⌫", "%"):
                    st = btn_style("#3a3a3c", "#fff", "#4a4a4c", 12, 15)
                else:
                    st = btn_style("#1f1f22", "#fff", "#2c2c2e", 12, 16)
                b = mkbtn(key, st, lambda k=key: self.press(k), None, 31)
                b.setMinimumWidth(56 * span + 5 * (span - 1))
                grid.addWidget(b, r, c, 1, span)
                c += span
        gw = QWidget()
        gw.setLayout(grid)
        lay.addWidget(gw, 0, Qt.AlignmentFlag.AlignVCenter)
        self._render()

    def _render(self):
        self.expr_lbl.setText(self.expr.replace(".", ","))
        self.res_lbl.setText(self.result)
        self.hist_lbl.setText("\n".join(self.history[-2:]))
        self.res_lbl.setStyleSheet(
            f"color:#fff;font-weight:700;font-size:{46 if len(self.result) < 11 else 30}px;")

    def _preview(self):
        if not self.expr or self.expr[-1] in self.OPS:
            return
        try:
            self.result = fmt_num(safe_eval(self.expr))
        except Exception:
            pass

    def press(self, k):
        if k == "C":
            self.expr, self.result, self.fresh = "", "0", False
        elif k == "⌫":
            self.expr = self.expr[:-1]
            self.result = "0" if not self.expr else self.result
            self._preview()
        elif k in self.OPS:
            if not self.expr:
                if k == "−":
                    self.expr = "−"
                elif self.result not in ("0", "Ошибка"):
                    self.expr = self.result.replace(",", ".") + k
            elif self.expr[-1] in self.OPS:
                self.expr = self.expr[:-1] + k
            else:
                self.expr += k
            self.fresh = False
        elif k == "%":
            if self.expr and (self.expr[-1].isdigit()):
                self.expr += "%"
                self._preview()
        elif k == "=":
            if self.expr:
                try:
                    res = fmt_num(safe_eval(self.expr))
                    self.history.append(f"{self.expr.replace('.', ',')} = {res}")
                    self.expr, self.result, self.fresh = res.replace(",", "."), res, True
                except ZeroDivisionError:
                    self.result, self.fresh = "Ошибка", True
                    self.expr = ""
                except Exception:
                    self.result = "Ошибка"
        else:  # цифры и запятая
            if self.fresh:
                self.expr, self.fresh = "", False
            if k == ",":
                m = re.search(r"[\d.]+$", self.expr)
                if m and "." in m.group(0):
                    return
                self.expr += "." if m else "0."
            else:
                self.expr += k
            self._preview()
        self._render()

    def mousePressEvent(self, e):
        self.setFocus()

    def showEvent(self, e):
        self.setFocus()

    def keyPressEvent(self, e):
        t, key = e.text(), e.key()
        if t and t in "0123456789":
            self.press(t)
        elif t in ("+",):
            self.press("+")
        elif t == "-":
            self.press("−")
        elif t == "*":
            self.press("×")
        elif t == "/":
            self.press("÷")
        elif t in (".", ","):
            self.press(",")
        elif t == "%":
            self.press("%")
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) or t == "=":
            self.press("=")
        elif key == Qt.Key.Key_Backspace:
            self.press("⌫")
        elif key in (Qt.Key.Key_Escape, Qt.Key.Key_Delete):
            self.press("C")
        else:
            super().keyPressEvent(e)


# ───────────────────────── Глазок ─────────────────────────
class EyePage(QWidget):
    def __init__(self):
        super().__init__()
        self.look = QPointF(0, 0)
        self.blink = 0.0
        self._dir = 0
        self._next = time.time() + random.uniform(2, 5)
        self.timer = QTimer(self, interval=33, timeout=self._tick)

    def showEvent(self, e):
        self.timer.start()

    def hideEvent(self, e):
        self.timer.stop()

    def mousePressEvent(self, e):
        if self._dir == 0:
            self._dir = 1

    def _tick(self):
        c = self.mapFromGlobal(QCursor.pos())
        cx, cy = self.width() / 2, self.height() / 2 - 8
        dx, dy = c.x() - cx, c.y() - cy
        k = min(1.0, math.hypot(dx, dy) / 240)
        ang = math.atan2(dy, dx)
        tx, ty = math.cos(ang) * 62 * k, math.sin(ang) * 30 * k
        self.look += (QPointF(tx, ty) - self.look) * 0.28
        if self._dir == 0 and time.time() >= self._next:
            self._dir = 1
        if self._dir == 1:
            self.blink += 0.28
            if self.blink >= 1:
                self.blink, self._dir = 1.0, -1
        elif self._dir == -1:
            self.blink -= 0.2
            if self.blink <= 0:
                self.blink, self._dir = 0.0, 0
                self._next = time.time() + random.uniform(2.5, 6)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx, cy = self.width() / 2, self.height() / 2 - 8
        ew, eh = 250.0, 140.0 * (1 - 0.93 * self.blink)
        eye = QRectF(cx - ew / 2, cy - eh / 2, ew, eh)
        path = QPainterPath()
        path.addEllipse(eye)

        p.save()
        p.setClipPath(path)
        g = QRadialGradient(QPointF(cx, cy - 20), 170)
        g.setColorAt(0, QColor("#ffffff"))
        g.setColorAt(1, QColor("#c7c7cc"))
        p.fillPath(path, g)
        ic = QPointF(cx + self.look.x(), cy + self.look.y())
        ig = QRadialGradient(ic, 40)
        ig.setColorAt(0, QColor("#8fd0ff"))
        ig.setColorAt(0.7, QColor("#3a78e0"))
        ig.setColorAt(1, QColor("#1b3f93"))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(ig)
        p.drawEllipse(ic, 40, 40)
        p.setBrush(QColor("#050505"))
        p.drawEllipse(ic, 18, 18)
        p.setBrush(QColor(255, 255, 255, 230))
        p.drawEllipse(ic + QPointF(-11, -12), 7, 7)
        p.drawEllipse(ic + QPointF(9, 10), 3.5, 3.5)
        p.restore()

        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor("#3a3a3c"), 3))
        p.drawPath(path)
        f = QFont("Segoe UI", 1)
        f.setPixelSize(12)
        p.setFont(f)
        p.setPen(QColor("#636366"))
        p.drawText(QRectF(0, self.height() - 22, self.width(), 20), Qt.AlignmentFlag.AlignCenter,
                   "Глазок следит за курсором · клик — моргнуть")


# ───────────────────────── Система ─────────────────────────
try:
    import psutil
except ImportError:  # без psutil вкладка покажет подсказку, остров продолжит работать
    psutil = None


def fmt_bytes(n, per_sec=False):
    units = ["Б", "КБ", "МБ", "ГБ", "ТБ"]
    v, i = float(max(0, n)), 0
    while v >= 1024 and i < len(units) - 1:
        v /= 1024
        i += 1
    s = f"{v:.0f}" if (i == 0 or v >= 100) else f"{v:.1f}"
    return s.replace(".", ",") + " " + units[i] + ("/с" if per_sec else "")


def load_color(base, pct):
    if pct >= 90:
        return QColor("#ff453a")
    if pct >= 75:
        return QColor("#ff9f0a")
    return QColor(base)


class MetricCard(QWidget):
    """Карточка: заголовок, крупное значение, до двух строк пояснений, полоска и мини-график."""
    HIST = 60

    def __init__(self, title, color, value_px=28):
        super().__init__()
        self.title, self.value_px = title, value_px
        self.accent = QColor(color)
        self.bar_color = QColor(color)
        self.value, self.lines = "…", []
        self.frac = None
        self.h1 = deque([0.0] * self.HIST, maxlen=self.HIST)
        self.h2 = None
        self.scale = 100.0
        self.setMinimumHeight(88)

    def use_second_line(self):
        self.h2 = deque([0.0] * self.HIST, maxlen=self.HIST)

    def push(self, value, lines, s1, s2=None, scale=100.0, frac=None, bar_color=None):
        self.value, self.lines, self.frac, self.scale = value, lines, frac, max(1e-9, scale)
        self.bar_color = QColor(bar_color) if bar_color else self.accent
        self.h1.append(s1)
        if self.h2 is not None:
            self.h2.append(s2 or 0.0)
        self.update()

    def clear(self):
        for h in (self.h1, self.h2):
            if h is not None:
                h.extend([0.0] * self.HIST)

    @staticmethod
    def _path(rect, data, scale):
        n = len(data)
        pts = [QPointF(rect.left() + rect.width() * i / (n - 1),
                       rect.bottom() - rect.height() * min(1.0, v / scale)) for i, v in enumerate(data)]
        path = QPainterPath(pts[0])
        for pt in pts[1:]:
            path.lineTo(pt)
        return path

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#1c1c1e"))
        p.drawRoundedRect(QRectF(0, 0, w, h), 16, 16)

        left, text_w = 16, w * 0.56 - 24
        f = QFont("Segoe UI", 1)
        f.setPixelSize(11)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor("#8e8e93"))
        p.drawText(QRectF(left, 8, text_w, 16), Qt.AlignmentFlag.AlignVCenter, self.title)

        f.setPixelSize(self.value_px)
        p.setFont(f)
        p.setPen(QColor("#ffffff"))
        p.drawText(QRectF(left, 22, text_w, 34), Qt.AlignmentFlag.AlignVCenter, self.value)

        f.setBold(False)
        f.setPixelSize(11)
        p.setFont(f)
        fm = QFontMetrics(f)
        p.setPen(QColor("#a1a1a6"))
        for i, line in enumerate(self.lines[:2]):
            txt = fm.elidedText(line, Qt.TextElideMode.ElideRight, int(text_w))
            p.drawText(QRectF(left, 56 + i * 15, text_w, 15), Qt.AlignmentFlag.AlignVCenter, txt)

        if self.frac is not None:
            bar = QRectF(left, h - 13, text_w, 5)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#3a3a3c"))
            p.drawRoundedRect(bar, 2.5, 2.5)
            p.setBrush(self.bar_color)
            p.drawRoundedRect(QRectF(bar.left(), bar.top(), max(5.0, bar.width() * min(1.0, self.frac)), 5), 2.5, 2.5)

        g = QRectF(w * 0.56, 14, w * 0.44 - 14, h - 28)
        p.setPen(QPen(QColor("#2c2c2e"), 1))
        p.drawLine(QPointF(g.left(), g.bottom()), QPointF(g.right(), g.bottom()))
        line = self._path(g, self.h1, self.scale)
        area = QPainterPath(line)
        area.lineTo(g.right(), g.bottom())
        area.lineTo(g.left(), g.bottom())
        area.closeSubpath()
        c1, c0 = QColor(self.accent), QColor(self.accent)
        c1.setAlpha(110)
        c0.setAlpha(0)
        grad = QLinearGradient(0, g.top(), 0, g.bottom())
        grad.setColorAt(0, c1)
        grad.setColorAt(1, c0)
        p.fillPath(area, grad)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(self.accent, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.drawPath(line)
        if self.h2 is not None:
            p.setPen(QPen(QColor(255, 255, 255, 150), 1.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                          Qt.PenJoinStyle.RoundJoin))
            p.drawPath(self._path(g, self.h2, self.scale))


class SystemPage(QWidget):
    """ЦП, ОЗУ, сеть и диск. Опрос раз в секунду — только пока вкладка видна."""

    def __init__(self):
        super().__init__()
        lay = QGridLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self._ok = psutil is not None
        if not self._ok:
            hint = QLabel("Для этой вкладки нужна библиотека psutil.\nУстанови её командой:  pip install psutil")
            hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
            hint.setStyleSheet("border:1.5px dashed #3a3a3c;border-radius:14px;color:#8e8e93;font-size:14px;")
            lay.addWidget(hint, 0, 0)
            return
        self.cpu = MetricCard("ПРОЦЕССОР", "#0a84ff")
        self.ram = MetricCard("ПАМЯТЬ", "#bf5af2")
        self.net = MetricCard("СЕТЬ", "#30d158", 22)
        self.dsk = MetricCard("ДИСК", "#ff9f0a")
        self.net.use_second_line()
        self.dsk.use_second_line()
        lay.addWidget(self.cpu, 0, 0)
        lay.addWidget(self.ram, 0, 1)
        lay.addWidget(self.net, 1, 0)
        lay.addWidget(self.dsk, 1, 1)
        self._path = (os.environ.get("SystemDrive", "C:") + "\\") if os.name == "nt" else "/"
        self._drive = self._path.rstrip("\\/") or "/"
        self._t0 = time.monotonic()
        self._net0 = self._dio0 = None
        self.timer = QTimer(self, interval=1000, timeout=self._tick)

    @staticmethod
    def _net():
        try:
            n = psutil.net_io_counters()
            return (n.bytes_recv, n.bytes_sent) if n else None
        except Exception:
            return None

    @staticmethod
    def _dio():
        try:
            d = psutil.disk_io_counters()
            return (d.read_bytes, d.write_bytes) if d else None
        except Exception:
            return None

    def showEvent(self, e):
        if not self._ok:
            return
        for c in (self.cpu, self.ram, self.net, self.dsk):
            c.clear()
        self._t0 = time.monotonic()
        self._net0, self._dio0 = self._net(), self._dio()
        psutil.cpu_percent(None)  # первый вызов только «запоминает» точку отсчёта
        self.timer.start()
        QTimer.singleShot(250, self._tick)

    def hideEvent(self, e):
        if self._ok:
            self.timer.stop()

    @staticmethod
    def _rate(new, old, dt_):
        return [max(0.0, (a - b) / dt_) for a, b in zip(new, old)] if new and old else [0.0, 0.0]

    def _tick(self):
        now = time.monotonic()
        dt_ = max(0.1, now - self._t0)
        self._t0 = now

        # процессор
        cpu = psutil.cpu_percent(None)
        l2 = f"Потоков: {psutil.cpu_count() or '?'}"
        try:
            fq = psutil.cpu_freq()
            if fq and fq.current:
                l2 += f" · {fq.current / 1000:.1f} ГГц".replace(".", ",")
        except Exception:
            pass
        self.cpu.push(f"{cpu:.0f}%", ["Загрузка", l2], cpu, scale=100, frac=cpu / 100,
                      bar_color=load_color("#0a84ff", cpu))

        # память
        vm = psutil.virtual_memory()
        self.ram.push(f"{vm.percent:.0f}%", [f"{fmt_bytes(vm.used)} из {fmt_bytes(vm.total)}",
                                              f"Свободно: {fmt_bytes(vm.available)}"],
                      vm.percent, scale=100, frac=vm.percent / 100, bar_color=load_color("#bf5af2", vm.percent))

        # сеть
        n1 = self._net()
        rx, tx = self._rate(n1, self._net0, dt_)
        self._net0 = n1 or self._net0
        tot = f"Всего: ↓ {fmt_bytes(n1[0])}  ↑ {fmt_bytes(n1[1])}" if n1 else "Нет данных"
        self.net.push(f"↓ {fmt_bytes(rx, True)}", [f"↑ {fmt_bytes(tx, True)}", tot], rx, tx,
                      scale=max(100 * 1024, max(self.net.h1), max(self.net.h2)))

        # диск
        d1 = self._dio()
        rd, wr = self._rate(d1, self._dio0, dt_)
        self._dio0 = d1 or self._dio0
        try:
            du = psutil.disk_usage(self._path)
            val, frac = f"{du.percent:.0f}%", du.percent / 100
            l1 = f"{self._drive} {fmt_bytes(du.used)} из {fmt_bytes(du.total)}"
            col = load_color("#ff9f0a", du.percent) if du.percent >= 90 else "#ff9f0a"
        except Exception:
            val, frac, l1, col = "—", None, "Нет данных", "#ff9f0a"
        self.dsk.push(val, [l1, f"Чт {fmt_bytes(rd, True)} · Зп {fmt_bytes(wr, True)}"], rd, wr,
                      scale=max(1024 * 1024, max(self.dsk.h1), max(self.dsk.h2)), frac=frac, bar_color=col)
