"""Запись экрана и скриншоты: вкладка «Запись».
Видео пишет ffmpeg (gdigrab), звук компьютера и микрофон — PyAudioWPatch (WASAPI loopback); в конце всё склеивается в mp4.
Нужно:  pip install imageio-ffmpeg   (или положи ffmpeg.exe рядом с островом / добавь ffmpeg в PATH)
        pip install PyAudioWPatch   (только если нужен звук)
"""
import os
import shutil
import subprocess
import threading
import time
import wave
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFontMetrics, QGuiApplication
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

import common as C
from island_snip import start_snip

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
MODES = [("screen", "Только экран"), ("sys", "Экран + звук ПК"), ("sysmic", "Экран + ПК + микрофон")]
RED, RED_HOVER = "#ff5a64", "#ff7078"
MSG_COLOR = {"info": "#8e8e93", "done": "#30d158", "ok": "#30d158", "warn": "#ff9f0a", "err": "#ff453a"}

MODE_QSS = ("QPushButton{background:#1c1c1e;color:#fff;border:none;border-radius:12px;font-size:14px;"
            "font-weight:600;padding:0 8px}QPushButton:hover{background:#2c2c2e}"
            f"QPushButton:checked{{background:{RED};color:#000}}"
            "QPushButton:disabled{color:#636366}QPushButton:checked:disabled{background:#7d3d42;color:#1a1a1a}")
START_QSS = (f"QPushButton{{background:{RED};color:#fff;border:none;border-radius:14px;font-size:15px;"
             f"font-weight:600}}QPushButton:hover{{background:{RED_HOVER}}}"
             "QPushButton:disabled{background:#5a3a3d;color:#a08a8c}")
SHOT_QSS = ("QPushButton{background:#1f1f22;color:#fff;border:none;border-radius:14px;font-size:15px;"
            "font-weight:600}QPushButton:hover{background:#2c2c2e}QPushButton:disabled{color:#636366}")

SNIP_QSS = ("QPushButton{background:#0a84ff;color:#fff;border:none;border-radius:14px;font-size:15px;"
            "font-weight:600}QPushButton:hover{background:#3d9bff}QPushButton:disabled{background:#1d3c5f;color:#7f93a8}")

_ffmpeg_cache = {}


def find_ffmpeg():
    """Путь к ffmpeg: PATH → рядом с программой → бинарник из imageio-ffmpeg. None, если не нашли."""
    if "p" in _ffmpeg_cache:
        return _ffmpeg_cache["p"]
    found = shutil.which("ffmpeg")
    if not found:
        here = Path(__file__).parent
        for cand in (here / "ffmpeg.exe", here / "ffmpeg" / "bin" / "ffmpeg.exe", here / "ffmpeg"):
            if cand.is_file():
                found = str(cand)
                break
    if not found:
        try:
            import imageio_ffmpeg
            found = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            found = None
    if found:  # «не нашли» не кэшируем — пользователь мог доустановить библиотеку, не закрывая остров
        _ffmpeg_cache["p"] = found
    return found


def fmt_clock(sec):
    sec = max(0, int(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


# ───────────────────────── звук (WASAPI) ─────────────────────────
def _load_pyaudio():
    try:
        import pyaudiowpatch as pyaudio
        return pyaudio
    except Exception:
        return None


def loopback_device(pa, pyaudio):
    """Устройство «то, что слышно в колонках/наушниках» (звук ПК)."""
    wasapi = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
    spk = pa.get_device_info_by_index(wasapi["defaultOutputDevice"])
    if spk.get("isLoopbackDevice"):
        return spk
    for lb in pa.get_loopback_device_info_generator():
        if spk["name"] in lb["name"]:
            return lb
    raise RuntimeError("не нашёл устройство вывода звука")


def mic_device(pa, pyaudio):
    wasapi = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
    idx = wasapi.get("defaultInputDevice", -1)
    if idx is None or idx < 0:
        raise RuntimeError("микрофон не найден")
    return pa.get_device_info_by_index(idx)


class AudioTap:
    """Пишет звук одного устройства в WAV. Loopback отдаёт данные только пока что-то играет, поэтому
    пропуски (тишину) добиваем нулями — иначе дорожка получится короче видео и звук поедет."""

    def __init__(self, pa, pyaudio, dev, path):
        self.pa, self.pyaudio, self.dev, self.path = pa, pyaudio, dev, Path(path)
        self.rate = int(dev["defaultSampleRate"])
        self.ch = max(1, int(dev["maxInputChannels"]))
        self.frames, self.t0 = 0, 0.0
        self.stream, self.wf = None, None
        self.lock = threading.Lock()

    def _zeros(self, n):
        chunk = self.rate  # не больше секунды за раз, чтобы не собирать гигантские строки
        while n > 0:
            k = min(n, chunk)
            self.wf.writeframes(b"\x00" * (k * 2 * self.ch))
            self.frames += k
            n -= k

    def _cb(self, data, frame_count, _ti, _status):
        try:
            with self.lock:
                if self.wf is None:
                    return None, self.pyaudio.paComplete
                gap = int((time.monotonic() - self.t0) * self.rate) - self.frames - frame_count
                if gap > self.rate * 0.04:
                    self._zeros(gap)
                self.wf.writeframes(data)
                self.frames += frame_count
        except Exception:
            pass
        return None, self.pyaudio.paContinue

    def start(self):
        self.wf = wave.open(str(self.path), "wb")
        self.wf.setnchannels(self.ch)
        self.wf.setsampwidth(2)
        self.wf.setframerate(self.rate)
        self.t0 = time.monotonic()
        self.stream = self.pa.open(format=self.pyaudio.paInt16, channels=self.ch, rate=self.rate, input=True,
                                   input_device_index=int(self.dev["index"]), frames_per_buffer=1024,
                                   stream_callback=self._cb)
        self.stream.start_stream()

    def stop(self, total_sec):
        try:
            if self.stream is not None:
                self.stream.stop_stream()
                self.stream.close()
        except Exception:
            pass
        with self.lock:
            if self.wf is not None:
                missing = int(total_sec * self.rate) - self.frames
                if missing > 0:
                    self._zeros(missing)
                self.wf.close()
                self.wf = None


# ───────────────────────── запись ─────────────────────────
class ScreenRecorder(QObject):
    state = Signal()              # сменилась фаза (старт / запись / сохранение / готово)
    message = Signal(str, str)    # (текст, вид: info | ok | warn | err)
    _finish_done = Signal(str, str)

    def __init__(self, get_cfg):
        super().__init__()
        self.get_cfg = get_cfg
        self.phase = "idle"       # idle | starting | recording | saving
        self.t_start = 0.0
        self.last_path = ""
        self._proc, self._pa, self._taps = None, None, []
        self._vid, self._log, self._final = None, None, ""
        self._want_sys = self._want_mic = False
        self._deadline, self._dur = 0.0, 0.0
        self._poll = QTimer(self, interval=120, timeout=self._poll_start)
        self._finish_done.connect(self._on_finish_done)

    # ---- состояние
    def elapsed(self):
        return time.monotonic() - self.t_start if self.phase == "recording" else 0.0

    def pill_text(self):
        """Время записи для пилюли острова (None, если запись не идёт)."""
        return fmt_clock(self.elapsed()) if self.phase == "recording" else None

    def _set_phase(self, phase):
        self.phase = phase
        self.state.emit()

    # ---- старт
    def start(self, mode):
        if self.phase != "idle":
            return
        ff = find_ffmpeg()
        if not ff:
            self.message.emit("Нужен ffmpeg:  pip install imageio-ffmpeg  (или положи ffmpeg.exe рядом с островом)", "err")
            return
        cfg = self.get_cfg()
        try:
            out = C.media_dir(cfg, "video")
            out.mkdir(parents=True, exist_ok=True)
            tmp = C.CFG_DIR / "rec_tmp"
            tmp.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            self.message.emit(f"Не могу создать папку для записей: {e}", "err")
            return
        stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
        self._vid, self._log = tmp / f"{stamp}.mkv", tmp / f"{stamp}.log"
        self._final = str(out / f"Запись {stamp}.mp4")
        scr = QGuiApplication.primaryScreen()
        g, dpr = scr.geometry(), scr.devicePixelRatio()
        x, y, w, h = round(g.x() * dpr), round(g.y() * dpr), round(g.width() * dpr), round(g.height() * dpr)
        fps = int(cfg.get("rec_fps", 30))
        cmd = [ff, "-y", "-hide_banner", "-loglevel", "error", "-stats",
               "-f", "gdigrab", "-framerate", str(fps), "-draw_mouse", "1" if cfg.get("rec_cursor", True) else "0",
               "-offset_x", str(x), "-offset_y", str(y), "-video_size", f"{w}x{h}", "-i", "desktop",
               "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
               "-c:v", "libx264", "-preset", "ultrafast", "-crf", "25", "-pix_fmt", "yuv420p", str(self._vid)]
        try:
            self._proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                          stderr=open(self._log, "wb"), creationflags=NO_WINDOW)
        except Exception as e:
            self.message.emit(f"ffmpeg не запустился: {e}", "err")
            return
        self._want_sys, self._want_mic = mode in ("sys", "sysmic"), mode == "sysmic"
        self._deadline = time.monotonic() + 6.0
        self.message.emit("Запускаю запись…", "info")
        self._set_phase("starting")
        self._poll.start()

    def _log_text(self):
        try:
            return Path(self._log).read_bytes().decode("utf-8", "ignore")
        except Exception:
            return ""

    def _poll_start(self):
        if self._proc is None:
            self._poll.stop()
            return
        if self._proc.poll() is not None:  # ffmpeg упал на старте
            self._poll.stop()
            tail = (self._log_text().strip().splitlines() or ["неизвестная ошибка"])[-1]
            self._cleanup_tmp()
            self._proc = None
            self.message.emit(f"ffmpeg не смог начать запись: {tail}", "err")
            self._set_phase("idle")
            return
        if "frame=" in self._log_text() or time.monotonic() > self._deadline:
            self._poll.stop()
            self._begin_recording()

    def _begin_recording(self):
        self.t_start = time.monotonic()  # видео пошло — от этого момента ведём и звук, и таймер
        notes = []
        if self._want_sys or self._want_mic:
            pyaudio = _load_pyaudio()
            if pyaudio is None:
                notes.append("звук не пишется — pip install PyAudioWPatch")
            else:
                try:
                    self._pa = pyaudio.PyAudio()
                except Exception as e:
                    notes.append(f"звук недоступен: {e}")
                if self._pa is not None:
                    tmp = Path(self._vid).parent
                    for kind, want, picker, label in (("sys", self._want_sys, loopback_device, "звук ПК"),
                                                      ("mic", self._want_mic, mic_device, "микрофон")):
                        if not want:
                            continue
                        try:
                            tap = AudioTap(self._pa, pyaudio, picker(self._pa, pyaudio), tmp / f"{Path(self._vid).stem}_{kind}.wav")
                            tap.start()
                            self._taps.append(tap)
                        except Exception as e:
                            notes.append(f"{label} недоступен: {e}")
        self.message.emit("Идёт запись" + (" · " + "; ".join(notes) if notes else ""), "warn" if notes else "info")
        self._set_phase("recording")

    # ---- стоп
    def stop(self):
        if self.phase == "starting":
            self._poll.stop()
            self._kill()
            self._cleanup_tmp()
            self.message.emit("Запись отменена", "info")
            self._set_phase("idle")
            return
        if self.phase != "recording":
            return
        self._dur = time.monotonic() - self.t_start
        self.message.emit("Сохраняю видео…", "info")
        self._set_phase("saving")
        threading.Thread(target=lambda: self._finish_done.emit(*self._finish()), daemon=True).start()

    def _kill(self):
        try:
            if self._proc is not None:
                self._proc.kill()
        except Exception:
            pass
        self._proc = None

    def _cleanup_tmp(self):
        for p in (self._vid, self._log):
            try:
                if p:
                    Path(p).unlink(missing_ok=True)
            except Exception:
                pass

    def _finish(self):
        """Останавливает ffmpeg и звук, склеивает mp4. Возвращает (путь, ошибка). Работает в фоновом потоке."""
        proc = self._proc
        try:
            proc.stdin.write(b"q\n")  # так ffmpeg корректно закрывает файл
            proc.stdin.flush()
        except Exception:
            pass
        try:
            proc.wait(timeout=20)
        except Exception:
            proc.kill()
        wavs = []
        for tap in self._taps:
            try:
                tap.stop(self._dur)
                wavs.append(str(tap.path))
            except Exception:
                pass
        try:
            if self._pa is not None:
                self._pa.terminate()
        except Exception:
            pass
        self._taps, self._pa, self._proc = [], None, None
        vid = str(self._vid)
        if not os.path.exists(vid) or os.path.getsize(vid) < 1024:
            return vid, "ffmpeg не записал видео"
        ff = find_ffmpeg()
        cmd = [ff, "-y", "-hide_banner", "-loglevel", "error", "-i", vid]
        for w in wavs:
            cmd += ["-i", w]
        if len(wavs) == 2:
            cmd += ["-filter_complex", "[1:a][2:a]amix=inputs=2:duration=longest:dropout_transition=0,volume=2[a]",
                    "-map", "0:v", "-map", "[a]"]
        elif len(wavs) == 1:
            cmd += ["-map", "0:v", "-map", "1:a"]
        cmd += ["-c:v", "copy"]
        if wavs:
            cmd += ["-c:a", "aac", "-b:a", "192k"]
        cmd += ["-movflags", "+faststart", self._final]
        try:
            r = subprocess.run(cmd, capture_output=True, creationflags=NO_WINDOW, timeout=600)
            if r.returncode != 0:
                tail = (r.stderr.decode("utf-8", "ignore").strip().splitlines() or ["?"])[-1]
                return vid, f"не удалось собрать mp4: {tail}"
        except Exception as e:
            return vid, f"не удалось собрать mp4: {e}"
        for p in [vid, str(self._log)] + wavs:  # временные файлы больше не нужны
            try:
                Path(p).unlink(missing_ok=True)
            except Exception:
                pass
        return self._final, ""

    def _on_finish_done(self, path, err):
        if err:
            self.message.emit(f"{err}. Исходник: {path}", "err")
        else:
            self.message.emit(f"Сохранено: {path}", "ok")
        self.last_path = path
        self._set_phase("idle")

    def shutdown(self):
        """Остров закрывают во время записи — аккуратно досохраняем, чтобы не потерять видео."""
        self._poll.stop()
        if self.phase == "starting":
            self._kill()
            self._cleanup_tmp()
        elif self.phase == "recording":
            self._dur = time.monotonic() - self.t_start
            self.phase = "saving"
            self._finish()


# ───────────────────────── вкладка ─────────────────────────
class RecordPage(QWidget):
    mode_changed = Signal(str)

    def __init__(self, get_cfg):
        super().__init__()
        self.get_cfg = get_cfg
        self.rec = ScreenRecorder(get_cfg)
        self.rec.state.connect(self._refresh)
        self.rec.message.connect(self.set_status)
        mode = get_cfg().get("rec_mode", "sys")
        self.mode = mode if mode in dict(MODES) else "sys"
        self._full_status = ""

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        modes = QHBoxLayout()
        modes.setSpacing(8)
        self.mode_btns = {}
        for key, label in MODES:
            b = QPushButton(label)
            b.setCheckable(True)
            b.setChecked(key == self.mode)
            b.setFixedHeight(36)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.setStyleSheet(MODE_QSS)
            b.clicked.connect(lambda _=False, k=key: self.pick_mode(k))
            self.mode_btns[key] = b
            modes.addWidget(b, 1)
        lay.addLayout(modes)

        lay.addStretch()
        self.clock = QLabel("00:00")
        self.clock.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.clock.setStyleSheet("font-size:50px;font-weight:300;color:#ffffff;")
        lay.addWidget(self.clock)

        self.status = QPushButton()  # плоская «надпись»: по клику открывает папку с последним файлом
        self.status.setFlat(True)
        self.status.setFixedHeight(20)
        self.status.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.status.clicked.connect(self._open_last)
        lay.addWidget(self.status)
        lay.addStretch()

        bottom = QHBoxLayout()
        bottom.setSpacing(10)
        self.start_btn = QPushButton()
        self.start_btn.setFixedHeight(44)
        self.start_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.start_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.start_btn.setStyleSheet(START_QSS)
        self.start_btn.clicked.connect(self._toggle)
        self.snip_btn = QPushButton("✂  Ножницы")
        self.snip_btn.setFixedHeight(44)
        self.snip_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.snip_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.snip_btn.setStyleSheet(SNIP_QSS)
        self.snip_btn.setToolTip("Выдели область — она попадёт в буфер обмена; потом можно порисовать и написать текст")
        self.snip_btn.clicked.connect(self.scissors)
        self.shot_btn = QPushButton("Весь экран")
        self.shot_btn.setFixedHeight(44)
        self.shot_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.shot_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.shot_btn.setStyleSheet(SHOT_QSS)
        self.shot_btn.clicked.connect(self.screenshot)
        bottom.addWidget(self.start_btn, 3)
        bottom.addWidget(self.snip_btn, 2)
        bottom.addWidget(self.shot_btn, 2)
        lay.addLayout(bottom)

        self.ticker = QTimer(self, interval=200, timeout=self._tick)
        self.ticker.start()
        self._refresh()
        self._idle_hint()

    # ---- режимы / кнопки
    def pick_mode(self, key):
        if self.rec.phase != "idle":
            self.mode_btns[key].setChecked(key == self.mode)
            return
        self.mode = key
        for k, b in self.mode_btns.items():
            b.setChecked(k == key)
        self.mode_changed.emit(key)

    def _toggle(self):
        if self.rec.phase == "idle":
            self.rec.start(self.mode)
        else:
            self.rec.stop()

    def _refresh(self):
        ph = self.rec.phase
        busy = ph != "idle"
        for b in self.mode_btns.values():
            b.setEnabled(not busy)
        self.start_btn.setEnabled(ph in ("idle", "recording", "starting"))
        self.start_btn.setText({"idle": "●  Начать запись", "starting": "■  Отменить", "recording": "■  Остановить запись",
                                "saving": "Сохраняю…"}[ph])
        self._tick()

    def _tick(self):
        rec = self.rec.phase == "recording"
        self.clock.setText(fmt_clock(self.rec.elapsed()))
        self.clock.setStyleSheet(f"font-size:50px;font-weight:300;color:{RED if rec else '#ffffff'};")

    # ---- статус
    def _idle_hint(self):
        if not find_ffmpeg():
            self.set_status("Нужен ffmpeg:  pip install imageio-ffmpeg  (или положи ffmpeg.exe рядом с островом)", "warn")
        else:
            self.set_status("Выбери режим и нажми «Начать запись»", "info")

    def set_status(self, text, kind="info"):
        self._full_status = text
        fm = QFontMetrics(self.status.font())
        shown = fm.elidedText(text, Qt.TextElideMode.ElideMiddle, 620)
        self.status.setText(shown)
        self.status.setToolTip(text)
        clickable = kind == "ok" and bool(self.rec.last_path)
        self.status.setCursor(Qt.CursorShape.PointingHandCursor if clickable else Qt.CursorShape.ArrowCursor)
        self.status.setStyleSheet(f"QPushButton{{color:{MSG_COLOR.get(kind, '#8e8e93')};background:transparent;"
                                  f"border:none;font-size:12px}}" + ("QPushButton:hover{color:#fff}" if clickable else ""))

    def _open_last(self):
        path = self.rec.last_path
        if not path or not os.path.exists(path):
            return
        if os.name == "nt":
            try:
                subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
                return
            except Exception:
                pass
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).parent)))

    # ---- ножницы: выделил область → в буфере; можно рисовать и писать текст
    def scissors(self):
        self.snip_btn.setEnabled(False)
        self.shot_btn.setEnabled(False)
        self.window().hide()                 # остров не должен попасть в кадр
        QTimer.singleShot(320, self._open_snip)

    def _open_snip(self):
        try:
            self._snip = start_snip(self.get_cfg, self._snip_done)
        except Exception as e:
            self._snip = None
            self._snip_done("error", str(e))

    def _snip_done(self, kind, path):
        self._snip = None
        self.window().show()
        self.snip_btn.setEnabled(True)
        self.shot_btn.setEnabled(True)
        if kind == "save":
            self.rec.last_path = path
            self.set_status(f"Ножницы: сохранено и скопировано в буфер — {path}", "ok")
        elif kind == "copy":
            self.set_status("Ножницы: снимок с пометками скопирован в буфер обмена", "done")
        elif kind == "error":
            self.set_status(f"Ножницы не открылись: {path}", "err")
        else:
            self.set_status("Ножницы закрыты", "info")

    # ---- скриншот всего экрана
    def screenshot(self):
        win = self.window()
        self.shot_btn.setEnabled(False)
        self.snip_btn.setEnabled(False)
        win.hide()  # прячем остров, чтобы он не попал в кадр
        QTimer.singleShot(320, self._grab)

    def _grab(self):
        try:
            pm = QGuiApplication.primaryScreen().grabWindow(0)
        finally:
            self.window().show()
            self.shot_btn.setEnabled(True)
            self.snip_btn.setEnabled(True)
        try:
            folder = C.media_dir(self.get_cfg(), "shot")
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"Скриншот {time.strftime('%Y-%m-%d_%H-%M-%S')}.png"
            if not pm.save(str(path), "PNG"):
                raise OSError("не удалось записать файл")
            QGuiApplication.clipboard().setPixmap(pm)
            self.rec.last_path = str(path)
            self.set_status(f"Скриншот сохранён и скопирован в буфер: {path}", "ok")
        except Exception as e:
            self.set_status(f"Скриншот не получился: {e}", "err")

    def shutdown(self):
        self.rec.shutdown()
