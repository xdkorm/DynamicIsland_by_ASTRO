"""История буфера обмена: текст, ссылки, файлы и картинки. Закрепление, поиск, возврат в буфер одним кликом.
Пароли из менеджеров паролей (они помечают данные «не записывать в историю») не сохраняются."""
import hashlib
import json
import re
import time
from pathlib import Path

from PySide6.QtCore import QMimeData, QObject, QRectF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QScrollArea, QVBoxLayout, QWidget

import common as C
from island_modules import DARK, GRAY, ORANGE, mkbtn

AL = Qt.AlignmentFlag
MAX_TEXT = 20000      # длиннее — обрезаем
MAX_ITEMS = 60        # не закреплённых записей
MAX_IMAGES = 15       # картинок среди них
ACCENT = QColor("#0a84ff")


def classify(t: str):
    """(метка типа, цвет-образец или None) для подписи у записи."""
    s = t.strip()
    if re.fullmatch(r"https?://\S+", s):
        return "ссылка", None
    if re.fullmatch(r"[\w.+-]+@[\w-]+\.[\w.-]+", s):
        return "почта", None
    if re.fullmatch(r"#[0-9a-fA-F]{6}", s):
        return "цвет", QColor(s)
    if "\n" not in s and (re.match(r"^[A-Za-z]:\\", s) or s.startswith("\\\\")):
        return "путь", None
    n = s.count("\n") + 1
    if n > 1:
        return f"{n} стр.", None
    return "", None


def fmt_ago(ts: float) -> str:
    d = max(0, int(time.time() - ts))
    if d < 10:
        return "сейчас"
    if d < 60:
        return f"{d} с"
    if d < 3600:
        return f"{d // 60} мин"
    if d < 86400:
        return f"{d // 3600} ч"
    return time.strftime("%d.%m", time.localtime(ts))


class ClipStore(QObject):
    """Следит за буфером обмена и хранит историю (новые сверху, закреплённые — выше всех)."""
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.items = []
        self.enabled = True     # писать историю
        self.persist = True     # хранить на диске между запусками
        self._skip_text = None  # то, что мы сами только что положили в буфер
        self._skip_img = None
        self._save_timer = QTimer(self, singleShot=True, interval=1500, timeout=self._save)
        self._load()
        QGuiApplication.clipboard().dataChanged.connect(lambda: QTimer.singleShot(90, self._read))

    # ---- хранение
    def _load(self):
        try:
            data = json.loads(C.CLIP_FILE.read_text("utf-8"))
            for it in data:
                if it.get("kind") == "image" and not (C.CLIP_DIR / it.get("file", "x")).exists():
                    continue
                if it.get("kind") in ("text", "files", "image"):
                    self.items.append(it)
        except Exception:
            pass

    def _save(self):
        try:
            if not self.persist:
                C.CLIP_FILE.unlink(missing_ok=True)
                return
            C.CFG_DIR.mkdir(parents=True, exist_ok=True)
            tmp = C.CLIP_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.items, ensure_ascii=False), "utf-8")
            tmp.replace(C.CLIP_FILE)
        except Exception as e:
            print("История буфера: не удалось сохранить:", e)

    def _touch(self):
        self._save_timer.start()
        self.changed.emit()

    def ordered(self):
        return [i for i in self.items if i.get("pin")] + [i for i in self.items if not i.get("pin")]

    # ---- чтение буфера
    def _read(self):
        if not self.enabled:
            return
        try:
            cb = QGuiApplication.clipboard()
            md = cb.mimeData()
            if md is None or md.hasFormat("ExcludeClipboardContentFromMonitorProcessing"):
                return  # менеджер паролей просит не записывать
            if md.hasUrls() and any(u.isLocalFile() for u in md.urls()):
                paths = [u.toLocalFile() for u in md.urls() if u.isLocalFile()]
                self._add("files", "\n".join(paths))
            elif md.hasText() and md.text().strip():
                self._add("text", md.text()[:MAX_TEXT])
            elif md.hasImage():
                img = cb.image()
                if not img.isNull():
                    self._add_image(img)
        except Exception as e:
            print("История буфера:", e)

    def _add(self, kind, text):
        if self._skip_text is not None and text == self._skip_text:
            self._skip_text = None
            return
        for it in self.items:
            if it["kind"] == kind and it["text"] == text:
                it["ts"] = time.time()
                self.items.remove(it)
                self.items.insert(0, it)
                self._touch()
                return
        self.items.insert(0, {"id": f"{int(time.time() * 1000)}", "kind": kind, "text": text,
                              "ts": time.time(), "pin": False})
        self._trim()
        self._touch()

    def _add_image(self, img: QImage):
        if max(img.width(), img.height()) > 1600:
            img = img.scaled(1600, 1600, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        try:
            digest = hashlib.md5(bytes(img.constBits())).hexdigest()
        except Exception:  # старые версии PySide отдают указатель вместо буфера
            digest = f"{img.width()}x{img.height()}-{img.cacheKey()}"
        if self._skip_img == digest:
            self._skip_img = None
            return
        for it in self.items:
            if it["kind"] == "image" and it.get("hash") == digest:
                it["ts"] = time.time()
                self.items.remove(it)
                self.items.insert(0, it)
                self._touch()
                return
        iid = f"{int(time.time() * 1000)}"
        C.CLIP_DIR.mkdir(parents=True, exist_ok=True)
        fname = iid + ".png"
        if not img.save(str(C.CLIP_DIR / fname), "PNG"):
            return
        self.items.insert(0, {"id": iid, "kind": "image", "text": f"Изображение {img.width()}×{img.height()}",
                              "file": fname, "hash": digest, "ts": time.time(), "pin": False})
        self._trim()
        self._touch()

    def _trim(self):
        free = [i for i in self.items if not i.get("pin")]
        imgs = [i for i in free if i["kind"] == "image"]
        drop = free[MAX_ITEMS:] + imgs[MAX_IMAGES:]
        for it in drop:
            if it in self.items:
                self._remove(it)

    def _remove(self, it):
        if it in self.items:
            self.items.remove(it)
        if it["kind"] == "image":
            try:
                (C.CLIP_DIR / it["file"]).unlink(missing_ok=True)
            except Exception:
                pass

    # ---- действия
    def copy_back(self, it):
        cb = QGuiApplication.clipboard()
        if it["kind"] == "image":
            img = QImage(str(C.CLIP_DIR / it["file"]))
            if not img.isNull():
                self._skip_img = it.get("hash")
                cb.setImage(img)
        elif it["kind"] == "files":
            md = QMimeData()
            md.setUrls([QUrl.fromLocalFile(p) for p in it["text"].splitlines()])
            md.setText(it["text"])
            self._skip_text = it["text"]
            cb.setMimeData(md)
        else:
            self._skip_text = it["text"]
            cb.setText(it["text"])

    def toggle_pin(self, it):
        it["pin"] = not it.get("pin")
        self._touch()

    def delete(self, it):
        self._remove(it)
        self._touch()

    def clear(self):
        for it in [i for i in self.items if not i.get("pin")]:
            self._remove(it)
        self._touch()

    def set_persist(self, on):
        if on != self.persist:
            self.persist = on
            self._save_timer.start()


class ClipRow(QWidget):
    copied = Signal(object)
    pinned = Signal(object)
    deleted = Signal(object)

    def __init__(self, item):
        super().__init__()
        self.it = item
        self.kind_label, self.swatch = classify(item["text"]) if item["kind"] == "text" else ("", None)
        if item["kind"] == "files":
            n = item["text"].count("\n") + 1
            self.kind_label = "файл" if n == 1 else f"{n} файла"
        self.pix = None
        if item["kind"] == "image":
            pm = QPixmap(str(C.CLIP_DIR / item["file"]))
            if not pm.isNull():
                self.pix = pm.scaled(52, 34, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            self.kind_label = "картинка"
        self._hover = False
        self._flash = 0.0
        self.setFixedHeight(44)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._anim = QTimer(self, interval=33, timeout=self._step)

    def preview(self):
        it = self.it
        if it["kind"] == "image":
            return it["text"]
        if it["kind"] == "files":
            first = it["text"].splitlines()[0].replace("\\", "/").rsplit("/", 1)[-1]
            return first or it["text"]
        for line in it["text"].splitlines():
            if line.strip():
                return re.sub(r"\s+", " ", line.strip())
        return it["text"].strip()

    def _step(self):
        if time.time() - self._flash > 0.8:
            self._anim.stop()
        self.update()

    def enterEvent(self, _):
        self._hover = True
        self.update()

    def leaveEvent(self, _):
        self._hover = False
        self.update()

    def mouseReleaseEvent(self, ev):
        if ev.button() != Qt.MouseButton.LeftButton:
            return
        x = ev.position().x()
        if x >= self.width() - 30:
            self.deleted.emit(self.it)
        elif x >= self.width() - 60:
            self.pinned.emit(self.it)
        else:
            self._flash = time.time()
            self._anim.start()
            self.copied.emit(self.it)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        flash = max(0.0, 1.0 - (time.time() - self._flash) / 0.8) if self._flash else 0.0
        bg = QColor(255, 255, 255, 26 if self._hover else 13)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(QRectF(0, 0, w, h), 12, 12)
        if flash > 0:
            c = QColor(ACCENT)
            c.setAlpha(int(110 * flash))
            p.setBrush(c)
            p.drawRoundedRect(QRectF(0, 0, w, h), 12, 12)
        pinned = bool(self.it.get("pin"))
        if pinned:
            p.setBrush(QColor("#ff9f0a"))
            p.drawRoundedRect(QRectF(0, 9, 3.5, h - 18), 1.7, 1.7)
        x = 14
        if self.pix is not None:
            path = QPainterPath()
            r = QRectF(x, (h - self.pix.height()) / 2, self.pix.width(), self.pix.height())
            path.addRoundedRect(r, 5, 5)
            p.save()
            p.setClipPath(path)
            p.drawPixmap(r.toRect(), self.pix)
            p.restore()
            x += self.pix.width() + 10
        elif self.swatch is not None:
            p.setBrush(self.swatch)
            p.setPen(QPen(QColor(255, 255, 255, 60), 1))
            p.drawEllipse(QRectF(x, h / 2 - 8, 16, 16))
            x += 26
        right = w - 12
        f = QFont("Segoe UI")
        f.setPixelSize(11)
        p.setFont(f)
        fm = QFontMetrics(f)
        if flash > 0.15:
            p.setPen(QColor("#ffffff"))
            lab = "✓ Скопировано"
            right -= fm.horizontalAdvance(lab)
            p.drawText(QRectF(right, 0, fm.horizontalAdvance(lab) + 2, h), AL.AlignVCenter | AL.AlignLeft, lab)
        elif self._hover:
            # звезда и крестик
            f2 = QFont("Segoe UI Symbol")
            f2.setPixelSize(16)
            p.setFont(f2)
            p.setPen(QColor("#ff9f0a") if pinned else QColor("#8e8e93"))
            p.drawText(QRectF(w - 58, 0, 26, h), AL.AlignCenter, "★" if pinned else "☆")
            p.setPen(QColor("#8e8e93"))
            p.drawText(QRectF(w - 30, 0, 26, h), AL.AlignCenter, "✕")
            right = w - 64
        else:
            tag = (self.kind_label + "  ·  " if self.kind_label else "") + fmt_ago(self.it["ts"])
            p.setPen(QColor("#636366"))
            tw = fm.horizontalAdvance(tag)
            right -= tw
            p.drawText(QRectF(right, 0, tw + 2, h), AL.AlignVCenter | AL.AlignLeft, tag)
        f3 = QFont("Segoe UI")
        f3.setPixelSize(14)
        p.setFont(f3)
        p.setPen(QColor("#ffffff"))
        avail = max(20, int(right - x - 12))
        p.drawText(QRectF(x, 0, avail, h), AL.AlignVCenter | AL.AlignLeft,
                   QFontMetrics(f3).elidedText(self.preview(), Qt.TextElideMode.ElideRight, avail))


class ClipPage(QWidget):
    """Вкладка «Буфер»: поиск, закрепление, клик — вернуть запись в буфер обмена."""
    pause_toggled = Signal(bool)

    def __init__(self, store: ClipStore):
        super().__init__()
        self.s = store
        self._dirty = True
        self.paused = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(6)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск по истории…")
        self.search.setFixedHeight(34)
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _t: self._rebuild())
        top.addWidget(self.search, 1)
        self.count = QLabel("")
        self.count.setStyleSheet("color:#636366;font-size:12px;")
        top.addWidget(self.count)
        self.b_pause = mkbtn("Пауза", DARK, self._toggle_pause, 96, 34)
        top.addWidget(self.b_pause)
        top.addWidget(mkbtn("Очистить", DARK, self._clear, 92, 34))
        lay.addLayout(top)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet(
            "QScrollArea,QScrollArea>QWidget>QWidget{background:transparent;border:none}"
            "QScrollBar:vertical{width:6px;background:transparent}"
            "QScrollBar::handle:vertical{background:#3a3a3c;border-radius:3px}"
            "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0}")
        inner = QWidget()
        self.col = QVBoxLayout(inner)
        self.col.setContentsMargins(0, 0, 6, 0)
        self.col.setSpacing(5)
        self.col.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll.setWidget(inner)
        lay.addWidget(self.scroll, 1)

        self.empty = QLabel("")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setStyleSheet("border:1.5px dashed #3a3a3c;border-radius:14px;color:#8e8e93;font-size:14px;")
        lay.addWidget(self.empty, 1)

        self.tick = QTimer(self, interval=20000, timeout=self.scroll.viewport().update)
        store.changed.connect(self._on_changed)
        self._rebuild()

    def set_paused(self, v):
        self.paused = bool(v)
        self.b_pause.setText("Продолжить" if self.paused else "Пауза")
        self.b_pause.setStyleSheet(ORANGE if self.paused else DARK)

    def _toggle_pause(self):
        self.pause_toggled.emit(not self.paused)

    def _clear(self):
        self.s.clear()

    def _on_changed(self):
        if self.isVisible():
            self._rebuild()
        else:
            self._dirty = True

    def showEvent(self, _):
        if self._dirty:
            self._rebuild()
        self.tick.start()

    def hideEvent(self, _):
        self.tick.stop()

    def _rebuild(self):
        self._dirty = False
        q = self.search.text().strip().lower()
        items = [i for i in self.s.ordered() if not q or q in i["text"].lower()]
        while self.col.count():
            it = self.col.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        for it in items:
            row = ClipRow(it)
            row.copied.connect(self.s.copy_back)
            row.pinned.connect(self.s.toggle_pin)
            row.deleted.connect(self.s.delete)
            self.col.addWidget(row)
        total = len(self.s.items)
        self.count.setText(f"{total}" if total else "")
        if items:
            self.empty.hide()
            self.scroll.show()
        else:
            self.scroll.hide()
            self.empty.setText("Ничего не найдено" if q else
                               ("Запись приостановлена" if self.paused else
                                "История пуста\nСкопируй что-нибудь (Ctrl+C) — оно появится здесь"))
            self.empty.show()
