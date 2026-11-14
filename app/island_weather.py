"""Погода для острова. Источник — Open-Meteo (бесплатно, без ключа).
Город берётся из настроек; если пусто — определяется по IP. Обновление раз в 15 минут, в фоновом потоке."""
import json
import threading
import time
import urllib.parse
import urllib.request

from PySide6.QtCore import QObject, Signal

# WMO-код → (значок, описание)
WMO = {
    0: ("☀️", "Ясно"), 1: ("🌤", "Малооблачно"), 2: ("⛅", "Облачно"), 3: ("☁️", "Пасмурно"),
    45: ("🌫", "Туман"), 48: ("🌫", "Изморозь"),
    51: ("🌦", "Морось"), 53: ("🌦", "Морось"), 55: ("🌦", "Сильная морось"),
    56: ("🌧", "Ледяная морось"), 57: ("🌧", "Ледяная морось"),
    61: ("🌧", "Дождь"), 63: ("🌧", "Дождь"), 65: ("🌧", "Сильный дождь"),
    66: ("🌧", "Ледяной дождь"), 67: ("🌧", "Ледяной дождь"),
    71: ("🌨", "Снег"), 73: ("🌨", "Снег"), 75: ("❄️", "Сильный снег"), 77: ("🌨", "Снежная крупа"),
    80: ("🌦", "Ливень"), 81: ("🌧", "Ливень"), 82: ("⛈", "Сильный ливень"),
    85: ("🌨", "Снегопад"), 86: ("❄️", "Сильный снегопад"),
    95: ("⛈", "Гроза"), 96: ("⛈", "Гроза с градом"), 99: ("⛈", "Гроза с градом"),
}

REFRESH_SEC = 15 * 60
RETRY_SEC = 60


def _get_json(url, timeout=8):
    req = urllib.request.Request(url, headers={"User-Agent": "DynamicIslandRU/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def fmt_temp(t) -> str:
    v = round(t)
    if v == 0:
        return "0°"
    return f"{v:+d}°".replace("-", "−")


class WeatherService(QObject):
    updated = Signal()

    def __init__(self):
        super().__init__()
        self.data = None          # {"temp", "feels", "code", "is_day", "wind", "city"}
        self._key = None          # для какого города получены данные
        self._last_ok = 0.0
        self._last_try = 0.0
        self._busy = False
        self._loc_cache = {}      # город ("" = по IP) → (lat, lon, имя)

    # вызывается часто (из таймера часов) — внутри всё дёшево
    def poll(self, city: str):
        city = (city or "").strip()
        now = time.time()
        if self._busy:
            return
        if city != self._key:
            self.data = None
            self._last_ok = 0.0
            self._last_try = 0.0
        stale = now - self._last_ok > REFRESH_SEC
        if self.data is None or stale:
            if now - self._last_try < RETRY_SEC:
                return
            self._busy = True
            self._last_try = now
            threading.Thread(target=self._work, args=(city,), daemon=True).start()

    def _locate(self, city):
        if city in self._loc_cache:
            return self._loc_cache[city]
        loc = None
        if city:
            q = urllib.parse.urlencode({"name": city, "count": 1, "language": "ru", "format": "json"})
            res = _get_json("https://geocoding-api.open-meteo.com/v1/search?" + q).get("results") or []
            if res:
                loc = (res[0]["latitude"], res[0]["longitude"], res[0].get("name", city))
        else:
            try:
                d = _get_json("http://ip-api.com/json/?lang=ru&fields=status,city,lat,lon")
                if d.get("status") == "success":
                    loc = (d["lat"], d["lon"], d.get("city", ""))
            except Exception:
                d = _get_json("https://ipwho.is/")
                if d.get("success"):
                    loc = (d["latitude"], d["longitude"], d.get("city", ""))
        if loc:
            self._loc_cache[city] = loc
        return loc

    def _work(self, city):
        try:
            loc = self._locate(city)
            if not loc:
                return
            lat, lon, name = loc
            q = urllib.parse.urlencode({
                "latitude": lat, "longitude": lon, "timezone": "auto", "wind_speed_unit": "ms",
                "current": "temperature_2m,apparent_temperature,weather_code,is_day,wind_speed_10m",
            })
            cur = _get_json("https://api.open-meteo.com/v1/forecast?" + q)["current"]
            self.data = {
                "temp": cur["temperature_2m"], "feels": cur["apparent_temperature"],
                "code": int(cur["weather_code"]), "is_day": bool(cur.get("is_day", 1)),
                "wind": cur.get("wind_speed_10m", 0), "city": name,
            }
            self._key = city
            self._last_ok = time.time()
            self.updated.emit()
        except Exception as e:
            print("Погода не загрузилась:", e)
        finally:
            self._busy = False

    # готовые строки для интерфейса
    def icon(self) -> str:
        if not self.data:
            return "…"
        code = self.data["code"]
        ic = WMO.get(code, ("🌡", ""))[0]
        if code in (0, 1) and not self.data["is_day"]:
            return "🌙"
        return ic

    def temp_text(self) -> str:
        return fmt_temp(self.data["temp"]) if self.data else "—"

    def desc_text(self) -> str:
        if not self.data:
            return "Загружаю погоду…"
        d = self.data
        text = WMO.get(d["code"], ("", "Погода"))[1]
        return f"{text} · ощущается {fmt_temp(d['feels'])}"

    def place_text(self) -> str:
        return self.data["city"] if self.data else ""
