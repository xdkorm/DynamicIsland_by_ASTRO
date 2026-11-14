"""Персонажи для острова: рисуются вручную, смотрят за курсором.
look — вектор взгляда от -1 до 1 по каждой оси, blink — моргание от 0 (открыты) до 1 (закрыты).
Каждый персонаж помещается примерно в 26×22 px вокруг точки (cx, cy)."""
import math
import random
import time

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainterPath, QPen


def _ell(p, x, y, rx, ry, color):
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(color if isinstance(color, QColor) else QColor(color))
    p.drawEllipse(QPointF(x, y), max(0.01, rx), max(0.01, ry))


def _poly(p, pts, color):
    path = QPainterPath()
    path.moveTo(*pts[0])
    for pt in pts[1:]:
        path.lineTo(*pt)
    path.closeSubpath()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color))
    p.drawPath(path)


# ───────── обычные ─────────
def _eyes(p, cx, cy, look, blink):
    """Два белых вертикальных овала, моргают в тонкую линию."""
    h = max(1.4, 11.5 * (1 - 0.88 * blink))
    w = 5.8
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#f5f5f7"))
    for dx in (-4.9, 4.9):
        x = cx + dx + look.x() * 2.4
        y = cy + look.y() * 1.6
        p.drawRoundedRect(QRectF(x - w / 2, y - h / 2, w, h), w / 2, min(w, h) / 2)


def _ghost(p, cx, cy, look, blink):
    path = QPainterPath()
    path.moveTo(cx - 10, cy + 10)
    path.lineTo(cx - 10, cy - 1)
    path.arcTo(QRectF(cx - 10, cy - 11, 20, 20), 180, -180)
    path.lineTo(cx + 10, cy + 10)
    xs = [cx + 10, cx + 3.3, cx - 3.3, cx - 10]
    for a, b in zip(xs[:-1], xs[1:]):
        path.quadTo((a + b) / 2, cy + 5.5, b, cy + 10)
    path.closeSubpath()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#f2f2f7"))
    p.drawPath(path)
    h = 2.9 * (1 - 0.9 * blink)
    for dx in (-3.6, 3.6):
        _ell(p, cx + dx + look.x() * 1.9, cy - 2 + look.y() * 1.4, 2.2, h, "#111111")


# ───────── Minecraft ─────────
def _creeper(p, cx, cy, look, blink):
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#5cb85c"))
    p.drawRect(QRectF(cx - 11, cy - 11, 22, 22))
    ox, oy = look.x() * 1.8, look.y() * 1.4
    p.setBrush(QColor("#0d2b0d"))
    eh = 4.6 * (1 - 0.9 * blink)
    p.drawRect(QRectF(cx - 7 + ox, cy - 6.5 + oy + (4.6 - eh) / 2, 4.6, eh))
    p.drawRect(QRectF(cx + 2.4 + ox, cy - 6.5 + oy + (4.6 - eh) / 2, 4.6, eh))
    p.drawRect(QRectF(cx - 2.3 + ox, cy - 2 + oy, 4.6, 5))
    p.drawRect(QRectF(cx - 4.6 + ox, cy + 2 + oy, 2.3, 5.5))
    p.drawRect(QRectF(cx + 2.3 + ox, cy + 2 + oy, 2.3, 5.5))


def _steve(p, cx, cy, look, blink):
    """Голова Стива 8×8 пикселей; зрачки смотрят влево/вправо."""
    s = 2.75
    ox, oy = cx - 4 * s, cy - 4 * s
    hair, skin, dark, mouth = "#3b2a1a", "#c68e6b", "#a56f4f", "#7a4a33"

    def cell(c, r, color, w=1):
        p.setBrush(QColor(color))
        p.drawRect(QRectF(ox + c * s, oy + r * s, s * w + 0.4, s + 0.4))

    p.setPen(Qt.PenStyle.NoPen)
    for r in range(8):
        for c in range(8):
            if r < 2 or (r == 2 and c in (0, 7)):
                col = hair
            else:
                col = skin
            cell(c, r, col)
    cell(3, 5, dark, 2)
    cell(2, 6, mouth, 4)
    if blink < 0.5:
        for white_c, inner in ((1, 1), (5, 0)):
            cell(white_c, 4, "#ffffff", 2)
            iris_c = white_c + (1 if look.x() > 0 else 0)
            cell(iris_c, 4, "#3a4ba8")
    else:
        cell(1, 4, dark, 2)
        cell(5, 4, dark, 2)


def _ender(p, cx, cy, look, blink):
    """Глаза Эндермена: фиолетовое свечение."""
    h = max(1.0, 3.4 * (1 - 0.85 * blink))
    for dx in (-5.4, 5.4):
        x = cx + dx + look.x() * 2.0
        y = cy + look.y() * 1.5
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(190, 80, 255, 70))
        p.drawRoundedRect(QRectF(x - 5.2, y - h / 2 - 1.6, 10.4, h + 3.2), 2, 2)
        p.setBrush(QColor("#c45cff"))
        p.drawRect(QRectF(x - 4.2, y - h / 2, 8.4, h))
        p.setBrush(QColor("#f3d9ff"))
        p.drawRect(QRectF(x - 1.2 + look.x() * 0.6, y - h / 2, 2.8, h))


# ───────── Ам Ням (Cut the Rope) ─────────
def _omnom(p, cx, cy, look, blink):
    dark, green = QColor("#2c5a0e"), QColor("#8ed21f")
    stem = QPainterPath()                                   # стебелёк на макушке
    stem.moveTo(cx - 0.6, cy - 6)
    stem.quadTo(cx - 1.4, cy - 10, cx + 2.4, cy - 11.2)
    for col, w in ((dark, 3.4), (green, 1.8)):
        p.setPen(QPen(col, w, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawPath(stem)
    p.setPen(QPen(dark, 1.1))                               # тело
    p.setBrush(green)
    p.drawRoundedRect(QRectF(cx - 11.2, cy - 6, 22.4, 17), 9, 7)
    mouth = QPainterPath()                                  # закрытый рот — тонкая дуга
    mouth.moveTo(cx - 8.4, cy + 5.2)
    mouth.quadTo(cx, cy + 8.0, cx + 8.4, cy + 5.2)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(dark, 1.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawPath(mouth)
    p.setPen(QPen(QColor("#111111"), 0.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.setBrush(QColor("#ffffff"))                           # белые зубки торчат вниз с тонкой чёрной обводкой
    for dx in (-3.8, 3.8):
        tp = QPainterPath()
        tp.moveTo(cx + dx - 2.0, cy + 6.1 + abs(dx) * 0.08)
        tp.lineTo(cx + dx + 2.0, cy + 6.1 + abs(dx) * 0.08)
        tp.lineTo(cx + dx, cy + 9.4)
        tp.closeSubpath()
        p.drawPath(tp)
    eh = max(0.6, 5.6 * (1 - 0.88 * blink))                 # огромные глаза
    for sgn in (-1, 1):
        ex, ey = cx + sgn * 4.9, cy - 1.8
        p.setPen(QPen(dark, 0.9))
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(QPointF(ex, ey), 5.2, eh)
        if blink < 0.75:
            px = cx + sgn * 2.7 + look.x() * 1.7
            py = ey + look.y() * 2.0
            _ell(p, px, py, 1.15, min(1.7, eh * 0.5), "#111111")


# ───────── Кирби ─────────
def _kirby(p, cx, cy, look, blink):
    for dx in (-6.2, 6.2):                       # ножки
        _ell(p, cx + dx, cy + 9.4, 4.2, 2.5, "#e5456f")
    for dx in (-10.2, 10.2):                     # ручки
        _ell(p, cx + dx, cy + 1.0, 2.8, 2.2, "#ff9ec4")
    _ell(p, cx, cy, 10.2, 9.8, "#ffb3d1")
    for dx in (-6.4, 6.4):                       # румянец
        _ell(p, cx + dx, cy + 3.6, 2.3, 1.4, "#ff7aa8")
    h = 3.5 * (1 - 0.9 * blink)
    for dx in (-3.5, 3.5):
        ex, ey = cx + dx + look.x() * 1.5, cy - 2.2 + look.y() * 1.2
        _ell(p, ex, ey, 1.9, h, "#14143c")
        if blink < 0.5:
            _ell(p, ex, ey + h * 0.45, 1.5, h * 0.4, "#3a5fd0")
            _ell(p, ex, ey - h * 0.4, 1.1, h * 0.32, "#ffffff")
    _ell(p, cx, cy + 4.6, 1.3, 0.9 + 0.6 * (1 - blink), "#c4284f")


# ───────── R.E.P.O ─────────
_DEF_REPO = "#6e7f6e"
_REPO = {"color": _DEF_REPO, "start": 0.0, "end": 0.0, "next": 0.0}   # цвет и «разговор»


def set_repo_color(hex_color):
    c = QColor(str(hex_color or ""))
    if not c.isValid() or c.name() == "#d9dde3":   # #d9dde3 — прежний цвет по умолчанию
        _REPO["color"] = _DEF_REPO
    else:
        _REPO["color"] = c.name()


def _repo_mouth(now):
    """0..1 — насколько открыт рот. Изредка робот «что-то говорит» пару секунд."""
    r = _REPO
    if r["next"] == 0.0:
        r["next"] = now + random.uniform(3, 8)
    if now >= r["next"] and now >= r["end"]:
        r["start"] = now
        r["end"] = now + random.uniform(1.0, 2.2)
        r["next"] = r["end"] + random.uniform(8, 22)
    if now < r["end"]:
        t = now - r["start"]
        return abs(math.sin(t * 10.5 + math.sin(t * 3.1))) * (0.55 + 0.45 * abs(math.sin(t * 2.2)))
    return 0.0


def _repo(p, cx, cy, look, blink):
    """Робот-игрок из R.E.P.O: купол головы с выпученными глазами. Когда «говорит» — весь купол
    приподнимается спереди, как крышка на шарнире сзади (слева)."""
    body = QColor(_REPO["color"])
    mo = _repo_mouth(time.time())
    ang = 15.0 * mo                       # угол подъёма купола, градусы
    seam = cy + 4.2                       # линия разреза
    hx = cx - 11                          # шарнир — левый нижний угол купола
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(body.darker(135))          # нижняя часть («шея») — неподвижна
    p.drawRect(QRectF(cx - 11, seam, 22, 4.6))
    if ang > 0.4:                         # тёмный клин внутри, пока крышка приоткрыта
        a = math.radians(ang)
        _poly(p, [(hx, seam), (cx + 11, seam), (hx + 22 * math.cos(a), seam - 22 * math.sin(a))], "#0d1210")
    p.save()
    p.translate(hx, seam)
    p.rotate(-ang)
    p.translate(-hx, -seam)
    dome = QPainterPath()
    dome.moveTo(cx - 11, seam)
    dome.lineTo(cx - 11, cy - 1)
    dome.arcTo(QRectF(cx - 11, cy - 12, 22, 22), 180, -180)
    dome.lineTo(cx + 11, seam)
    dome.closeSubpath()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(body)
    p.drawPath(dome)
    _ell(p, cx - 3, cy - 8.2, 5.5, 2.0, body.lighter(125))             # блик на куполе
    eh = max(0.7, 5.0 * (1 - 0.88 * blink))                            # глазки
    for sgn in (-1, 1):
        ex, ey = cx + sgn * 5.0, cy - 2.2
        _ell(p, ex, ey, 5.0, eh, "#f4f4f4")
        if blink < 0.7:
            _ell(p, ex + look.x() * 1.9, ey + look.y() * 1.5, 2.0, min(2.0, eh * 0.45), "#111111")
    p.restore()
    if ang <= 0.4:                        # когда закрыт — тонкий шов
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 150))
        p.drawRect(QRectF(cx - 11, seam - 0.4, 22, 0.9))


MASCOTS = [
    ("eye", "Глазки", _eyes),
    ("ghost", "Призрак", _ghost),
    ("creeper", "Крипер (Minecraft)", _creeper),
    ("steve", "Стив (Minecraft)", _steve),
    ("ender", "Эндермен (Minecraft)", _ender),
    ("omnom", "Ам Ням", _omnom),
    ("kirby", "Кирби", _kirby),
    ("repo", "Робот (R.E.P.O)", _repo),
]
_BY_KEY = {k: fn for k, _, fn in MASCOTS}


def draw_mascot(p, key, cx, cy, look, blink=0.0):
    fn = _BY_KEY.get(key, _eyes)  # неизвестный (старый) ключ из конфига → глазки
    p.save()
    fn(p, cx, cy, look, blink)
    p.restore()
