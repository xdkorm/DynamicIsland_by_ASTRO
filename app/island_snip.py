"""Ножницы: выдели область экрана — она сразу попадает в буфер обмена. Дальше можно порисовать кистью
или написать текст поверх выделенного (цвет, толщина и размер текста меняются), и нажать «Готово» —
картинка с твоими пометками заменит снимок в буфере. Есть «Отменить» и «Сохранить».

Клавиши: Esc — отмена, Enter — готово, Ctrl+Z — отменить последнее, Ctrl+S — сохранить файл.
"""
import time

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QCursor, QFont, QFontMetrics, QGuiApplication, QImage, QPainter, QPainterPath,
                           QPen, QPixmap)
from PySide6.QtWidgets import QColorDialog, QHBoxLayout, QLineEdit, QPushButton, QWidget

import common as C

PALETTE = ["#ff453a", "#ff9f0a", "#ffd60a", "#30d158", "#0a84ff", "#bf5af2", "#ffffff", "#000000"]
PEN_WIDTHS = [3, 6, 11]          # тонкая, средняя, толстая кисть
TEXT_SIZES = [20, 30, 46]        # размеры текста, px
ACCENT = "#0a84ff"

BAR_QSS = ("#snipbar{background:#1c1c1e;border:1px solid #3a3a3c;border-radius:20px}"
           "QPushButton{background:transparent;color:#ffffff;border:none;border-radius:13px;font-size:14px;"
           "font-weight:600;padding:0 12px}QPushButton:hover{background:#2c2c2e}"
           f"QPushButton:checked{{background:{ACCENT};color:#ffffff}}")


def draw_items(p, items):
    """Рисует пометки (кисть и текст) — и на экране, и в итоговой картинке."""
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    for it in items:
        if it["t"] == "pen":
            pen = QPen(it["c"], it["w"], Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            pts = it["pts"]
            if len(pts) == 1:                                    # одиночный клик — точка
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(it["c"])
                p.drawEllipse(pts[0], it["w"] / 2, it["w"] / 2)
                continue
            path = QPainterPath(pts[0])                          # сглаживаем линию через середины отрезков
            for a, b in zip(pts[:-1], pts[1:]):
                path.quadTo(a, QPointF((a.x() + b.x()) / 2, (a.y() + b.y()) / 2))
            path.lineTo(pts[-1])
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)
        else:
            f = QFont("Segoe UI")
            f.setPixelSize(it["px"])
            f.setBold(True)
            fm = QFontMetrics(f)
            base = QPointF(it["pos"].x(), it["pos"].y() + fm.ascent())
            p.setFont(f)
            if it["c"].lightness() > 140:                        # светлый текст получает мягкую тень — читается на любом фоне
                p.setPen(QColor(0, 0, 0, 120))
                p.drawText(base + QPointF(1.2, 1.2), it["s"])
            p.setPen(it["c"])
            p.drawText(base, it["s"])


class SnipEdit(QLineEdit):
    """Поле, в котором пишется текст прямо на снимке."""
    cancelled = Signal()

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()
            return
        super().keyPressEvent(ev)


class SizeButton(QPushButton):
    """Кнопка размера: круг нужного диаметра (толщина кисти / размер текста)."""

    def __init__(self, r):
        super().__init__()
        self.r = r
        self.setCheckable(True)
        self.setFixedSize(30, 30)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def paintEvent(self, ev):
        super().paintEvent(ev)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(QPointF(self.width() / 2, self.height() / 2), self.r, self.r)


class SnipOverlay(QWidget):
    """Затемнённый экран: выделяешь область, потом рисуешь и пишешь."""
    finished = Signal(str, str)      # (что сделали: "cancel" | "copy" | "save", путь к файлу или "")

    def __init__(self, get_cfg):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.get_cfg = get_cfg
        scr = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        self.shot = scr.grabWindow(0)                              # снимок экрана до появления затемнения
        self.dpr = self.shot.devicePixelRatio() or 1.0
        geo = scr.geometry()
        self.setGeometry(geo)
        self.dim = QPixmap(self.shot)                              # затемнённая копия — чтобы выделение «светилось»
        pd = QPainter(self.dim)
        pd.fillRect(QRect(0, 0, geo.width(), geo.height()), QColor(0, 0, 0, 120))
        pd.end()

        self.mode = "select"                                       # select → edit
        self.sel = QRect()
        self.start = QPoint()
        self.items, self.cur = [], None                            # готовые пометки / штрих в процессе
        self.tool = "pen"
        self.colors = {"pen": QColor("#ff453a"), "text": QColor("#ffd60a")}      # у кисти и у текста свои цвета
        self.size_idx = {"pen": 1, "text": 1}
        self.toast, self._done = "", False

        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.edit = SnipEdit(self)
        self.edit.hide()
        self.edit.returnPressed.connect(self._commit_text)
        self.edit.cancelled.connect(self._cancel_text)
        self.edit.textChanged.connect(self._grow_edit)
        self._build_bar()
        self.bar.hide()

    # ───────── панель инструментов ─────────
    def _build_bar(self):
        self.bar = QWidget(self)
        self.bar.setObjectName("snipbar")
        self.bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.bar.setStyleSheet(BAR_QSS)
        lay = QHBoxLayout(self.bar)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(6)

        def btn(text, tip, cb, checkable=False):
            b = QPushButton(text)
            b.setFixedHeight(30)
            b.setToolTip(tip)
            b.setCheckable(checkable)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.clicked.connect(lambda _=False: cb())
            lay.addWidget(b)
            return b

        self.b_pen = btn("✏  Кисть", "Рисовать", lambda: self.set_tool("pen"), True)
        self.b_text = btn("T  Текст", "Написать текст: кликни по снимку, набери и нажми Enter",
                          lambda: self.set_tool("text"), True)
        lay.addSpacing(6)
        self.swatches = []
        for hexc in PALETTE:
            s = QPushButton()
            s.setFixedSize(22, 22)
            s.setCursor(Qt.CursorShape.PointingHandCursor)
            s.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            s.setToolTip("Цвет")
            s.clicked.connect(lambda _=False, c=hexc: self.set_color(QColor(c)))
            s.setProperty("hex", hexc)
            lay.addWidget(s)
            self.swatches.append(s)
        self.b_custom = QPushButton("+")
        self.b_custom.setFixedSize(22, 22)
        self.b_custom.setToolTip("Свой цвет")
        self.b_custom.setCursor(Qt.CursorShape.PointingHandCursor)
        self.b_custom.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.b_custom.clicked.connect(self.pick_custom)
        lay.addWidget(self.b_custom)
        lay.addSpacing(6)
        self.size_btns = []
        for i, r in enumerate((3, 5.5, 8.5)):
            sb = SizeButton(r)
            sb.setToolTip("Толщина кисти / размер текста")
            sb.clicked.connect(lambda _=False, i=i: self.set_size(i))
            lay.addWidget(sb)
            self.size_btns.append(sb)
        lay.addSpacing(6)
        btn("↶", "Отменить последнее (Ctrl+Z)", self.undo)
        btn("⟲  Заново", "Выбрать другую область", self.restart)
        btn("💾", "Сохранить в файл (Ctrl+S)", lambda: self.finish(save=True))
        ok = btn("✓  Готово", "Скопировать с пометками (Enter)", lambda: self.finish())
        ok.setStyleSheet(f"QPushButton{{background:{ACCENT};color:#fff;border-radius:13px;padding:0 14px}}"
                         "QPushButton:hover{background:#3d9bff}")
        btn("✕", "Закрыть (Esc)", self.cancel)
        self.bar.adjustSize()
        self._sync_bar()

    def _sync_bar(self):
        self.b_pen.setChecked(self.tool == "pen")
        self.b_text.setChecked(self.tool == "text")
        cur = self.colors[self.tool]
        in_palette = False
        for s in self.swatches:
            on = QColor(s.property("hex")) == cur
            in_palette = in_palette or on
            s.setStyleSheet(f"QPushButton{{background:{s.property('hex')};border-radius:11px;"
                            f"border:2px solid {'#ffffff' if on else 'rgba(255,255,255,70)'}}}")
        if in_palette:
            self.b_custom.setStyleSheet("QPushButton{background:#2c2c2e;color:#fff;border-radius:11px;"
                                        "border:2px solid rgba(255,255,255,70);padding:0;font-size:14px}")
        else:
            self.b_custom.setStyleSheet(f"QPushButton{{background:{cur.name()};color:#fff;border-radius:11px;"
                                        "border:2px solid #ffffff;padding:0;font-size:14px}")
        for i, sb in enumerate(self.size_btns):
            sb.setChecked(i == self.size_idx[self.tool])

    def _place_bar(self):
        r, W, H = self.sel, self.width(), self.height()
        bw, bh = self.bar.width(), self.bar.height()
        x = max(8, min(W - bw - 8, r.left()))
        y = r.bottom() + 12
        if y + bh > H - 8:                                      # снизу нет места — ставим над выделением
            y = r.top() - bh - 12
        if y < 8:                                               # и сверху нет — внутрь
            y = max(8, r.bottom() - bh - 12)
        self.bar.move(x, y)
        self.bar.show()
        self.bar.raise_()

    # ───────── инструменты ─────────
    def set_tool(self, tool):
        self._commit_text()
        self.tool = tool
        self._sync_bar()
        self._update_cursor(self.mapFromGlobal(QCursor.pos()))

    def set_color(self, c):
        self.colors[self.tool] = c
        if self.edit.isVisible():
            self._style_edit()
        self._sync_bar()

    def pick_custom(self):
        dlg = QColorDialog(self.colors[self.tool], self)
        dlg.setWindowTitle("Свой цвет")
        dlg.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        dlg.setOption(QColorDialog.ColorDialogOption.DontUseNativeDialog, True)
        if dlg.exec():
            self.set_color(dlg.currentColor())
        self.activateWindow()
        self.setFocus()

    def set_size(self, i):
        self.size_idx[self.tool] = i
        if self.edit.isVisible():
            self._style_edit()
        self._sync_bar()

    def undo(self):
        if self.edit.isVisible():
            self._cancel_text()
        elif self.items:
            self.items.pop()
            self.update()

    def restart(self):
        self._cancel_text()
        self.items.clear()
        self.cur, self.mode, self.sel = None, "select", QRect()
        self.bar.hide()
        self.toast = ""
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.update()

    # ───────── текст ─────────
    def _text_font(self):
        f = QFont("Segoe UI")
        f.setPixelSize(TEXT_SIZES[self.size_idx["text"]])
        f.setBold(True)
        return f

    def _style_edit(self):
        c = self.colors["text"]
        f = self._text_font()
        self.edit.setFont(f)
        self.edit.setStyleSheet(f"QLineEdit{{background:rgba(0,0,0,70);color:{c.name()};border:1px dashed "
                                "rgba(255,255,255,170);border-radius:4px;padding:0 4px;"
                                "selection-background-color:#3a6df0}")
        self._grow_edit()

    def _grow_edit(self, *_):
        fm = QFontMetrics(self.edit.font())
        self.edit.resize(max(90, fm.horizontalAdvance(self.edit.text()) + 30), fm.height() + 6)

    def _start_text(self, pos):
        self._commit_text()
        self.edit.clear()
        self._style_edit()
        self.edit.move(max(0, pos.x() - 5), max(0, pos.y() - self.edit.height() // 2))
        self.edit.show()
        self.edit.raise_()
        self.edit.setFocus()

    def _commit_text(self):
        if not self.edit.isVisible():
            return
        s = self.edit.text().strip()
        if s:
            fm = QFontMetrics(self.edit.font())
            top = self.edit.y() + (self.edit.height() - fm.height()) / 2        # где реально стоит строка в поле
            self.items.append({"t": "text", "c": QColor(self.colors["text"]), "px": TEXT_SIZES[self.size_idx["text"]],
                               "pos": QPointF(self.edit.x() + 5, top), "s": s})
        self.edit.hide()
        self.edit.clear()
        self.setFocus()
        self.update()

    def _cancel_text(self):
        self.edit.hide()
        self.edit.clear()
        self.setFocus()

    # ───────── мышь ─────────
    def _update_cursor(self, pos):
        if self.mode == "select":
            self.setCursor(Qt.CursorShape.CrossCursor)
        elif self.sel.contains(pos):
            self.setCursor(Qt.CursorShape.IBeamCursor if self.tool == "text" else Qt.CursorShape.CrossCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)

    def mousePressEvent(self, ev):
        pos = ev.position().toPoint()
        if ev.button() == Qt.MouseButton.RightButton:
            if self.mode == "select":
                self.cancel()
            return
        if ev.button() != Qt.MouseButton.LeftButton:
            return
        if self.mode == "select":
            self.start = pos
            self.sel = QRect(pos, pos)
            self.update()
        elif self.sel.contains(pos):
            if self.tool == "pen":
                self._commit_text()
                self.cur = {"t": "pen", "c": QColor(self.colors["pen"]), "w": PEN_WIDTHS[self.size_idx["pen"]],
                            "pts": [QPointF(ev.position())]}
            else:
                self._start_text(pos)

    def mouseMoveEvent(self, ev):
        pos = ev.position().toPoint()
        if self.mode == "select":
            if ev.buttons() & Qt.MouseButton.LeftButton:
                self.sel = QRect(self.start, pos).normalized().intersected(self.rect_bounds())
                self.update()
        elif self.cur is not None:
            p = QPointF(ev.position())
            p.setX(max(self.sel.left(), min(self.sel.right(), p.x())))        # штрих не выходит за выделение
            p.setY(max(self.sel.top(), min(self.sel.bottom(), p.y())))
            last = self.cur["pts"][-1]
            if abs(p.x() - last.x()) + abs(p.y() - last.y()) >= 1.5:
                self.cur["pts"].append(p)
                self.update()
        self._update_cursor(pos)

    def mouseReleaseEvent(self, ev):
        if ev.button() != Qt.MouseButton.LeftButton:
            return
        if self.mode == "select":
            if self.sel.width() < 8 or self.sel.height() < 8:                 # случайный клик — выбираем заново
                self.sel = QRect()
                self.update()
                return
            self.mode = "edit"
            self._copy_raw()
            self._place_bar()
            self._update_cursor(ev.position().toPoint())
            self.update()
        elif self.cur is not None:
            self.items.append(self.cur)
            self.cur = None
            self.update()

    def rect_bounds(self):
        return QRect(0, 0, self.width(), self.height())

    # ───────── клавиатура ─────────
    def keyPressEvent(self, ev):
        k, ctrl = ev.key(), bool(ev.modifiers() & Qt.KeyboardModifier.ControlModifier)
        if k == Qt.Key.Key_Escape:
            self.cancel()
        elif k in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.mode == "edit":
            self.finish()
        elif k == Qt.Key.Key_Z and ctrl:
            self.undo()
        elif k == Qt.Key.Key_S and ctrl and self.mode == "edit":
            self.finish(save=True)
        else:
            super().keyPressEvent(ev)

    # ───────── результат ─────────
    def _crop_raw(self):
        d, r = self.dpr, self.sel
        return self.shot.copy(QRect(round(r.x() * d), round(r.y() * d), round(r.width() * d), round(r.height() * d)))

    def _copy_raw(self):
        """Сразу после выделения снимок уже лежит в буфере обмена."""
        QGuiApplication.clipboard().setPixmap(self._crop_raw())
        self.toast = "скопировано в буфер"
        QTimer.singleShot(2200, self._clear_toast)

    def _clear_toast(self):
        self.toast = ""
        self.update()

    def result(self):
        """Снимок выделенной области вместе с пометками, в полном разрешении экрана."""
        d, r = self.dpr, self.sel
        img = QImage(round(r.width() * d), round(r.height() * d), QImage.Format.Format_ARGB32_Premultiplied)
        img.setDevicePixelRatio(d)
        p = QPainter(img)
        p.drawPixmap(QPointF(0, 0), self.shot, QRectF(r.x() * d, r.y() * d, r.width() * d, r.height() * d))
        p.translate(-r.x(), -r.y())
        draw_items(p, self.items)
        p.end()
        return QPixmap.fromImage(img)

    def finish(self, save=False):
        if self._done or self.mode != "edit":
            return
        self._commit_text()
        self._done = True
        pm = self.result()
        QGuiApplication.clipboard().setPixmap(pm)
        path = ""
        if save:
            try:
                folder = C.media_dir(self.get_cfg(), "shot")
                folder.mkdir(parents=True, exist_ok=True)
                path = str(folder / f"Ножницы {time.strftime('%Y-%m-%d_%H-%M-%S')}.png")
                if not pm.save(path, "PNG"):
                    raise OSError("не удалось записать файл")
            except Exception as e:
                path = ""
                self._done = False
                self.toast = f"не сохранилось: {e}"
                self.update()
                return
        self.finished.emit("save" if save else "copy", path)
        self.close()

    def cancel(self):
        if self._done:
            return
        self._done = True
        self.finished.emit("cancel", "")
        self.close()

    # ───────── рисование ─────────
    def paintEvent(self, _):
        p = QPainter(self)
        p.drawPixmap(0, 0, self.dim)
        if self.mode == "select" and self.sel.isNull():                        # подсказка, пока ничего не выделено
            self._pill(p, "Выдели область экрана  ·  Esc или правая кнопка — отмена", self.width() // 2, 46, center=True)
        r = self.sel
        if r.isNull() or r.width() < 1 or r.height() < 1:
            return
        d = self.dpr
        p.drawPixmap(r.topLeft(), self.shot, QRect(round(r.x() * d), round(r.y() * d),
                                                   round(r.width() * d), round(r.height() * d)))
        p.save()
        p.setClipRect(r)
        draw_items(p, self.items + ([self.cur] if self.cur else []))
        p.restore()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(ACCENT), 1.6))
        p.drawRect(QRectF(r).adjusted(-0.5, -0.5, 0.5, 0.5))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#ffffff"))
        for pt in (r.topLeft(), r.topRight(), r.bottomLeft(), r.bottomRight()):
            p.drawEllipse(QPointF(pt), 3.5, 3.5)
        label = f"{round(r.width() * d)} × {round(r.height() * d)}"
        if self.toast:
            label += f"  ·  {self.toast}"
        self._pill(p, label, r.left(), max(14, r.top() - 18))

    def _pill(self, p, text, x, cy, center=False):
        f = QFont("Segoe UI")
        f.setPixelSize(13)
        f.setBold(True)
        p.setFont(f)
        w = QFontMetrics(f).horizontalAdvance(text) + 22
        left = x - w / 2 if center else min(x, self.width() - w - 6)
        box = QRectF(left, cy - 13, w, 26)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(20, 20, 22, 235))
        p.drawRoundedRect(box, 13, 13)
        p.setPen(QColor("#ffffff"))
        p.drawText(box, Qt.AlignmentFlag.AlignCenter, text)

    def showEvent(self, ev):
        super().showEvent(ev)
        self.raise_()
        self.activateWindow()
        self.setFocus()
        wh = self.windowHandle()
        if wh is not None:
            wh.requestActivate()


def start_snip(get_cfg, on_finished):
    """Открывает «Ножницы». on_finished(kind, path) вызывается один раз, когда всё закончилось."""
    ov = SnipOverlay(get_cfg)
    ov.finished.connect(on_finished)
    ov.show()
    return ov
