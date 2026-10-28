"""Planner: tasks, reminders, alarms, timers, stopwatch + Russian time phrase parsing."""
import datetime as dt
import re
import threading
import time
import uuid

from .config import JsonStore

# ------------------------------------------------------------------ number words
UNITS = {"ноль": 0, "один": 1, "одну": 1, "одна": 1, "одной": 1, "два": 2, "две": 2, "три": 3, "четыре": 4, "пять": 5,
         "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10, "одиннадцать": 11, "двенадцать": 12,
         "тринадцать": 13, "четырнадцать": 14, "пятнадцать": 15, "шестнадцать": 16, "семнадцать": 17,
         "восемнадцать": 18, "девятнадцать": 19, "двадцать": 20, "тридцать": 30, "сорок": 40, "пятьдесят": 50,
         "шестьдесят": 60, "семьдесят": 70, "восемьдесят": 80, "девяносто": 90, "сто": 100,
         "двести": 200, "триста": 300, "четыреста": 400, "пятьсот": 500, "шестьсот": 600, "семьсот": 700,
         "восемьсот": 800, "девятьсот": 900}
# oblique cases: "в трёх милях", "из пяти", "около двухсот" were not numbers at all
for _forms, _v in (("одного одному одним одном", 1), ("двух двум двумя", 2), ("трех трем тремя", 3),
                   ("четырех четырем четырьмя", 4), ("пяти пятью", 5), ("шести шестью", 6), ("семи семью", 7),
                   ("восьми восемью", 8), ("девяти девятью", 9), ("десяти десятью", 10), ("одиннадцати", 11),
                   ("двенадцати", 12), ("тринадцати", 13), ("четырнадцати", 14), ("пятнадцати", 15),
                   ("шестнадцати", 16), ("семнадцати", 17), ("восемнадцати", 18), ("девятнадцати", 19),
                   ("двадцати двадцатью", 20), ("тридцати тридцатью", 30), ("сорока", 40), ("пятидесяти", 50),
                   ("шестидесяти", 60), ("семидесяти", 70), ("восьмидесяти", 80), ("девяноста", 90), ("ста", 100),
                   ("двухсот", 200), ("трехсот", 300), ("четырехсот", 400), ("пятисот", 500), ("шестисот", 600),
                   ("семисот", 700), ("восьмисот", 800), ("девятисот", 900)):
    for _f in _forms.split():
        UNITS.setdefault(_f, _v)


def words_to_digits(text):
    """'двадцать пять минут' -> '25 минут', 'в трёх милях' -> 'в 3 милях', 'сто двадцать пять' -> '125',
    'две тысячи двадцать шесть' -> '2026'."""
    toks = text.split()
    out, acc, have = [], 0, False
    for t in toks:
        w = t.strip(",.").lower().replace("ё", "е")
        if w in UNITS:
            v = UNITS[w]
            if have and ((acc >= 1000 and acc % 1000 == 0 and v < 1000) or (acc >= 100 and acc % 100 == 0 and v < 100)
                         or (acc >= 20 and acc % 10 == 0 and v < 10)):
                acc += v
            else:
                if have:
                    out.append(str(acc))
                acc, have = v, True
        elif re.fullmatch(r"тысяч\w*", w):
            acc, have = (acc if have else 1) * 1000, True
        else:
            if have:
                out.append(str(acc))
                acc, have = 0, False
            out.append(t)
    if have:
        out.append(str(acc))
    s = " ".join(out)
    s = re.sub(r"\bполчаса\b", "30 минут", s)
    s = re.sub(r"\bполтора часа\b", "90 минут", s)
    s = re.sub(r"\bполторы минуты\b", "90 секунд", s)
    s = re.sub(r"\bчетверть часа\b", "15 минут", s)
    s = re.sub(r"\b(час|минуту|минутку|секунду)\b(?<!\d )", lambda m: "1 " + m.group(1), s) if re.search(r"(?<!\d )\b(через|на) (час|минуту|минутку|секунду)\b", s) else s
    return s


DUR_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(сек\w*|с\b|мин\w*|м\b|час\w*|ч\b|д(?:ень|ня|ней)\b|сут\w*)")


def parse_duration(text):
    """Returns seconds or None. '1 час 30 минут', '5 мин', 'полчаса'."""
    text = words_to_digits(text.lower())
    total, found = 0.0, False
    for m in DUR_RE.finditer(text):
        v = float(m.group(1).replace(",", "."))
        u = m.group(2)
        if u.startswith("с") and not u.startswith("сут"):
            total += v
        elif u.startswith("м"):
            total += v * 60
        elif u.startswith("ч"):
            total += v * 3600
        else:
            total += v * 86400
        found = True
    return int(total) if found else None


WEEKDAYS = {"понедельник": 0, "вторник": 1, "сред": 2, "четверг": 3, "пятниц": 4, "суббот": 5, "воскресень": 6}

TIME_PATTERNS = [
    r"через\s+(?:\d+(?:[.,]\d+)?\s*(?:сек\w*|мин\w*|час\w*|д(?:ень|ня|ней)|сут\w*)\s*(?:и\s*)?)+",
    r"(?:сегодня|завтра|послезавтра)?\s*(?:в|во|на)?\s*(?:понедельник|вторник|среду|четверг|пятницу|субботу|воскресенье)?\s*(?:в|на|к)\s+\d{1,2}(?:[:. ]\d{2})?(?:\s*(?:час\w*))?(?:\s*\d{1,2}\s*минут\w*)?(?:\s*(?:утра|дня|вечера|ночи))?",
    r"(?:сегодня|завтра|послезавтра)(?:\s+(?:утром|днём|днем|вечером|ночью))?",
    r"(?:в|во)\s+(?:понедельник|вторник|среду|четверг|пятницу|субботу|воскресенье)",
    r"\d{1,2}[./]\d{1,2}(?:[./]\d{2,4})?",
]


def parse_when(text, now=None):
    """Find a time expression in text. Returns (datetime|None, text_without_time_phrase)."""
    now = now or dt.datetime.now()
    src = words_to_digits(text)
    low = src.lower()
    when = None
    spans = []
    # relative: через ...
    m = re.search(TIME_PATTERNS[0], low)
    if m:
        secs = parse_duration(m.group(0))
        if secs:
            when = now + dt.timedelta(seconds=secs)
            spans.append(m.span())
    if when is None:
        day = None
        m = re.search(r"\b(сегодня|завтра|послезавтра)\b", low)
        if m:
            day = now.date() + dt.timedelta(days={"сегодня": 0, "завтра": 1, "послезавтра": 2}[m.group(1)])
            spans.append(m.span())
        for wd, idx in WEEKDAYS.items():
            m = re.search(r"\b(?:в|во)?\s*" + wd + r"\w*", low)
            if m:
                delta = (idx - now.weekday()) % 7 or 7
                day = now.date() + dt.timedelta(days=delta)
                spans.append(m.span())
                break
        m = re.search(r"\b\d{1,2}[./]\d{1,2}(?:[./](\d{2,4}))?\b", low)
        if m and not re.search(r"\d{1,2}:\d{2}", m.group(0)):
            parts = re.split(r"[./]", m.group(0))
            try:
                y = int(parts[2]) if len(parts) > 2 else now.year
                y = y + 2000 if y < 100 else y
                day = dt.date(y, int(parts[1]), int(parts[0]))
                spans.append(m.span())
            except Exception:
                pass
        hour = minute = None
        m = re.search(r"\b(?:в|на|к)\s+(\d{1,2})(?:[:. ](\d{2}))?(?:\s*час\w*)?(?:\s*(\d{1,2})\s*минут\w*)?(?:\s*(утра|дня|вечера|ночи))?", low)
        if m:
            hour = int(m.group(1))
            minute = int(m.group(2) or m.group(3) or 0)
            part = m.group(4)
            if part in ("дня", "вечера") and hour < 12:
                hour += 12
            if part == "ночи" and hour == 12:
                hour = 0
            if hour <= 23 and minute <= 59:
                spans.append(m.span())
            else:
                hour = None
        else:
            m = re.search(r"\b(утром|днём|днем|вечером|ночью)\b", low)
            if m:
                hour = {"утром": 9, "днём": 13, "днем": 13, "вечером": 19, "ночью": 23}[m.group(1)]
                minute = 0
                spans.append(m.span())
        if hour is not None:
            d = day or now.date()
            when = dt.datetime.combine(d, dt.time(hour, minute))
            if when <= now and day is None:
                # "в 7" said at 9 in the evening -> tomorrow (or +12h if that makes sense)
                if hour < 12 and when + dt.timedelta(hours=12) > now and not m.group(4 if m and m.lastindex and m.lastindex >= 4 else 0):
                    when += dt.timedelta(hours=12)
                else:
                    when += dt.timedelta(days=1)
        elif day is not None:
            when = dt.datetime.combine(day, dt.time(9, 0))
    rest = src
    for a, b in sorted(spans, reverse=True):
        rest = rest[:a] + " " + rest[b:]
    rest = re.sub(r"\s+", " ", rest).strip(" ,.")
    return when, rest


def plural(n, one, few, many):
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def say_duration(secs):
    secs = int(secs)
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    parts = []
    if h:
        parts.append(f"{h} {plural(h, 'час', 'часа', 'часов')}")
    if m:
        parts.append(f"{m} {plural(m, 'минуту', 'минуты', 'минут')}")
    if s and not h:
        parts.append(f"{s} {plural(s, 'секунду', 'секунды', 'секунд')}")
    return " ".join(parts) or "0 секунд"


MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]
WD_NAMES = ["понедельник", "вторник", "среду", "четверг", "пятницу", "субботу", "воскресенье"]


def say_when(when, now=None):
    now = now or dt.datetime.now()
    d = (when.date() - now.date()).days
    t = f"{when:%H:%M}"
    if d == 0:
        return f"сегодня в {t}"
    if d == 1:
        return f"завтра в {t}"
    if d == 2:
        return f"послезавтра в {t}"
    if 0 < d < 7:
        return f"в {WD_NAMES[when.weekday()]} в {t}"
    return f"{when.day} {MONTHS[when.month - 1]} в {t}"


# ------------------------------------------------------------------ planner
class Planner:
    def __init__(self, on_fire, on_change=None):
        """on_fire(kind, item): called from scheduler thread when timer/reminder/alarm triggers."""
        self.tasks = JsonStore("tasks")
        self.reminders = JsonStore("reminders")
        self.timers = JsonStore("timers")
        self.on_fire = on_fire
        self.on_change = on_change or (lambda what: None)
        self.stopwatch = {"start": None, "acc": 0.0, "laps": []}
        self.lock = threading.RLock()
        # completed tasks used to pile up forever in "Выполнено": keep them two weeks
        try:
            self.clear_done(older_days=14)
        except Exception:
            pass
        # drop timers that expired while Jarvis was off (fire them once)
        threading.Thread(target=self._loop, daemon=True, name="planner").start()

    # ---------- tasks
    def add_task(self, text, due=None, priority=2, tags=None, notes=""):
        with self.lock:
            t = {"id": uuid.uuid4().hex[:8], "text": text.strip(), "due": due.isoformat(timespec="minutes") if isinstance(due, dt.datetime) else due,
                 "priority": priority, "done": False, "created": dt.datetime.now().isoformat(timespec="seconds"),
                 "tags": tags or [], "notes": notes, "notified": False}
            self.tasks.items.append(t)
            self.tasks.save()
        self.on_change("tasks")
        return t

    def update_task(self, tid, **kw):
        with self.lock:
            for t in self.tasks.items:
                if t["id"] == tid:
                    if "due" in kw and kw["due"] != t.get("due"):
                        t["notified"] = False
                    t.update(kw)
                    if kw.get("done"):
                        t["done_at"] = dt.datetime.now().isoformat(timespec="seconds")
                    elif "done" in kw:
                        t.pop("done_at", None)
                    self.tasks.save()
                    self.on_change("tasks")
                    return t
        return None

    def delete_task(self, tid):
        """Returns the removed task, so the UI can offer "Отменить"."""
        with self.lock:
            gone = next((t for t in self.tasks.items if t["id"] == tid), None)
            self.tasks.items = [t for t in self.tasks.items if t["id"] != tid]
            self.tasks.save()
        self.on_change("tasks")
        return gone

    def restore_task(self, task):
        with self.lock:
            if isinstance(task, dict) and task.get("id") and not any(t["id"] == task["id"] for t in self.tasks.items):
                self.tasks.items.append(task)
                self.tasks.save()
        self.on_change("tasks")
        return task

    def clear_done(self, older_days=0):
        """Remove completed tasks (all, or only those finished more than older_days ago)."""
        cut = (dt.datetime.now() - dt.timedelta(days=older_days)).isoformat(timespec="seconds")
        with self.lock:
            n0 = len(self.tasks.items)
            self.tasks.items = [t for t in self.tasks.items
                                if not t["done"] or (older_days and (t.get("done_at") or t.get("created") or "") > cut)]
            n = n0 - len(self.tasks.items)
            if n:
                self.tasks.save()
        if n:
            self.on_change("tasks")
        return n

    def find_task(self, query):
        from rapidfuzz import fuzz, process
        open_tasks = [t for t in self.tasks.items if not t["done"]]
        m = re.match(r"^\s*(?:номер\s*)?(\d+)\s*$", words_to_digits(query))
        if m:
            i = int(m.group(1)) - 1
            lst = self.sorted_tasks(open_only=True)
            return lst[i] if 0 <= i < len(lst) else None
        if not open_tasks:
            return None
        r = process.extractOne(query.lower(), [t["text"].lower() for t in open_tasks], scorer=fuzz.WRatio, score_cutoff=60)
        return open_tasks[r[2]] if r else None

    def sorted_tasks(self, open_only=False):
        items = [t for t in self.tasks.items if not (open_only and t["done"])]
        return sorted(items, key=lambda t: (t["done"], t.get("due") or "9999", -int(t.get("priority", 2))))

    def tasks_for_day(self, day=None):
        day = (day or dt.date.today()).isoformat()
        return [t for t in self.sorted_tasks(True) if (t.get("due") or "").startswith(day)]

    # ---------- reminders / alarms
    def add_reminder(self, text, when, repeat=None, alarm=False, command=None):
        """command: a voice command to run at that time instead of speaking a reminder."""
        with self.lock:
            r = {"id": uuid.uuid4().hex[:8], "text": text.strip() or ("Будильник" if alarm else "Напоминание"),
                 "at": when.isoformat(timespec="seconds"), "repeat": repeat, "alarm": alarm, "active": True}
            if command:
                r["command"] = command
            self.reminders.items.append(r)
            self.reminders.save()
        self.on_change("reminders")
        return r

    def update_reminder(self, rid, **kw):
        with self.lock:
            for r in self.reminders.items:
                if r["id"] == rid:
                    r.update(kw)
            self.reminders.save()
        self.on_change("reminders")

    def delete_reminder(self, rid):
        with self.lock:
            self.reminders.items = [r for r in self.reminders.items if r["id"] != rid]
            self.reminders.save()
        self.on_change("reminders")

    def find_reminders(self, query="", alarm=None):
        """Reminders matching a spoken description: "про чайник", "на 7:30", "на завтра", "" = all.
        alarm: True only alarms, False only reminders, None both."""
        from rapidfuzz import fuzz
        items = [r for r in self.reminders.items if alarm is None or bool(r.get("alarm")) == alarm]
        q = re.sub(r"^(?:про|о|об|на|в|который|которое|что)\s+", "", (query or "").strip().lower())
        if not q or q in ("все", "всё", "всех"):
            return items
        m = re.search(r"(\d{1,2})[:. ](\d{2})|\b(\d{1,2})\s*(?:час|утра|вечера|дня|ночи)", words_to_digits(q))
        if m:
            h = int(m.group(1) or m.group(3))
            mi = int(m.group(2) or 0)
            if re.search(r"вечера|дня", q) and h < 12:
                h += 12
            hit = [r for r in items if dt.datetime.fromisoformat(r["at"]).hour % 24 == h % 24
                   and (m.group(2) is None or dt.datetime.fromisoformat(r["at"]).minute == mi)]
            if hit:
                return hit
        if "завтра" in q:
            day = (dt.date.today() + dt.timedelta(days=1)).isoformat()
            return [r for r in items if r["at"].startswith(day)]
        if "сегодня" in q:
            return [r for r in items if r["at"].startswith(dt.date.today().isoformat())]
        scored = sorted(((fuzz.partial_ratio(q, r["text"].lower()), r) for r in items), key=lambda x: -x[0])
        return [r for s, r in scored if s >= 70][:1]

    def find_timers(self, query=""):
        """Timers matching "на 5 минут", "чай", "" = all."""
        from rapidfuzz import fuzz
        q = (query or "").strip().lower()
        if not q or q in ("все", "всё"):
            return list(self.timers.items)
        secs = parse_duration(q)
        if secs:
            hit = [t for t in self.timers.items if abs(t["duration"] - secs) < 2]
            if hit:
                return hit
        scored = sorted(((fuzz.partial_ratio(q, (t.get("label") or "").lower()), t) for t in self.timers.items),
                        key=lambda x: -x[0])
        return [t for s, t in scored if s >= 70][:1]

    # ---------- timers
    def add_timer(self, seconds, label=""):
        with self.lock:
            t = {"id": uuid.uuid4().hex[:8], "label": label, "duration": int(seconds),
                 "end": time.time() + seconds, "paused_left": None}
            self.timers.items.append(t)
            self.timers.save()
        self.on_change("timers")
        return t

    def cancel_timer(self, tid=None):
        with self.lock:
            if tid is None:
                n = len(self.timers.items)
                self.timers.items = []
            else:
                n0 = len(self.timers.items)
                self.timers.items = [t for t in self.timers.items if t["id"] != tid]
                n = n0 - len(self.timers.items)
            self.timers.save()
        self.on_change("timers")
        return n

    def pause_timer(self, tid, pause=True):
        with self.lock:
            for t in self.timers.items:
                if t["id"] == tid:
                    if pause and t["paused_left"] is None:
                        t["paused_left"] = max(0, t["end"] - time.time())
                    elif not pause and t["paused_left"] is not None:
                        t["end"] = time.time() + t["paused_left"]
                        t["paused_left"] = None
            self.timers.save()
        self.on_change("timers")

    def timer_left(self, t):
        return t["paused_left"] if t["paused_left"] is not None else max(0, t["end"] - time.time())

    # ---------- stopwatch
    def sw_start(self):
        if self.stopwatch["start"] is None:
            self.stopwatch["start"] = time.time()
        self.on_change("stopwatch")

    def sw_stop(self):
        s = self.stopwatch
        if s["start"] is not None:
            s["acc"] += time.time() - s["start"]
            s["start"] = None
        self.on_change("stopwatch")
        return s["acc"]

    def sw_reset(self):
        self.stopwatch = {"start": None, "acc": 0.0, "laps": []}
        self.on_change("stopwatch")

    def sw_lap(self):
        self.stopwatch["laps"].append(self.sw_value())
        self.on_change("stopwatch")

    def sw_value(self):
        s = self.stopwatch
        return s["acc"] + (time.time() - s["start"] if s["start"] else 0)

    # ---------- scheduler
    def _loop(self):
        while True:
            time.sleep(0.5)
            now = dt.datetime.now()
            try:
                self._tick(now)
            except Exception as e:
                print("[planner]", e)

    def _tick(self, now):
        fired = []
        with self.lock:
            for t in list(self.timers.items):
                if t["paused_left"] is None and t["end"] <= time.time():
                    self.timers.items.remove(t)
                    fired.append(("timer", t))
            if fired:
                self.timers.save()
            for r in self.reminders.items:
                if not r.get("active"):
                    continue
                at = dt.datetime.fromisoformat(r["at"])
                if at <= now:
                    fired.append(("alarm" if r.get("alarm") else "reminder", dict(r)))
                    rep = r.get("repeat")
                    if rep == "daily":
                        nxt = at + dt.timedelta(days=1)
                    elif rep == "weekly":
                        nxt = at + dt.timedelta(days=7)
                    elif rep == "weekdays":
                        nxt = at + dt.timedelta(days=1)
                        while nxt.weekday() >= 5:
                            nxt += dt.timedelta(days=1)
                    elif rep == "hourly":
                        nxt = at + dt.timedelta(hours=1)
                    else:
                        nxt = None
                    if nxt:
                        while nxt <= now or (rep == "weekdays" and nxt.weekday() >= 5):
                            # catching up after the PC was off: a weekday alarm must not land on a weekend
                            # (and a weekly one stays on its weekday: it used to drift by a day per missed week)
                            nxt += {"hourly": dt.timedelta(hours=1), "weekly": dt.timedelta(days=7)}.get(rep, dt.timedelta(days=1))
                        r["at"] = nxt.isoformat(timespec="seconds")
                    else:
                        r["active"] = False
            # a one-off reminder that has fired is done: drop it, so the list never fills with dead entries
            # (Core keeps the fired copy for "отложи на 10 минут")
            self.reminders.items = [r for r in self.reminders.items if r.get("active") or r.get("repeat")]
            if any(k in ("reminder", "alarm") for k, _ in fired):
                self.reminders.save()
            for t in self.tasks.items:
                if t["done"] or t.get("notified") or not t.get("due") or len(t["due"]) < 16:
                    continue
                if dt.datetime.fromisoformat(t["due"]) <= now:
                    t["notified"] = True
                    fired.append(("task", dict(t)))
                    self.tasks.save()
        for kind, item in fired:
            self.on_change({"timer": "timers", "task": "tasks"}.get(kind, "reminders"))
            try:
                self.on_fire(kind, item)
            except Exception as e:
                print("[planner fire]", e)
