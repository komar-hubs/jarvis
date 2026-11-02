"""Steam by voice, from Steam's own files (no login, no key):

- «запусти последнюю игру» — the installed game with the latest LastPlayed (userdata/<id>/config/localconfig.vdf),
  started with steam://rungameid/<appid>;
- «сколько у меня часов в Spider-Man» — Steam's own playtime from the same file;
- «что из желаемого на скидке» — the wishlist (public profiles: IWishlistService, no key) and its discounts from the
  store (one batch request for prices, then a name for each discounted game, at most 6).
The account is the one Steam marks MostRecent in config/loginusers.vdf."""
import re
from pathlib import Path

import requests

from . import system as S

STEAMID_BASE = 76561197960265728
UA = {"User-Agent": "Mozilla/5.0 JARVIS"}


def vdf(text):
    """Valve KeyValues text -> nested dict (keys lower-cased)."""
    root, stack, key = {}, [], None
    cur = root
    for tok in re.findall(r'"((?:[^"\\]|\\.)*)"|([{}])', text):
        s, brace = tok
        if brace == "{":
            new = {}
            cur[(key or "").lower()] = new
            stack.append(cur)
            cur, key = new, None
        elif brace == "}":
            cur = stack.pop() if stack else root
            key = None
        elif key is None:
            key = s
        else:
            cur[key.lower()] = s.replace("\\\\", "\\")
            key = None
    return root


def steam_dir():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
            return Path(winreg.QueryValueEx(k, "SteamPath")[0])
    except OSError:
        p = Path(r"C:\Program Files (x86)\Steam")
        return p if p.exists() else None


def _read(p):
    try:
        return vdf(Path(p).read_text("utf-8", errors="replace"))
    except Exception:
        return {}


def account():
    """(steamid64, persona name) of the account Steam logged in last."""
    d = steam_dir()
    users = _read(d / "config" / "loginusers.vdf").get("users", {}) if d else {}
    best = None
    for sid, u in users.items():
        if not isinstance(u, dict):
            continue
        ts = int(u.get("timestamp", 0) or 0) + (10**12 if u.get("mostrecent") == "1" else 0)
        if not best or ts > best[0]:
            best = (ts, sid, u.get("personaname", ""))
    return (best[1], best[2]) if best else (None, "")


def installed():
    """{appid: name} of installed games, every library."""
    d = steam_dir()
    if not d:
        return {}
    libs = [d]
    for v in _read(d / "steamapps" / "libraryfolders.vdf").get("libraryfolders", {}).values():
        if isinstance(v, dict) and v.get("path"):
            libs.append(Path(v["path"]))
    out = {}
    for lib in dict.fromkeys(libs):
        for acf in (lib / "steamapps").glob("appmanifest_*.acf"):
            st = _read(acf).get("appstate", {})
            name = st.get("name", "")
            if name and not re.search(r"(?i)steamworks|redistributable|proton|runtime", name):
                out[st.get("appid", acf.stem.split("_")[-1])] = name
    return out


def played():
    """{appid: {"last": unix time, "minutes": playtime}} from the account's localconfig.vdf."""
    sid, _ = account()
    d = steam_dir()
    if not sid or not d:
        return {}
    acc = int(sid) - STEAMID_BASE
    cfg = _read(d / "userdata" / str(acc) / "config" / "localconfig.vdf")
    apps = (cfg.get("userlocalconfigstore", {}).get("software", {}).get("valve", {}).get("steam", {}).get("apps", {}))
    return {a: {"last": int(v.get("lastplayed", 0) or 0), "minutes": int(v.get("playtime", 0) or 0)}
            for a, v in apps.items() if isinstance(v, dict)}


def find_game(spoken):
    """The installed game a spoken name means ("спайдермен" -> Marvel's Spider-Man): (appid, name) or (None, "")."""
    from rapidfuzz import fuzz
    games = installed()
    q = re.sub(r"[^a-z0-9]+", "", S.translit(spoken.lower()))
    best, score = (None, ""), 0
    for a, name in games.items():
        n = re.sub(r"[^a-z0-9]+", "", name.lower())
        sc = max(fuzz.partial_ratio(q, n), fuzz.ratio(q, n))
        if sc > score:
            best, score = (a, name), sc
    return best if score >= 68 else (None, "")


def wishlist_sales(limit=6):
    """[(name, discount %, final price text)] of discounted wishlist games, best discounts first."""
    sid, _ = account()
    if not sid:
        raise RuntimeError("не нашёл аккаунт Steam на этом ПК")
    r = requests.get("https://api.steampowered.com/IWishlistService/GetWishlist/v1/", params={"steamid": sid},
                     headers=UA, timeout=15)
    items = (r.json().get("response") or {}).get("items") or [] if r.ok else []
    ids = [str(x["appid"]) for x in items][:150]
    if not ids:
        raise RuntimeError("список желаемого пуст или скрыт настройками приватности Steam")
    r = requests.get("https://store.steampowered.com/api/appdetails", params={"appids": ",".join(ids),
                     "filters": "price_overview", "cc": "ru", "l": "russian"}, headers=UA, timeout=20)
    data = r.json() if r.ok else {}
    sale = []
    for a in ids:
        p = ((data.get(a) or {}).get("data") or {})
        p = p.get("price_overview") if isinstance(p, dict) else None
        if p and p.get("discount_percent"):
            sale.append((a, p["discount_percent"], p.get("final_formatted", "")))
    sale.sort(key=lambda x: -x[1])
    out = []
    for a, pct, price in sale[:limit]:
        name = a
        try:
            j = requests.get("https://store.steampowered.com/api/appdetails", params={"appids": a, "filters": "basic",
                             "l": "russian"}, headers=UA, timeout=10).json()
            name = (j.get(a) or {}).get("data", {}).get("name", a)
        except Exception:
            pass
        out.append((name, pct, price))
    return out, len(sale)


def register(R, core):
    from .planner import plural

    @R(r"^(?:запусти|включи|открой|давай)\s+(?:мне\s+)?(?:последнюю|прошлую|недавнюю)\s+игру(?:\s+(?:в\s+)?(?:стим\w*|steam))?$")
    def _last(m, t):
        games, pl = installed(), played()
        cand = [(pl.get(a, {}).get("last", 0), a, n) for a, n in games.items()]
        cand = [c for c in cand if c[0]]
        if not cand:
            return "Не нашёл в Steam игр, в которые вы играли, сэр."
        _, a, name = max(cand)
        S.open_target(f"steam://rungameid/{a}")
        return f"Запускаю {name}."

    @R(r"^сколько\s+(?:у\s+меня\s+)?(?:часов|времени)\s+(?:я\s+)?(?:наиграл\w*\s+)?(?:в|во)\s+(.+?)(?:\s+(?:в\s+)?(?:стим\w*|steam))?$|"
       r"^сколько\s+я\s+(?:всего\s+)?(?:наиграл\w*|играл\w*\s+всего)\s+(?:в|во)\s+(.+?)$")
    def _hours(m, t):
        q = (m.group(1) or m.group(2)).strip()
        a, name = find_game(q)
        if not a:
            return False                     # not a Steam game: the usage statistics answer ("сколько я играл в …")
        mins = played().get(a, {}).get("minutes", 0)
        if not mins:
            return f"В {name} по данным Steam ещё не играли."
        h = mins // 60
        if not h:
            return f"В {name} у вас {mins} {plural(mins, 'минута', 'минуты', 'минут')} по данным Steam."
        return (f"В {name} у вас {h} {plural(h, 'час', 'часа', 'часов')}" + (f" {mins % 60} мин" if h < 10 and mins % 60 else "")
                + " по данным Steam.")

    @R(r"^(?:что|какие\s+игры)\s+(?:из\s+)?(?:моего\s+)?(?:списка\s+)?(?:желаемого|вишлиста|wishlist)\s+(?:сейчас\s+)?(?:на\s+скидке|подешевело|со\s+скидкой)$|"
       r"^(?:есть\s+)?скидки\s+(?:в|из|на)\s+(?:списке\s+)?(?:желаемого|вишлисте)(?:\s+в\s+стиме)?$")
    def _sales(m, t):
        try:
            sale, n = wishlist_sales()
        except Exception as e:
            return f"Не получилось посмотреть список желаемого: {e}."
        if not sale:
            return "Из списка желаемого сейчас ничего не на скидке, сэр."
        md = "**Скидки из списка желаемого Steam**\n\n" + "\n".join(f"- {name} — −{pct}%, {price}" for name, pct, price in sale)
        core._chat_add("assistant", md, "voice")
        top = sale[0]
        return (f"На скидке {n} {plural(n, 'игра', 'игры', 'игр')} из желаемого. Лучшая — {top[0]}, минус {top[1]} процентов. "
                f"Список в чате.")
