"""Live Lyrics — тексты песен в реальном времени на острове: пилюля-караоке и вкладка «Музыка».
Трек берётся из системного плеера Windows (GSMTC), текст — из открытого сервиса LRCLIB.
Зависимость: winsdk (Python до 3.12) или набор winrt-* (Python 3.13+). Вкладка умеет поставить их сама.
"""
import asyncio
import bisect
import hashlib
import importlib
import json
import math
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from PySide6.QtCore import QObject, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (QBrush, QColor, QFont, QFontMetrics, QGuiApplication, QImage, QLinearGradient,
                           QPainter, QPainterPath, QPen, QPixmap, QRadialGradient)
from PySide6.QtWidgets import QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget

import common as C
from island_modules import btn_style, mkbtn

UA = "DynamicIslandRU/1.0 (desktop widget)"
AL = Qt.AlignmentFlag
NOWIN = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW


# ════════════════════════════════════════════════════════════════════
#   1. LIVE LYRICS
# ════════════════════════════════════════════════════════════════════
_TS = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\]")
_JUNK = re.compile(r"\s*[\(\[][^)\]]*(official|video|audio|lyrics?|клип|текст|hd|4k|remaster|visuali[sz]er|mv)"
                   r"[^)\]]*[\)\]]", re.I)
_FEAT = re.compile(r"\s*[\(\[]?\s*(feat\.?|ft\.?)\s.*$", re.I)


def parse_lrc(text: str) -> list:
    """LRC → [(секунда, строка)], отсортировано по времени."""
    out = []
    for raw in text.splitlines():
        stamps = _TS.findall(raw)
        if not stamps:
            continue
        line = _TS.sub("", raw)
        line = re.sub(r"<\d+:\d+(?:\.\d+)?>", "", line).strip()  # теги слов из enhanced-LRC
        for mm, ss in stamps:
            out.append((int(mm) * 60 + float(ss), line or "♪"))
    out.sort(key=lambda x: x[0])
    return out


def clean_track(title: str, artist: str):
    """Убираем из названия «(Official Video)», «feat. …», «- Topic» — чтобы текст находился."""
    title = _JUNK.sub("", title or "").strip()
    artist = re.sub(r"\s*-\s*Topic$", "", artist or "", flags=re.I).strip()
    if " - " in title:
        a, t = [s.strip() for s in title.split(" - ", 1)]
        if not artist or artist.lower() in a.lower() or a.lower() in artist.lower():
            artist, title = a, t
    title = _FEAT.sub("", title).strip()
    return title, artist


def _http_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=7) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch_lyrics(title, artist, album, duration):
    """Ищем текст в LRCLIB. Возвращает {"state": ok|plain|instr|none|net, "lines": [(сек, строка)], "plain": str}.
    ok — есть синхронный текст, plain — только обычный (без таймингов), instr — инструментал."""
    t, a = clean_track(title, artist)
    base = "https://lrclib.net/api/"
    q = {"track_name": t, "artist_name": a}
    if album:
        q["album_name"] = album
    if duration > 0:
        q["duration"] = int(round(duration))
    urls = [base + "get?" + urllib.parse.urlencode(q),
            base + "search?" + urllib.parse.urlencode({"track_name": t, "artist_name": a}),
            base + "search?" + urllib.parse.urlencode({"q": f"{a} {t}".strip()}),
            base + "search?" + urllib.parse.urlencode({"track_name": t})]
    net_errors, plain, instr = 0, "", False
    for url in urls:
        try:
            data = _http_json(url)
        except urllib.error.HTTPError:  # 404 — просто нет такого трека
            continue
        except Exception:
            net_errors += 1
            continue
        items = [it for it in (data if isinstance(data, list) else [data]) if isinstance(it, dict)]
        if duration > 0 and items:  # отсекаем другие версии трека (другая длина)
            items.sort(key=lambda it: abs((it.get("duration") or duration) - duration))
            items = [it for it in items if abs((it.get("duration") or duration) - duration) <= 8]
        for it in items:
            if it.get("syncedLyrics"):
                lines = parse_lrc(it["syncedLyrics"])
                if lines:
                    return {"state": "ok", "lines": lines, "plain": ""}
        for it in items:
            if not plain and it.get("plainLyrics"):
                plain = it["plainLyrics"]
            instr = instr or bool(it.get("instrumental"))
    if plain:
        return {"state": "plain", "lines": [], "plain": plain}
    if instr:
        return {"state": "instr", "lines": [], "plain": ""}
    return {"state": "net" if net_errors == len(urls) else "none", "lines": [], "plain": ""}


def _secs(x):
    try:
        return float(x.total_seconds())
    except Exception:
        try:
            return float(x)
        except Exception:
            return 0.0


# ---- WinRT: winsdk (старый, Python ≤ 3.12) или winrt-* (новый, Python 3.13+)
WINRT_PKGS = ["winrt-runtime", "winrt-Windows.Foundation", "winrt-Windows.Foundation.Collections",
              "winrt-Windows.Media.Control", "winrt-Windows.Storage.Streams"]


def winrt_mod(name):
    """Модуль WinRT, например winrt_mod('media.control'). Пробует winsdk, затем winrt."""
    errs = []
    for root in ("winsdk", "winrt"):
        try:
            return importlib.import_module(f"{root}.windows.{name}")
        except ImportError as e:
            errs.append(f"{root}: {e}")
    raise ImportError(" | ".join(errs))


def dep_sets():
    """Что пробуем ставить по очереди. winsdk не собирается под Python 3.13+, там нужен winrt-*."""
    return [WINRT_PKGS] if sys.version_info >= (3, 13) else [["winsdk"], WINRT_PKGS]


def py_exe():
    exe = sys.executable or "python"
    if os.path.basename(exe).lower().startswith("astroisland"):   # наш exe без консоли → берём python.exe рядом
        cand = os.path.join(os.path.dirname(exe), "python.exe")
        if os.path.exists(cand):
            return cand
    if exe.lower().endswith("pythonw.exe"):
        cand = exe[:-len("pythonw.exe")] + "python.exe"
        if os.path.exists(cand):
            return cand
    return exe


def fix_command():
    return f'"{py_exe()}" -m pip install ' + " ".join(dep_sets()[0])


def pretty_app(aumid):
    """Понятное имя плеера по AppUserModelID."""
    s = (aumid or "").lower()
    for key, name in (("yandexmusic", "Яндекс Музыка"), ("spotify", "Spotify"), ("chrome", "Chrome"),
                      ("msedge", "Edge"), ("firefox", "Firefox"), ("yandex", "Яндекс Браузер"), ("opera", "Opera"),
                      ("brave", "Brave"), ("vlc", "VLC"), ("zune", "Медиаплеер"), ("media.player", "Медиаплеер"),
                      ("telegram", "Telegram"), ("discord", "Discord"), ("itunes", "iTunes"),
                      ("applemusic", "Apple Music"), ("foobar", "foobar2000"), ("aimp", "AIMP")):
        if key in s:
            return name
    tail = re.split(r"[!\\/]", aumid or "")[-1]
    return re.sub(r"\.exe$", "", tail, flags=re.I)[:24]


class MediaWatcher(QObject):
    """Опрашивает системный медиаплеер Windows (GSMTC) в фоновом потоке."""
    updated = Signal(dict)

    def __init__(self):
        super().__init__()
        self.enabled = True
        self._cmds = []
        self._started = False
        self._ck, self._have_cover, self._tries, self._next_try = "", False, 0, 0.0

    def start(self):
        if self._started:
            return
        self._started = True
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        try:
            asyncio.run(self._loop())
        except Exception as e:
            self.updated.emit({"error": "winrt", "detail": f"{type(e).__name__}: {e}"})
        finally:
            self._started = False  # позволит перезапустить (кнопка «Повторить»)

    def command(self, name):
        self._cmds.append(name)

    async def _loop(self):
        try:
            mc = winrt_mod("media.control")
            mgr = await mc.GlobalSystemMediaTransportControlsSessionManager.request_async()
        except ImportError as e:
            self.updated.emit({"error": "missing", "detail": str(e)})
            return
        except Exception as e:
            self.updated.emit({"error": "winrt", "detail": f"{type(e).__name__}: {e}"})
            return
        fails = 0
        while True:
            try:
                sess = self._pick(mgr)
                while self._cmds:
                    await self._exec(sess, self._cmds.pop(0))
                if self.enabled:
                    self.updated.emit(await self._snapshot(sess))
                fails = 0
            except Exception as e:
                fails += 1
                if fails == 3:  # одиночные сбои плеера не показываем — только если это надолго
                    print("Live Lyrics: ошибка чтения плеера:", e)
                    self.updated.emit({"error": "poll", "detail": f"{type(e).__name__}: {e}"})
            await asyncio.sleep(0.5)

    @staticmethod
    def _is_playing(s):
        try:
            return int(s.get_playback_info().playback_status) == 4  # PLAYING
        except Exception:
            return False

    def _pick(self, mgr):
        """Текущая сессия Windows — не всегда та, где играет музыка (на паузе может висеть другой плеер).
        Поэтому предпочитаем сессию, которая прямо сейчас играет."""
        cur = mgr.get_current_session()
        if cur is not None and self._is_playing(cur):
            return cur
        try:
            for s in list(mgr.get_sessions()):
                if self._is_playing(s):
                    return s
        except Exception:
            pass
        return cur

    @staticmethod
    async def _exec(sess, cmd):
        if sess is None:
            return
        if isinstance(cmd, tuple) and cmd[0] == "seek":  # перемотка (поддерживают не все плееры)
            await sess.try_change_playback_position_async(int(cmd[1] * 10_000_000))
        elif cmd == "toggle":
            await sess.try_toggle_play_pause_async()
        elif cmd == "next":
            await sess.try_skip_next_async()
        elif cmd == "prev":
            await sess.try_skip_previous_async()

    @staticmethod
    async def _cover(props):
        """Обложка трека (байты картинки) или None."""
        try:
            ref = props.thumbnail
            if ref is None:
                return None
            streams = winrt_mod("storage.streams")
            stream = await ref.open_read_async()
            size = int(stream.size)
            if not size or size > 8_000_000:
                return None
            try:
                buf = streams.Buffer(size)
                await stream.read_async(buf, buf.capacity, streams.InputStreamOptions.READ_AHEAD)
                return bytes(memoryview(buf))[:int(buf.length)]
            except Exception:
                reader = streams.DataReader(stream)
                await reader.load_async(size)
                raw = bytearray(size)
                reader.read_bytes(raw)
                return bytes(raw)
        except Exception:
            return None

    async def _snapshot(self, sess):
        if sess is None:
            return {"active": False}
        props = await sess.try_get_media_properties_async()
        playing = self._is_playing(sess)
        tl = sess.get_timeline_properties()
        start, end, pos = _secs(tl.start_time), _secs(tl.end_time), _secs(tl.position)
        if playing:  # позиция в timeline обновляется рывками — достраиваем до «сейчас»
            try:
                lu = tl.last_updated_time
                if lu.tzinfo is None:
                    lu = lu.replace(tzinfo=timezone.utc)
                gap = (datetime.now(timezone.utc) - lu).total_seconds()
                if 0 <= gap < 36000:
                    pos += gap
            except Exception:
                pass
        try:
            app = pretty_app(sess.source_app_user_model_id)
        except Exception:
            app = ""
        ck = f"{props.artist}|{props.title}"
        out = {"active": bool(props.title), "title": props.title or "", "artist": props.artist or "",
               "album": props.album_title or "", "playing": playing, "cover_key": ck, "app": app,
               "duration": max(0.0, end - start), "pos": max(0.0, pos - start)}
        # обложка приходит у плееров с задержкой — пробуем несколько раз, не дёргая каждые полсекунды
        if ck != self._ck:
            self._ck, self._have_cover, self._tries, self._next_try = ck, False, 0, 0.0
        if out["active"] and not self._have_cover and self._tries < 6 and time.monotonic() >= self._next_try:
            self._tries += 1
            self._next_try = time.monotonic() + 1.5
            data = await self._cover(props)
            if data:
                self._have_cover = True
                out["cover"] = data
        return out


# ════════════════════════════════════════════════════════════════════
#   Общие помощники для рисования
# ════════════════════════════════════════════════════════════════════
DEFAULT_ACCENT = "#8b7cf6"


def mix(a: QColor, b: QColor, k: float) -> QColor:
    return QColor(round(a.red() + (b.red() - a.red()) * k), round(a.green() + (b.green() - a.green()) * k),
                  round(a.blue() + (b.blue() - a.blue()) * k))


def accent_from(img: QImage) -> QColor:
    """Яркий акцентный цвет по обложке (среднее с упором на насыщенные пиксели)."""
    try:
        small = img.scaled(24, 24, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        r = g = b = ws = 0.0
        for y in range(24):
            for x in range(24):
                c = QColor(small.pixel(x, y))
                wgt = c.hsvSaturationF() ** 2 * c.valueF() + 0.02
                r, g, b, ws = r + c.redF() * wgt, g + c.greenF() * wgt, b + c.blueF() * wgt, ws + wgt
        h, s, v, _ = QColor.fromRgbF(r / ws, g / ws, b / ws).getHsvF()
        if h < 0 or s < 0.12:  # серая обложка — берём фирменный цвет
            return QColor(DEFAULT_ACCENT)
        return QColor.fromHsvF(h, min(1.0, max(s, 0.55)), max(v, 0.85))
    except Exception:
        return QColor(DEFAULT_ACCENT)


def fit_font(txt, px, min_px, avail, bold=True):
    """Шрифт, в который строка влезает (ужимаем до min_px), и текст с «…», если всё равно длинно."""
    f = QFont("Segoe UI")
    f.setBold(bold)
    size = px
    f.setPixelSize(size)
    fm = QFontMetrics(f)
    while fm.horizontalAdvance(txt) > avail and size > min_px:
        size -= 1
        f.setPixelSize(size)
        fm = QFontMetrics(f)
    return f, fm.elidedText(txt, Qt.TextElideMode.ElideRight, max(1, int(avail)))


def karaoke_brush(left, width, prog, lit: QColor, dim: QColor) -> QBrush:
    """Кисть для текста: слева «спето» (lit), справа ещё нет (dim), граница мягкая."""
    g = QLinearGradient(left, 0, left + max(1.0, width), 0)
    if prog >= 0.999:
        g.setColorAt(0, lit)
        g.setColorAt(1, lit)
    elif prog <= 0.001:
        g.setColorAt(0, dim)
        g.setColorAt(1, dim)
    else:
        g.setColorAt(0, lit)
        g.setColorAt(prog, lit)
        g.setColorAt(min(1.0, prog + 0.12), dim)
        g.setColorAt(1, dim)
    return QBrush(g)


def draw_cover(p, pm, rect: QRectF, radius, accent: QColor):
    """Обложка со скруглением; если её нет — градиентная заглушка с нотой."""
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    p.setClipPath(path)
    if pm is not None and not pm.isNull():
        s = min(pm.width(), pm.height())
        p.drawPixmap(rect, pm, QRectF((pm.width() - s) / 2, (pm.height() - s) / 2, s, s))
    else:
        g = QLinearGradient(rect.topLeft(), rect.bottomRight())
        g.setColorAt(0, accent)
        g.setColorAt(1, mix(accent, QColor("#000000"), 0.65))
        p.fillPath(path, g)
        f = QFont("Segoe UI")
        f.setPixelSize(max(10, int(rect.height() * 0.5)))
        p.setFont(f)
        p.setPen(QColor(255, 255, 255, 200))
        p.drawText(rect, AL.AlignCenter, "♪")
    p.restore()


def draw_eq(p, cx, cy, color: QColor, playing: bool, hmax=14.0):
    """Маленький эквалайзер из четырёх столбиков; при паузе замирает."""
    n, bw, gap = 4, 3.0, 2.0
    t = time.time()
    x = cx - (n * bw + (n - 1) * gap) / 2
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(color)
    for i in range(n):
        hh = 4 + (hmax - 4) * (0.5 + 0.5 * math.sin(t * (5.5 + i * 1.7) + i * 1.9)) if playing else 3.0
        p.drawRoundedRect(QRectF(x + i * (bw + gap), cy - hh / 2, bw, hh), bw / 2, bw / 2)
    p.restore()


def fmt_time(s):
    s = max(0, int(s))
    return f"{s // 60}:{s % 60:02d}"


# ════════════════════════════════════════════════════════════════════
#   Движок: что играет, какой текст, какая строка сейчас
# ════════════════════════════════════════════════════════════════════
class LyricsEngine(QObject):
    changed = Signal()
    _fetched = Signal(str, object)
    _install_done = Signal(bool, str)

    def __init__(self):
        super().__init__()
        self.watcher = MediaWatcher()
        self.watcher.updated.connect(self._on_snap)
        self._fetched.connect(self._on_fetched)
        self._install_done.connect(self._on_install_done)
        self.track = None
        self.key = ""
        self.lines, self.times = [], []
        self.plain = ""            # текст без таймингов (если синхронного нет)
        self.state = "idle"        # idle | loading | ok | plain | instr | none | net | error
        self.err_kind = ""         # missing | winrt | poll (state == error)
        self.detail = ""           # подробности ошибки
        self.install_state = ""    # "" | running | failed
        self.playing = False
        self.offset = 0.0          # ручной сдвиг синхронизации, сек
        self.cover = None          # QPixmap обложки
        self.accent = QColor(DEFAULT_ACCENT)
        self.track_t0 = 0.0        # когда началась текущая песня (для «карточки» с названием)
        self._covers = {}          # artist|title → (pixmap, accent)
        self._base_pos = 0.0
        self._base_t = time.monotonic()
        self._last_rep = None
        self._last_play = 0.0

    # ---- управление
    def set_enabled(self, on):
        self.watcher.enabled = on
        if on:
            self.watcher.start()

    def command(self, name):
        self.watcher.command(name)

    def seek(self, sec):
        """Перемотка: плееру уходит команда, а свои часы сдвигаем сразу — без «подвисания» текста."""
        sec = max(0.0, sec)
        self.watcher.command(("seek", sec))
        self._base_pos, self._base_t, self._last_rep = sec, time.monotonic(), None
        self.changed.emit()

    def retry_watcher(self):
        """Перезапустить чтение плеера (после установки библиотеки или сбоя)."""
        importlib.invalidate_caches()
        self.state, self.err_kind, self.detail = "idle", "", ""
        self.watcher.start()
        self.changed.emit()

    # ---- установка библиотеки прямо из острова
    def install_deps(self):
        if self.install_state == "running":
            return
        self.install_state = "running"
        self.changed.emit()
        threading.Thread(target=self._install, daemon=True).start()

    def _install(self):
        exe, tail = py_exe(), ""
        for pkgs in dep_sets():
            try:
                r = subprocess.run([exe, "-m", "pip", "install", "--disable-pip-version-check", *pkgs],
                                   capture_output=True, text=True, timeout=420, creationflags=NOWIN)
                if r.returncode == 0:
                    self._install_done.emit(True, "")
                    return
                lines = (r.stdout + "\n" + r.stderr).strip().splitlines()
                tail = lines[-1] if lines else f"pip завершился с кодом {r.returncode}"
            except Exception as e:
                tail = f"{type(e).__name__}: {e}"
        self._install_done.emit(False, tail)

    def _on_install_done(self, ok, msg):
        if ok:
            self.install_state = ""
            self.retry_watcher()
        else:
            self.install_state, self.detail = "failed", msg
            self.changed.emit()

    # ---- данные из плеера
    def _on_snap(self, d):
        if d.get("error"):
            self.state, self.err_kind, self.detail = "error", d["error"], d.get("detail", "")
            self.playing = False
            self.changed.emit()
            return
        if self.state == "error" and not self.install_state:  # плеер снова отвечает
            self.state, self.err_kind, self.detail = "idle", "", ""
            self.changed.emit()
        if not d.get("active"):
            if self.track or self.playing:
                self.track, self.key, self.playing = None, "", False
                self.lines, self.times, self.plain = [], [], ""
                self.cover, self.state = None, "idle"
                self.changed.emit()
            return
        key = f"{d['artist']}|{d['title']}|{round(d['duration'])}"
        now = time.monotonic()
        if d.get("cover"):
            img = QImage.fromData(d["cover"])
            if not img.isNull():
                if len(self._covers) > 20:
                    self._covers.clear()
                self._covers[d["cover_key"]] = (QPixmap.fromImage(img), accent_from(img))
                self.changed.emit()
        if key != self.key:
            self.key, self.track = key, d
            self.lines, self.times, self.plain = [], [], ""
            self.state = "loading"
            self.track_t0 = now
            self._base_pos, self._base_t, self._last_rep = d["pos"], now, d["pos"]
            self._start_fetch(key, d)
            self.changed.emit()
        d.pop("cover", None)
        self.track = d
        cov = self._covers.get(d["cover_key"])
        new_cover = cov[0] if cov else None
        if new_cover is not self.cover:
            self.cover = new_cover
            self.accent = QColor(cov[1]) if cov else QColor(DEFAULT_ACCENT)
        if d["playing"] != self.playing:
            self.playing = d["playing"]
            self._base_pos = self.position()
            self._base_t = now
            self.changed.emit()
        if d["playing"]:
            self._last_play = now
        # плеер иногда отдаёт замершую позицию — тогда доверяем своим часам
        if self._last_rep is None or abs(d["pos"] - self._last_rep) > 0.01:
            self._base_pos, self._base_t = d["pos"], now
        self._last_rep = d["pos"]

    def position(self):
        if not self.playing:
            return self._base_pos
        return self._base_pos + (time.monotonic() - self._base_t)

    def progress(self):
        """(позиция, длительность) трека в секундах."""
        dur = float((self.track or {}).get("duration") or 0)
        pos = self.position()
        return (min(pos, dur) if dur > 0 else pos), dur

    # ---- поиск текста
    def _cache_path(self, key):
        return C.LYRICS_DIR / (hashlib.sha1(key.encode("utf-8")).hexdigest() + ".json")

    def _start_fetch(self, key, d):
        threading.Thread(target=self._fetch, daemon=True,
                         args=(key, d["title"], d["artist"], d["album"], d["duration"])).start()

    def _fetch(self, key, title, artist, album, duration):
        try:
            C.LYRICS_DIR.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        cache = self._cache_path(key)
        try:
            d = json.loads(cache.read_text("utf-8"))
            if d.get("state") in ("ok", "plain", "instr") or time.time() - d.get("ts", 0) < 6 * 3600:
                self._fetched.emit(key, d)
                return
        except Exception:
            pass
        res = fetch_lyrics(title, artist, album, duration)
        if res["state"] != "net":
            try:
                cache.write_text(json.dumps({**res, "ts": time.time()}, ensure_ascii=False), "utf-8")
            except Exception:
                pass
        self._fetched.emit(key, res)

    def _on_fetched(self, key, res):
        if key != self.key:
            return
        lines = [(float(t), str(s)) for t, s in res.get("lines", [])]
        self.lines, self.times = lines, [t for t, _ in lines]
        self.plain = res.get("plain", "") or ""
        st = res.get("state", "none")
        if lines:
            st = "ok"
        elif st == "ok":
            st = "none"
        elif st == "plain" and not self.plain:
            st = "none"
        self.state = st
        self.changed.emit()

    def refetch(self):
        """Забыть найденное и искать текст заново."""
        if not self.track or not self.key:
            return
        try:
            self._cache_path(self.key).unlink()
        except Exception:
            pass
        self.lines, self.times, self.plain = [], [], ""
        self.state = "loading"
        self._start_fetch(self.key, self.track)
        self.changed.emit()

    # ---- запросы от интерфейса
    def live(self):
        """Есть синхронный текст и музыка играет (или остановилась только что)."""
        return bool(self.lines) and (self.playing or time.monotonic() - self._last_play < 4)

    def card_text(self):
        """Первые секунды песни остров показывает «Название — Исполнитель»."""
        if self.track and self.playing and time.monotonic() - self.track_t0 < 3.2:
            t = self.track
            return f"{t['title']} — {t['artist']}" if t.get("artist") else t["title"]
        return None

    def line_state(self):
        """(индекс строки, её текст, прогресс 0..1, следующая строка). До первой строки индекс = -1."""
        if not self.lines:
            return -1, "", 0.0, ""
        pos = self.position() + self.offset
        idx = bisect.bisect_right(self.times, pos) - 1
        if idx < 0:
            return -1, "♪  " + (self.track or {}).get("title", ""), 0.0, self.lines[0][1]
        t0 = self.times[idx]
        t1 = self.times[idx + 1] if idx + 1 < len(self.times) else t0 + 5
        prog = max(0.0, min(1.0, (pos - t0) / max(0.3, min(t1 - t0, 8.0))))
        nxt = self.lines[idx + 1][1] if idx + 1 < len(self.lines) else ""
        return idx, self.lines[idx][1], prog, nxt


# ════════════════════════════════════════════════════════════════════
#   Виджеты вкладки «Музыка»
# ════════════════════════════════════════════════════════════════════
class ElidedText(QWidget):
    """Одна строка текста с «…» на конце, если не влезает."""

    def __init__(self, px=14, color="#ffffff", bold=False):
        super().__init__()
        self.px, self.color, self.bold, self.txt = px, QColor(color), bold, ""
        self.setFixedHeight(px + 8)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)

    def setText(self, t):
        if t != self.txt:
            self.txt = t
            self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        f = QFont("Segoe UI")
        f.setPixelSize(self.px)
        f.setBold(self.bold)
        p.setFont(f)
        p.setPen(self.color)
        p.drawText(QRectF(0, 0, self.width(), self.height()), AL.AlignVCenter | AL.AlignLeft,
                   QFontMetrics(f).elidedText(self.txt, Qt.TextElideMode.ElideRight, self.width()))


class SeekBar(QWidget):
    """Полоса прогресса: клик или перетаскивание — перемотка. Слева текущее время, справа длительность."""

    def __init__(self, engine):
        super().__init__()
        self.e = engine
        self._hover, self._drag = False, None
        self.setFixedHeight(30)
        self.setMouseTracking(True)
        self.timer = QTimer(self, interval=100, timeout=self.update)

    def showEvent(self, _):
        self.timer.start()

    def hideEvent(self, _):
        self.timer.stop()

    def enterEvent(self, _):
        self._hover = True

    def leaveEvent(self, _):
        self._hover = False

    def _frac(self, x):
        return max(0.0, min(1.0, x / max(1, self.width())))

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton and self.e.progress()[1] > 0:
            self._drag = self._frac(ev.position().x())
            self.update()

    def mouseMoveEvent(self, ev):
        if self._drag is not None:
            self._drag = self._frac(ev.position().x())
            self.update()

    def mouseReleaseEvent(self, ev):
        if self._drag is not None:
            self.e.seek(self._drag * self.e.progress()[1])
            self._drag = None

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pos, dur = self.e.progress()
        frac = self._drag if self._drag is not None else (pos / dur if dur > 0 else 0.0)
        big = self._hover or self._drag is not None
        bh = 6 if big else 4
        w = self.width()
        track = QRectF(0, 3 + (6 - bh) / 2, w, bh)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 38))
        p.drawRoundedRect(track, bh / 2, bh / 2)
        p.setBrush(self.e.accent)
        p.drawRoundedRect(QRectF(0, track.top(), max(bh, w * frac), bh), bh / 2, bh / 2)
        if big:
            p.setBrush(QColor("#ffffff"))
            p.drawEllipse(QPointF(max(5, min(w - 5, w * frac)), track.center().y()), 5, 5)
        f = QFont("Segoe UI")
        f.setPixelSize(11)
        p.setFont(f)
        p.setPen(QColor("#8e8e93"))
        shown = frac * dur if dur > 0 else pos
        p.drawText(QRectF(0, 14, w / 2, 14), AL.AlignLeft | AL.AlignVCenter, fmt_time(shown))
        p.drawText(QRectF(w / 2, 14, w / 2, 14), AL.AlignRight | AL.AlignVCenter, fmt_time(dur) if dur > 0 else "")


class CoverWidget(QWidget):
    """Обложка со свечением цвета обложки; на паузе чуть тускнеет."""

    def __init__(self, engine, size=64):
        super().__init__()
        self.e, self.s = engine, size
        self.setFixedSize(size + 14, size + 14)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        e = self.e
        c = QColor(e.accent)
        c.setAlpha(90 if e.playing else 40)
        g = QRadialGradient(QPointF(self.width() / 2, self.height() / 2), self.width() * 0.62)
        g.setColorAt(0.55, c)
        c0 = QColor(c)
        c0.setAlpha(0)
        g.setColorAt(1, c0)
        p.fillRect(self.rect(), QBrush(g))
        r = QRectF(7, 7, self.s, self.s)
        draw_cover(p, e.cover, r, 14, e.accent)
        if not e.playing and e.track:
            path = QPainterPath()
            path.addRoundedRect(r, 14, 14)
            p.fillPath(path, QColor(0, 0, 0, 90))
        p.setPen(QPen(QColor(255, 255, 255, 28), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(r, 14, 14)


class MarqueeText(QWidget):
    """Строка текста: если не влезает — медленно «бежит» туда-обратно, а не обрезается многоточием."""

    def __init__(self, px=14, color="#ffffff", bold=False):
        super().__init__()
        self.px, self.color, self.bold, self.txt = px, QColor(color), bold, ""
        self._t0 = time.time()
        self.setFixedHeight(px + 8)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.timer = QTimer(self, interval=33, timeout=self.update)

    def setText(self, t):
        if t != self.txt:
            self.txt, self._t0 = t, time.time()
            self.update()

    def showEvent(self, _):
        self.timer.start()

    def hideEvent(self, _):
        self.timer.stop()

    def paintEvent(self, _):
        p = QPainter(self)
        f = QFont("Segoe UI")
        f.setPixelSize(self.px)
        f.setBold(self.bold)
        p.setFont(f)
        p.setPen(self.color)
        tw, w = QFontMetrics(f).horizontalAdvance(self.txt), self.width()
        x = 0.0
        if tw > w:
            travel, speed = tw - w + 14, 32.0
            cycle = 1.6 + travel / speed + 1.2
            ph = (time.time() - self._t0) % cycle
            x = -min(travel, max(0.0, ph - 1.6) * speed)
        p.setClipRect(QRectF(0, 0, w, self.height()))
        p.drawText(QRectF(x, 0, max(tw, w) + 4, self.height()), AL.AlignVCenter | AL.AlignLeft, self.txt)


class TransportButton(QWidget):
    """Кнопки плеера, нарисованные вручную (эмодзи ⏮ ⏭ выглядят цветными квадратиками)."""
    clicked = Signal()

    def __init__(self, kind, size, engine):
        super().__init__()
        self.kind, self.e = kind, engine
        self._hover = self._down = False
        self.setFixedSize(size, size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def enterEvent(self, _):
        self._hover = True
        self.update()

    def leaveEvent(self, _):
        self._hover = self._down = False
        self.update()

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            self._down = True
            self.update()

    def mouseReleaseEvent(self, ev):
        if self._down and self.rect().contains(ev.position().toPoint()):
            self.clicked.emit()
        self._down = False
        self.update()

    def _poly(self, p, pts):
        path = QPainterPath()
        path.moveTo(*pts[0])
        for pt in pts[1:]:
            path.lineTo(*pt)
        path.closeSubpath()
        p.drawPath(path)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        k = 0.92 if self._down else 1.0
        s = self.width() * k
        r = QRectF((self.width() - s) / 2, (self.height() - s) / 2, s, s)
        cx, cy = r.center().x(), r.center().y()
        p.setPen(Qt.PenStyle.NoPen)
        if self.kind == "play":
            col = QColor(self.e.accent)
            p.setBrush(mix(col, QColor("#ffffff"), 0.22) if self._hover else col)
            p.drawEllipse(r)
            p.setBrush(QColor("#000000"))
            if self.e.playing:
                bw, bh, gap = s * 0.10, s * 0.34, s * 0.09
                p.drawRoundedRect(QRectF(cx - gap / 2 - bw, cy - bh / 2, bw, bh), 1.5, 1.5)
                p.drawRoundedRect(QRectF(cx + gap / 2, cy - bh / 2, bw, bh), 1.5, 1.5)
            else:
                self._poly(p, [(cx - s * 0.10, cy - s * 0.17), (cx - s * 0.10, cy + s * 0.17), (cx + s * 0.19, cy)])
            return
        if self._hover:
            p.setBrush(QColor(255, 255, 255, 30))
            p.drawEllipse(r)
        p.setBrush(QColor("#ffffff"))
        t = s * 0.15
        if self.kind == "prev":
            p.drawRoundedRect(QRectF(cx - s * 0.22, cy - t, s * 0.06, t * 2), 1, 1)
            self._poly(p, [(cx + s * 0.20, cy - t), (cx + s * 0.20, cy + t), (cx - s * 0.12, cy)])
        else:
            p.drawRoundedRect(QRectF(cx + s * 0.16, cy - t, s * 0.06, t * 2), 1, 1)
            self._poly(p, [(cx - s * 0.20, cy - t), (cx - s * 0.20, cy + t), (cx + s * 0.12, cy)])


class LyricsView(QWidget):
    """Текст песни: текущая строка крупная и заливается «караоке», соседние плавно тают, за текстом — мягкое
    свечение цвета обложки. Клик по строке — перемотка. Здесь же состояния: ошибка плеера, поиск, нет текста."""
    action = Signal(str)  # island | refetch | install | copy | retry

    def __init__(self, engine):
        super().__init__()
        self.e = engine
        self.disp = None
        self.acc = QColor(engine.accent)
        self.island_on = True
        self._hover, self._hits, self._btns, self._hbtn = -1, [], [], None
        self._copied = 0.0
        self._pkey, self._play, self._ph = None, [], 0
        self._scroll, self._man_y, self._man_t = 0.0, 0.0, 0.0
        self.setMouseTracking(True)
        self.timer = QTimer(self, interval=33, timeout=self._tick)

    def showEvent(self, _):
        self.disp = None
        self.timer.start()

    def hideEvent(self, _):
        self.timer.stop()

    def _tick(self):
        self.acc = mix(self.acc, self.e.accent, 0.08)
        if self.e.lines:
            idx = self.e.line_state()[0]
            self.disp = idx if self.disp is None else self.disp + (idx - self.disp) * 0.16
        self.update()

    # ---- мышь
    def _line_at(self, pos):
        for r, i in self._hits:
            if r.contains(pos):
                return i
        return -1

    def _btn_at(self, pos):
        for r, key in self._btns:
            if r.contains(pos):
                return key
        return None

    def mouseMoveEvent(self, ev):
        b = self._btn_at(ev.position())
        i = -1 if b else self._line_at(ev.position())
        if b != self._hbtn or i != self._hover:
            self._hbtn, self._hover = b, i
            self.setCursor(Qt.CursorShape.PointingHandCursor if (b or i >= 0) else Qt.CursorShape.ArrowCursor)

    def leaveEvent(self, _):
        self._hover, self._hbtn = -1, None

    def mousePressEvent(self, ev):
        if ev.button() != Qt.MouseButton.LeftButton:
            return
        b = self._btn_at(ev.position())
        if b:
            if b == "copy":
                self._copied = time.time()
            self.action.emit(b)
            return
        i = self._line_at(ev.position())
        if i >= 0:
            self.e.seek(self.e.times[i] - self.e.offset + 0.05)

    def wheelEvent(self, ev):
        if self.e.state == "plain" and self.e.plain:
            self._man_y = max(0.0, min(max(0.0, self._ph - self.height() + 50), self._scroll - ev.angleDelta().y() / 2))
            self._scroll, self._man_t = self._man_y, time.time()
            ev.accept()
        else:
            ev.ignore()

    # ---- рисование
    def _font(self, px, bold=False):
        f = QFont("Segoe UI")
        f.setPixelSize(px)
        f.setBold(bold)
        return f

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        e = self.e
        self._hits, self._btns = [], []
        if e.track and e.state != "error":
            g = QRadialGradient(QPointF(w * 0.5, h * 0.55), max(w, h) * 0.8)
            c = QColor(self.acc)
            c.setAlpha(48 if e.playing else 20)
            g.setColorAt(0, c)
            c0 = QColor(self.acc)
            c0.setAlpha(0)
            g.setColorAt(1, c0)
            p.fillRect(self.rect(), QBrush(g))
        if e.lines:
            self._synced(p, w, h)
        elif e.state == "plain" and e.plain:
            self._plain(p, w, h)
        else:
            self._empty(p, w, h)
        self._chips(p, w)

    def _synced(self, p, w, h):
        e = self.e
        idx, _t, prog, _n = e.line_state()
        disp = idx if self.disp is None else self.disp
        row = 40
        lit = mix(self.acc, QColor("#ffffff"), 0.6)
        for i in range(max(0, round(disp) - 3), min(len(e.lines), round(disp) + 4)):
            d = i - disp
            alpha = max(0.0, 1 - abs(d) / 3.3)
            if alpha <= 0.02:
                continue
            near = max(0.0, 1 - abs(d))
            f, txt = fit_font(e.lines[i][1], round(14 + 10 * near), round(12 + 4 * near), w - 24)
            fm = QFontMetrics(f)
            tw = fm.horizontalAdvance(txt)
            rect = QRectF((w - tw) / 2, h / 2 + d * row - row / 2, tw + 2, row)
            self._hits.append((QRectF(8, rect.top(), w - 16, row), i))
            p.setFont(f)
            p.setOpacity(alpha)
            if i == idx:
                halo = QColor(self.acc)
                halo.setAlpha(46)
                p.setPen(halo)
                for dx, dy in ((-1.6, 0), (1.6, 0), (0, -1.6), (0, 1.6)):
                    p.drawText(rect.translated(dx, dy), AL.AlignVCenter | AL.AlignLeft, txt)
                p.setPen(QPen(karaoke_brush(rect.left(), tw, prog, lit, QColor("#6e6e73")), 0))
            else:
                p.setPen(QColor("#d1d1d6" if i == self._hover else "#8e8e93"))
            p.drawText(rect, AL.AlignVCenter | AL.AlignLeft, txt)
        p.setOpacity(1.0)

    def _plain(self, p, w, h):
        """Текст без таймингов: прокручивается сам по ходу трека, колесо мыши — вручную."""
        e = self.e
        f = self._font(14)
        fm = QFontMetrics(f)
        key = (e.plain, w)
        if key != self._pkey:
            lay, y = [], 0
            flags = int(AL.AlignHCenter | Qt.TextFlag.TextWordWrap)
            for ln in e.plain.splitlines():
                ln = ln.strip()
                if not ln:
                    y += 12
                    continue
                hh = fm.boundingRect(QRect(0, 0, max(40, w - 44), 10000), flags, ln).height()
                lay.append((y, hh, ln))
                y += hh + 5
            self._pkey, self._play, self._ph = key, lay, y
        pos, dur = e.progress()
        span = max(0.0, self._ph - h + 60)
        target = (pos / dur if dur > 0 else 0.0) * span
        if time.time() - self._man_t > 7:
            self._scroll += (target - self._scroll) * 0.08
        p.setFont(f)
        flags = AL.AlignHCenter | Qt.TextFlag.TextWordWrap
        for y, hh, ln in self._play:
            vy = 34 + y - self._scroll
            if vy + hh < 0 or vy > h:
                continue
            edge = min(vy + hh / 2, h - (vy + hh / 2))
            p.setOpacity(max(0.15, min(1.0, edge / 40)))
            p.setPen(QColor("#d1d1d6"))
            p.drawText(QRectF(22, vy, w - 44, hh), flags, ln)
        p.setOpacity(1.0)
        f2 = self._font(11)
        p.setFont(f2)
        p.setPen(QColor("#636366"))
        p.drawText(QRectF(0, h - 18, w, 16), AL.AlignCenter, "Текст без таймингов — прокручивается по ходу трека")

    def _button(self, p, rect, text, key, primary):
        hov = self._hbtn == key
        p.setPen(Qt.PenStyle.NoPen)
        if primary:
            p.setBrush(mix(self.acc, QColor("#ffffff"), 0.18) if hov else self.acc)
        else:
            p.setBrush(QColor(255, 255, 255, 44 if hov else 26))
        p.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
        p.setFont(self._font(12, True))
        p.setPen(QColor("#000000") if primary else QColor("#d1d1d6"))
        p.drawText(rect, AL.AlignCenter, text)
        self._btns.append((rect, key))

    def _buttons(self, p, w, y, items):
        fm = QFontMetrics(self._font(12, True))
        widths = [fm.horizontalAdvance(t) + 30 for t, _k, _p in items]
        x = (w - sum(widths) - 10 * (len(items) - 1)) / 2
        for (t, k, prim), bw in zip(items, widths):
            self._button(p, QRectF(x, y, bw, 28), t, k, prim)
            x += bw + 10

    def _chip(self, p, x_right, text, on, key):
        f = self._font(11, True)
        cw = QFontMetrics(f).horizontalAdvance(text) + 20
        r = QRectF(x_right - cw, 2, cw, 22)
        hov = self._hbtn == key
        p.setPen(Qt.PenStyle.NoPen)
        if on:
            bg = QColor(self.acc)
            bg.setAlpha(230 if hov else 190)
        else:
            bg = QColor(255, 255, 255, 44 if hov else 24)
        p.setBrush(bg)
        p.drawRoundedRect(r, 11, 11)
        p.setFont(f)
        p.setPen(QColor("#000000") if on else QColor("#c7c7cc"))
        p.drawText(r, AL.AlignCenter, text)
        self._btns.append((r, key))
        return r.left() - 6

    def _chips(self, p, w):
        e = self.e
        if e.state == "error" or e.install_state:
            return
        x = self._chip(p, w - 2, "На острове" + ("  ✓" if self.island_on else ""), self.island_on, "island")
        if e.track and e.state in ("none", "net", "plain", "instr", "ok"):
            self._chip(p, x, "↻  Найти снова", False, "refetch")

    def _empty(self, p, w, h):
        e = self.e
        dots = "." * (int(time.time() * 2.5) % 4)
        py = sys.version_info
        buttons, hint = [], ""
        if e.install_state == "running":
            glyph, msg = "⚙", "Устанавливаю библиотеку" + dots
            hint = "Это займёт до минуты. Остров ничего не потеряет."
        elif e.install_state == "failed":
            glyph, msg = "⚠", "Не получилось установить автоматически"
            hint = (e.detail or "")[:150]
            buttons = [("Повторить", "install", True), ("Скопировать команду", "copy", False)]
        elif e.state == "error" and e.err_kind == "missing":
            glyph, msg = "⚠", "Нужна библиотека для чтения плеера Windows"
            fm = QFontMetrics(self._font(12))
            hint = f"Python {py.major}.{py.minor} · " + fm.elidedText(py_exe(), Qt.TextElideMode.ElideMiddle, w - 120)
            buttons = [("Установить автоматически", "install", True), ("Скопировать команду", "copy", False)]
        elif e.state == "error":
            glyph, msg = "⚠", "Не удалось подключиться к плееру Windows"
            hint = (e.detail or "")[:150]
            buttons = [("Перезапустить", "retry", True)]
        else:
            glyph, msg, hint = {
                "idle": ("♪", "Ничего не играет", "Включи музыку в Spotify, Яндекс Музыке или браузере"),
                "loading": ("♪", "Ищу текст" + dots, ""),
                "none": ("♪", "Для этого трека нет текста", "Музыка играет как обычно"),
                "instr": ("♪", "Инструментальный трек", "Слов здесь нет"),
                "net": ("⚠", "Нет связи с сервисом текстов", "Нажми «Найти снова», когда появится интернет"),
            }.get(e.state, ("♪", "", ""))
        if time.time() - self._copied < 2.0:
            hint = "Команда скопирована — вставь её в терминал, потом нажми «Перезапустить»"
            buttons = [("Перезапустить", "retry", True)]
        top = h / 2 - (96 if buttons else 78)
        p.setFont(self._font(44))
        p.setPen(mix(self.acc, QColor("#000000"), 0.35) if glyph == "♪" else QColor("#ff9f0a"))
        p.drawText(QRectF(0, top, w, 56), AL.AlignCenter, glyph)
        p.setFont(self._font(15, True))
        p.setPen(QColor("#a1a1a6"))
        p.drawText(QRectF(14, top + 58, w - 28, 26), AL.AlignCenter | Qt.TextFlag.TextWordWrap, msg)
        if hint:
            p.setFont(self._font(12))
            p.setPen(QColor("#636366"))
            p.drawText(QRectF(18, top + 86, w - 36, 36), AL.AlignHCenter | AL.AlignTop | Qt.TextFlag.TextWordWrap, hint)
        if buttons:
            self._buttons(p, w, top + 126, buttons)


class LyricsPage(QWidget):
    """Вкладка «Музыка»: обложка, трек, перемотка, управление плеером, сдвиг текста и сам текст."""
    offset_step = Signal(float)
    island_toggled = Signal(bool)

    def __init__(self, engine):
        super().__init__()
        self.e = engine
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)

        left = QWidget()
        left.setFixedWidth(252)
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(4)

        head = QHBoxLayout()
        head.setSpacing(6)
        head.addWidget(CoverWidget(engine), 0, AL.AlignTop)
        info = QVBoxLayout()
        info.setSpacing(0)
        info.addStretch()
        self.title = MarqueeText(16, "#ffffff", True)
        self.artist = MarqueeText(13, "#a1a1a6")
        self.state = ElidedText(11, "#636366")
        info.addWidget(self.title)
        info.addWidget(self.artist)
        info.addWidget(self.state)
        info.addStretch()
        head.addLayout(info, 1)
        ll.addLayout(head)

        ll.addWidget(SeekBar(engine))

        ctr = QHBoxLayout()
        ctr.setSpacing(14)
        ctr.addStretch()
        for kind, size, cmd in (("prev", 38, "prev"), ("play", 50, "toggle"), ("next", 38, "next")):
            b = TransportButton(kind, size, engine)
            b.clicked.connect(lambda c=cmd: engine.command(c))
            ctr.addWidget(b)
        ctr.addStretch()
        ll.addLayout(ctr)

        off = QHBoxLayout()
        off.setSpacing(6)
        small = btn_style("#1c1c1e", "#c7c7cc", "#2c2c2e", 10, 13)
        off.addWidget(mkbtn("−", small, lambda: self.offset_step.emit(-0.5), 30, 26))
        self.off_btn = mkbtn("", btn_style("transparent", "#8e8e93", "#1c1c1e", 10, 12),
                             lambda: self.offset_step.emit(-self.e.offset), None, 26)
        self.off_btn.setToolTip("Сдвиг текста относительно музыки: если строки опаздывают — нажми «+».\n"
                                "Клик по надписи — сбросить")
        off.addWidget(self.off_btn, 1)
        off.addWidget(mkbtn("+", small, lambda: self.offset_step.emit(0.5), 30, 26))
        ll.addLayout(off)
        ll.addStretch()
        lay.addWidget(left)

        self.view = LyricsView(engine)
        self.view.action.connect(self._on_action)
        lay.addWidget(self.view, 1)
        engine.changed.connect(self.refresh)
        self.refresh()

    def set_island_on(self, v):
        self.view.island_on = bool(v)
        self.view.update()

    def _on_action(self, key):
        if key == "island":
            self.island_toggled.emit(not self.view.island_on)
        elif key == "refetch":
            self.e.refetch()
        elif key == "install":
            self.e.install_deps()
        elif key == "copy":
            QGuiApplication.clipboard().setText(fix_command())
        elif key == "retry":
            self.e.retry_watcher()

    def refresh(self):
        e = self.e
        t = e.track or {}
        self.title.setText(t.get("title") or "Ничего не играет")
        self.artist.setText(t.get("artist") or "")
        status = {"loading": "Ищу текст…", "none": "Текст не найден", "net": "Нет связи с сервисом",
                  "ok": "Текст синхронизирован", "plain": "Текст без таймингов",
                  "instr": "Инструментал"}.get(e.state, "")
        app = t.get("app") or ""
        self.state.setText(" · ".join(x for x in (app, status) if x))
        self.off_btn.setText(f"Сдвиг текста {e.offset:+.1f} с".replace(".", ","))
        self.update()
        for w in self.findChildren(TransportButton):
            w.update()
