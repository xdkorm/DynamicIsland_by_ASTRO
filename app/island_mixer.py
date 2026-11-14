"""Микшер громкости: общая громкость и отдельный ползунок для каждого приложения, как в Windows,
только прямо на острове. Рядом с ползунком живой индикатор: видно, какое приложение шумит прямо сейчас.
Нужно:  pip install pycaw comtypes psutil   (вкладка умеет поставить их сама)
"""
import importlib
import os
import subprocess
import sys
import threading

from PySide6.QtCore import QFileInfo, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import (QFileIconProvider, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea,
                               QVBoxLayout, QWidget)

from island_extras import mix, py_exe

NOWIN = 0x08000000 if os.name == "nt" else 0
PKGS = ["pycaw", "comtypes", "psutil"]
MUTE_OFF = ("QPushButton{background:#1c1c1e;border:none;border-radius:14px;font-size:14px}"
            "QPushButton:hover{background:#2c2c2e}")
MUTE_ON = ("QPushButton{background:#3a1d1f;border:none;border-radius:14px;font-size:14px}"
           "QPushButton:hover{background:#4a2326}")
ACCENT = "#0a84ff"

# понятные названия для привычных программ
PRETTY = {"chrome": "Google Chrome", "msedge": "Microsoft Edge", "firefox": "Firefox", "opera": "Opera",
          "operagx": "Opera GX", "brave": "Brave", "browser": "Яндекс Браузер", "spotify": "Spotify",
          "yandexmusic": "Яндекс Музыка", "discord": "Discord", "telegram": "Telegram", "vlc": "VLC",
          "steam": "Steam", "steamwebhelper": "Steam", "obs64": "OBS Studio", "zoom": "Zoom", "teams": "Teams",
          "ms-teams": "Teams", "skype": "Skype", "whatsapp": "WhatsApp", "itunes": "iTunes", "potplayermini64": "PotPlayer",
          "wmplayer": "Проигрыватель Windows", "systemsounds": "Системные звуки"}


# ───────────────────────── Работа со звуком Windows ─────────────────────────
def _pycaw():
    """Импортируем лениво, чтобы после установки пакетов всё заработало без перезапуска острова."""
    au = importlib.import_module("pycaw.pycaw")
    cc = importlib.import_module("comtypes")
    return au, cc


def pretty_name(proc_name):
    base = os.path.splitext(proc_name or "")[0]
    key = base.lower()
    if key in PRETTY:
        return PRETTY[key]
    base = base.replace("_", " ").replace("-", " ").strip()
    return (base[:1].upper() + base[1:]) if base else "Приложение"


class Mixer:
    """Тонкая обёртка над pycaw: общая громкость + сессии приложений, сгруппированные по программам."""

    def __init__(self):
        self.err = ""
        self.master = None
        self.groups = {}           # ключ → {"name", "exe", "sess": [(session, meter)], "active"}
        self._master_try = 0.0

    # ---- общая громкость
    def connect_master(self):
        try:
            au, cc = _pycaw()
            dev = au.AudioUtilities.GetSpeakers()
            ep = getattr(dev, "EndpointVolume", None)         # новый pycaw
            if ep is None:                                    # старый pycaw
                raw = getattr(dev, "_dev", dev)
                iface = raw.Activate(au.IAudioEndpointVolume._iid_, cc.CLSCTX_ALL, None)
                import ctypes
                ep = ctypes.cast(iface, ctypes.POINTER(au.IAudioEndpointVolume))
            ep.GetMasterVolumeScalar()
            self.master = ep
        except Exception as e:
            self.master, self.err = None, f"{type(e).__name__}: {e}"

    def master_get(self):
        try:
            return float(self.master.GetMasterVolumeScalar()), bool(self.master.GetMute())
        except Exception:
            self.master = None
            return 0.0, False

    def master_set(self, v):
        try:
            self.master.SetMasterVolumeScalar(max(0.0, min(1.0, v)), None)
            if v > 0 and self.master.GetMute():
                self.master.SetMute(0, None)
        except Exception:
            pass

    def master_mute(self, on):
        try:
            self.master.SetMute(1 if on else 0, None)
        except Exception:
            pass

    # ---- приложения
    def refresh(self):
        """Перечитывает сессии. Возвращает список ключей в порядке появления."""
        au, _ = _pycaw()
        try:
            from pycaw.pycaw import IAudioMeterInformation
        except Exception:
            IAudioMeterInformation = None
        groups = {}
        for s in au.AudioUtilities.GetAllSessions():
            try:
                if s.State == 2:                              # сессия закрыта
                    continue
                pid = s.ProcessId
                proc = None
                try:
                    proc = s.Process
                except Exception:
                    pass
                if proc is not None:
                    pname, exe = proc.name(), ""
                    try:
                        exe = proc.exe()
                    except Exception:
                        pass
                elif pid == 0:
                    pname, exe = "SystemSounds", ""
                else:
                    continue
                key = os.path.splitext(pname)[0].lower()
                meter = None
                if IAudioMeterInformation is not None:
                    try:
                        meter = s._ctl.QueryInterface(IAudioMeterInformation)
                    except Exception:
                        meter = None
                g = groups.setdefault(key, {"name": pretty_name(pname), "exe": exe, "sess": [], "active": False})
                g["sess"].append((s, meter))
                g["active"] = g["active"] or s.State == 1
            except Exception:
                continue
        self.groups = groups
        return list(groups)

    def read(self, key):
        """(громкость, без звука, активна ли)."""
        g = self.groups.get(key)
        vol, muted = 0.0, False
        if g:
            for s, _ in g["sess"]:
                try:
                    vol = max(vol, float(s.SimpleAudioVolume.GetMasterVolume()))
                    muted = muted or bool(s.SimpleAudioVolume.GetMute())
                except Exception:
                    pass
        return vol, muted, bool(g and g["active"])

    def peak(self, key):
        g, p = self.groups.get(key), 0.0
        if g:
            for _, meter in g["sess"]:
                try:
                    p = max(p, float(meter.GetPeakValue()))
                except Exception:
                    pass
        return p

    def set_volume(self, key, v):
        g = self.groups.get(key)
        for s, _ in (g["sess"] if g else []):
            try:
                s.SimpleAudioVolume.SetMasterVolume(max(0.0, min(1.0, v)), None)
                if v > 0 and s.SimpleAudioVolume.GetMute():
                    s.SimpleAudioVolume.SetMute(0, None)
            except Exception:
                pass

    def set_mute(self, key, on):
        g = self.groups.get(key)
        for s, _ in (g["sess"] if g else []):
            try:
                s.SimpleAudioVolume.SetMute(1 if on else 0, None)
            except Exception:
                pass


# ───────────────────────── Ползунок ─────────────────────────
class VolSlider(QWidget):
    """Ползунок громкости: тянется мышью, колёсиком меняет по 3%, подсвечивается в такт звуку приложения."""
    changed = Signal(float)

    def __init__(self, accent=ACCENT):
        super().__init__()
        self.value, self.level, self.muted = 0.0, 0.0, False
        self.accent = QColor(accent)
        self._drag = self._hover = False
        self.setFixedHeight(30)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_value(self, v):
        if not self._drag and abs(v - self.value) > 0.001:
            self.value = max(0.0, min(1.0, v))
            self.update()

    def set_level(self, lv):
        lv = max(0.0, min(1.0, lv * 1.6))
        new = max(lv, self.level * 0.82)               # быстро вверх, плавно вниз
        if abs(new - self.level) > 0.015:
            self.level = new
            self.update()

    def set_muted(self, m):
        if m != self.muted:
            self.muted = m
            self.update()

    def _val_at(self, x):
        return max(0.0, min(1.0, (x - 9) / max(1, self.width() - 18)))

    def _set(self, x):
        self.value = self._val_at(x)
        self.changed.emit(self.value)
        self.update()

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            self._drag = True
            self._set(ev.position().x())

    def mouseMoveEvent(self, ev):
        if self._drag:
            self._set(ev.position().x())

    def mouseReleaseEvent(self, _):
        self._drag = False
        self.update()

    def wheelEvent(self, ev):
        self.value = max(0.0, min(1.0, self.value + ev.angleDelta().y() / 120 * 0.03))
        self.changed.emit(self.value)
        self.update()
        ev.accept()

    def enterEvent(self, _):
        self._hover = True
        self.update()

    def leaveEvent(self, _):
        self._hover = False
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, cy = self.width(), self.height() / 2
        big = self._hover or self._drag
        th = 8 if big else 6
        track = QRectF(9, cy - th / 2, w - 18, th)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 34))
        p.drawRoundedRect(track, th / 2, th / 2)
        x = 9 + (w - 18) * self.value
        if self.muted:
            col = QColor("#636366")
        else:
            col = mix(self.accent, QColor("#ffffff"), 0.10 + 0.5 * self.level)   # громче играет — ярче заливка
        p.setBrush(col)
        p.drawRoundedRect(QRectF(track.left(), track.top(), max(th, x - track.left()), th), th / 2, th / 2)
        if big or self.level > 0.05:
            r = 8 if big else 6
            p.setBrush(QColor(0, 0, 0, 70))
            p.drawEllipse(QPointF(x, cy + 1), r, r)
            p.setBrush(QColor("#ffffff"))
            p.drawEllipse(QPointF(x, cy), r - 1, r - 1)


# ───────────────────────── Строка приложения ─────────────────────────
class MixRow(QWidget):
    volume_changed = Signal(float)
    mute_toggled = Signal(bool)

    def __init__(self, name, exe="", master=False):
        super().__init__()
        self.master, self.muted, self._pm = master, False, None
        self.setFixedHeight(42)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 2, 4, 2)
        lay.setSpacing(10)

        self.icon = QLabel()
        self.icon.setFixedSize(26, 26)
        self.icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon.setStyleSheet("font-size:18px;background:transparent")
        if master:
            self.icon.setText("🔊")
        elif name == "Системные звуки":
            self.icon.setText("🔔")
        else:
            self._load_icon(exe)
        lay.addWidget(self.icon)

        self.name = QLabel()
        self.name.setFixedWidth(150)
        f = QFont("Segoe UI")
        f.setPixelSize(14)
        f.setBold(True)
        self.name.setFont(f)
        self._name_full = name
        self.name.setText(QFontMetrics(f).elidedText(name, Qt.TextElideMode.ElideRight, 150))
        self.name.setToolTip(name)
        self.name.setStyleSheet("color:#ffffff;background:transparent")
        lay.addWidget(self.name)

        self.slider = VolSlider("#ffffff" if master else ACCENT)
        self.slider.changed.connect(self._on_slide)
        lay.addWidget(self.slider, 1)

        self.pct = QLabel("0%")
        self.pct.setFixedWidth(42)
        self.pct.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.pct.setStyleSheet("color:#8e8e93;font-size:13px;font-weight:600;background:transparent")
        lay.addWidget(self.pct)

        self.mute = QPushButton("🔊")
        self.mute.setFixedSize(34, 28)
        self.mute.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mute.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.mute.setStyleSheet(MUTE_OFF)
        self.mute.setToolTip("Выключить звук")
        self.mute.clicked.connect(lambda: self.mute_toggled.emit(not self.muted))
        lay.addWidget(self.mute)

    def _load_icon(self, exe):
        self.icon.setText("🎵")
        if exe and os.path.exists(exe):
            try:
                pm = QFileIconProvider().icon(QFileInfo(exe)).pixmap(24, 24)
                if not pm.isNull():
                    self.icon.setPixmap(pm)
            except Exception:
                pass

    def _on_slide(self, v):
        self.pct.setText(f"{round(v * 100)}%")
        self.volume_changed.emit(v)

    def apply(self, vol, muted, level):
        self.slider.set_value(vol)
        self.slider.set_muted(muted)
        self.slider.set_level(0.0 if muted else level)
        text = "Выкл" if muted else f"{round(self.slider.value * 100)}%"
        if text != self.pct.text():
            self.pct.setText(text)
        if muted != self.muted:
            self.pct.setStyleSheet(f"color:{'#ff453a' if muted else '#8e8e93'};font-size:13px;font-weight:600;"
                                   "background:transparent")
            self.muted = muted
            self.mute.setText("🔇" if muted else "🔊")
            self.mute.setStyleSheet(MUTE_ON if muted else MUTE_OFF)
            self.mute.setToolTip("Включить звук" if muted else "Выключить звук")


# ───────────────────────── Страница ─────────────────────────
class MixerPage(QWidget):
    _install_done = Signal(bool, str)

    def __init__(self):
        super().__init__()
        self.mixer = Mixer()
        self.rows, self._vals = {}, {}
        self._installing = False
        self._install_err = ""
        self._install_done.connect(self._on_install_done)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)

        self.master_row = MixRow("Общая громкость", master=True)
        self.master_row.volume_changed.connect(self.mixer.master_set)
        self.master_row.mute_toggled.connect(self.mixer.master_mute)
        lay.addWidget(self.master_row)

        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet("background:#2c2c2e")
        lay.addWidget(line)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet(
            "QScrollArea,QScrollArea>QWidget>QWidget{background:transparent;border:none}"
            "QScrollBar:vertical{width:6px;background:transparent}"
            "QScrollBar::handle:vertical{background:#3a3a3c;border-radius:3px}"
            "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0}")
        self.inner = QWidget()
        self.vbox = QVBoxLayout(self.inner)
        self.vbox.setContentsMargins(0, 2, 6, 2)
        self.vbox.setSpacing(2)
        self.vbox.addStretch()
        self.scroll.setWidget(self.inner)
        lay.addWidget(self.scroll, 1)

        self.note = QLabel(self.scroll)                      # подсказка поверх списка
        self.note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.note.setWordWrap(True)
        self.note.setStyleSheet("color:#8e8e93;font-size:13px;background:transparent")
        self.note.hide()

        self.install_btn = QPushButton("Установить автоматически", self.scroll)
        self.install_btn.setFixedSize(230, 36)
        self.install_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.install_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.install_btn.setStyleSheet("QPushButton{background:#0a84ff;color:#fff;border:none;border-radius:12px;"
                                       "font-size:14px;font-weight:600}QPushButton:hover{background:#3d9bff}"
                                       "QPushButton:disabled{background:#2c2c2e;color:#8e8e93}")
        self.install_btn.clicked.connect(self.install)
        self.install_btn.hide()

        self.t_list = QTimer(self, interval=1200, timeout=self.refresh)    # список приложений
        self.t_live = QTimer(self, interval=60, timeout=self._live)        # индикаторы звука

    # ---- жизненный цикл: пока вкладка не видна, звук не опрашиваем
    def showEvent(self, _):
        self.refresh()
        self.t_list.start()
        self.t_live.start()

    def hideEvent(self, _):
        self.t_list.stop()
        self.t_live.stop()

    def resizeEvent(self, _):
        self._place_note()

    def _place_note(self):
        w, h = self.scroll.width(), self.scroll.height()
        self.note.setGeometry(24, 8, w - 48, h - (56 if self.install_btn.isVisible() else 16))
        self.install_btn.move((w - self.install_btn.width()) // 2, h - 52)

    def _show_note(self, text, button=False):
        self.note.setText(text)
        self.note.show()
        self.install_btn.setVisible(button)
        self._place_note()
        self.note.raise_()
        self.install_btn.raise_()

    # ---- данные
    def refresh(self):
        try:
            _pycaw()
        except Exception as e:
            self.master_row.setEnabled(False)
            msg = ("Для микшера нужны библиотеки pycaw, comtypes и psutil.\nМожно поставить их одной кнопкой."
                   if not self._installing else "Устанавливаю библиотеки… это займёт около минуты.")
            if self._install_err:
                msg = f"Не получилось установить: {self._install_err}\n" + f"Вручную: {py_exe()} -m pip install {' '.join(PKGS)}"
            self.install_btn.setEnabled(not self._installing)
            self._show_note(msg, button=not self._installing)
            return
        if self.mixer.master is None:
            self.mixer.connect_master()
        self.master_row.setEnabled(self.mixer.master is not None)
        try:
            keys = self.mixer.refresh()
        except Exception as e:
            self._show_note(f"Не получилось прочитать звук Windows:\n{type(e).__name__}: {e}")
            return
        for k in [k for k in self.rows if k not in keys]:      # приложение закрылось
            row = self.rows.pop(k)
            self._vals.pop(k, None)
            self.vbox.removeWidget(row)
            row.deleteLater()
        for k in keys:                                          # новое приложение
            if k not in self.rows:
                g = self.mixer.groups[k]
                row = MixRow(g["name"], g["exe"])
                row.volume_changed.connect(lambda v, k=k: self._vol(k, v))
                row.mute_toggled.connect(lambda on, k=k: self._mute(k, on))
                self.vbox.insertWidget(self.vbox.count() - 1, row)
                self.rows[k] = row
        if self.rows:
            self.note.hide()
            self.install_btn.hide()
        else:
            self._show_note("Сейчас ничего не шумит.\nВключи музыку или видео — приложение появится здесь.")
        self._live(sync=True)

    def _vol(self, k, v):
        self.mixer.set_volume(k, v)
        self._vals[k] = (v, False)                              # снятый «без звука» тоже запоминаем сразу

    def _mute(self, k, on):
        self.mixer.set_mute(k, on)
        self._vals[k] = (self._vals.get(k, (0.0, False))[0], on)

    def _live(self, sync=False):
        if self.mixer.master is not None:
            vol, muted = self.mixer.master_get()
            self.master_row.apply(vol, muted, 0.0)
        for k, row in self.rows.items():
            if sync:
                self._vals[k] = self.mixer.read(k)[:2]
            vol, muted = self._vals.get(k, (0.0, False))
            row.apply(vol, muted, self.mixer.peak(k))

    # ---- установка библиотек прямо из острова
    def install(self):
        if self._installing:
            return
        self._installing, self._install_err = True, ""
        self.refresh()
        threading.Thread(target=self._install, daemon=True).start()

    def _install(self):
        try:
            r = subprocess.run([py_exe(), "-m", "pip", "install", "--disable-pip-version-check", *PKGS],
                               capture_output=True, text=True, timeout=420, creationflags=NOWIN)
            if r.returncode == 0:
                self._install_done.emit(True, "")
                return
            lines = (r.stdout + "\n" + r.stderr).strip().splitlines()
            self._install_done.emit(False, lines[-1] if lines else f"код {r.returncode}")
        except Exception as e:
            self._install_done.emit(False, f"{type(e).__name__}: {e}")

    def _on_install_done(self, ok, msg):
        self._installing = False
        self._install_err = "" if ok else msg
        importlib.invalidate_caches()
        self.refresh()
