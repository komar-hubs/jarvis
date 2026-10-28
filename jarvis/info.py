"""Information skills: weather (Open-Meteo, no key), currency (CBR), crypto, IP, jokes."""
import random

import requests

WMO = {0: "ясно", 1: "в основном ясно", 2: "переменная облачность", 3: "пасмурно", 45: "туман", 48: "изморозь",
       51: "лёгкая морось", 53: "морось", 55: "сильная морось", 61: "небольшой дождь", 63: "дождь", 65: "ливень",
       66: "ледяной дождь", 67: "сильный ледяной дождь", 71: "небольшой снег", 73: "снег", 75: "сильный снег",
       77: "снежная крупа", 80: "кратковременный дождь", 81: "ливневый дождь", 82: "сильный ливень",
       85: "снегопад", 86: "сильный снегопад", 95: "гроза", 96: "гроза с градом", 99: "сильная гроза с градом"}


def _case_variants(city):
    """'в Ейске' -> Ейск, 'в Москве' -> Москва, 'в Казани' -> Казань, 'в Питере' -> Питер."""
    c = city.strip().lower()
    out = [c]
    if c.endswith("е"):
        out += [c[:-1], c[:-1] + "а", c[:-1] + "я"]
    if c.endswith("и"):
        out += [c[:-1] + "ь", c[:-1] + "я", c[:-1]]
    if c.endswith("у"):
        out += [c[:-1] + "а", c[:-1]]
    if c.endswith(("ом", "ем")):
        out += [c[:-2]]
    return list(dict.fromkeys(x for x in out if len(x) >= 2))


def geocode(city):
    for name in _case_variants(city):
        r = requests.get("https://geocoding-api.open-meteo.com/v1/search",
                         params={"name": name, "count": 1, "language": "ru"}, timeout=8)
        res = (r.json().get("results") or [None])[0]
        if res:
            return {"city": res["name"], "lat": res["latitude"], "lon": res["longitude"]}
    return None


def weather(lat, lon):
    r = requests.get("https://api.open-meteo.com/v1/forecast", params={
        "latitude": lat, "longitude": lon, "timezone": "auto", "forecast_days": 7,
        "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m",
        "hourly": "precipitation_probability",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code,sunrise,sunset,"
                 "uv_index_max"}, timeout=8)
    d = r.json()
    c, dl = d["current"], d["daily"]
    # first hour from now with a real chance of rain (for "дождь около 15:00")
    rain_at = None
    try:
        now = c["time"][:13]
        for t, p in zip(d["hourly"]["time"], d["hourly"]["precipitation_probability"]):
            if t[:13] >= now and (p or 0) >= 55:
                rain_at = t[11:16]
                break
            if t[:10] > now[:10] and t[11:13] >= "06":
                break
    except Exception:
        pass
    extra = {"rain_at": rain_at, "sunrise": (dl.get("sunrise") or [""])[0][11:16],
             "sunset": (dl.get("sunset") or [""])[0][11:16], "uv": (dl.get("uv_index_max") or [0])[0],
             # the week ahead: "погода на выходных", "в субботу"
             "days": [{"date": dl["time"][i], "min": round(dl["temperature_2m_min"][i]), "max": round(dl["temperature_2m_max"][i]),
                       "desc": WMO.get(dl["weather_code"][i], ""), "rain": dl["precipitation_probability_max"][i]}
                      for i in range(len(dl.get("time", [])))]}
    return {**extra,
        "temp": round(c["temperature_2m"]), "feels": round(c["apparent_temperature"]),
        "humidity": c["relative_humidity_2m"], "wind": round(c["wind_speed_10m"] / 3.6, 1),
        "desc": WMO.get(c["weather_code"], ""), "code": c["weather_code"],
        "max": round(dl["temperature_2m_max"][0]), "min": round(dl["temperature_2m_min"][0]),
        "rain": dl["precipitation_probability_max"][0],
        "tomorrow": {"max": round(dl["temperature_2m_max"][1]), "min": round(dl["temperature_2m_min"][1]),
                     "desc": WMO.get(dl["weather_code"][1], ""), "rain": dl["precipitation_probability_max"][1]},
    }


def _pct(n):
    from .planner import plural
    return plural(n or 0, "процент", "процента", "процентов")


def weather_phrase(city, w, tomorrow=False):
    if tomorrow:
        t = w["tomorrow"]
        return (f"Завтра в городе {city} {t['desc']}, от {t['min']} до {t['max']} градусов, "
                f"вероятность осадков {t['rain']} {_pct(t['rain'])}.")
    s = (f"В городе {city} сейчас {w['temp']} градусов, ощущается как {w['feels']}, {w['desc']}. "
         f"Ветер {w['wind']} метра в секунду. Днём до {w['max']}.")
    if (w.get("rain") or 0) >= 50:
        s += " Рекомендую взять зонт, сэр."
    elif w["temp"] <= -10:
        s += " Одевайтесь теплее."
    return s


def currency_trend():
    """{code: (rate, change since the previous Central Bank rate)} for USD, EUR, CNY."""
    d = requests.get("https://www.cbr-xml-daily.ru/daily_json.js", timeout=8).json()["Valute"]
    return {k: (round(d[k]["Value"] / d[k]["Nominal"], 2), round((d[k]["Value"] - d[k]["Previous"]) / d[k]["Nominal"], 2))
            for k in ("USD", "EUR", "CNY") if k in d}


def currency():
    d = requests.get("https://www.cbr-xml-daily.ru/daily_json.js", timeout=8).json()["Valute"]
    return {k: round(d[k]["Value"] / d[k]["Nominal"], 2) for k in ("USD", "EUR", "CNY", "GBP") if k in d}


def crypto():
    d = requests.get("https://api.coingecko.com/api/v3/simple/price",
                     params={"ids": "bitcoin,ethereum,toncoin", "vs_currencies": "usd"}, timeout=8).json()
    return {k: v["usd"] for k, v in d.items()}


def public_ip():
    return requests.get("https://api.ipify.org", timeout=6).text


JOKES = [
    "Я бы рассказал вам шутку про UDP, сэр, но не уверен, что она до вас дойдёт.",
    "Сэр, я проверил: в 97 процентах случаев проблема находится между креслом и клавиатурой. Остальные три — это я.",
    "Программист ставит на тумбочку два стакана: с водой — если захочет пить, и пустой — если не захочет.",
    "Мой уровень сарказма сегодня установлен на 75 процентов, сэр. Желаете повысить?",
    "Есть 10 типов людей: те, кто понимает двоичную систему, и те, кто нет.",
    "Сэр, я бы приготовил вам кофе, но у меня до сих пор нет рук. Я напоминаю об этом при каждом удобном случае.",
    "Оптимист говорит: стакан наполовину полон. Пессимист: наполовину пуст. Инженер: стакан в два раза больше, чем нужно.",
]

QUIPS_DONE = ["Готово, сэр.", "Сделано.", "Выполнено, сэр.", "Как пожелаете.", "Уже сделано.", "Исполнено."]
QUIPS_ACK = ["Да, сэр?", "Слушаю, сэр.", "К вашим услугам.", "Я здесь, сэр.", "Слушаю."]


def done():
    return random.choice(QUIPS_DONE)


def ack():
    return random.choice(QUIPS_ACK)


def joke():
    return random.choice(JOKES)
