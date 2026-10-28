"""Journal of what Jarvis did, so it can be undone or corrected: "отмени последнее", "не котиков, а собачек",
"не на 5, а на 10 минут", "что ты сделал", "повтори последнее действие".

Every user request opens a group (Router.handle / Core.chat); actions inside it are recorded by thin wrappers
around system.* and Planner methods, so rule commands and AI tools are covered alike. Each record may carry an
undo function. Nested calls (change_volume -> set_volume) record only once."""
import datetime as dt
import re
import threading
import time
from collections import deque

from . import system as S


class Journal:
    KEEP = 60

    def __init__(self, core):
        self.core = core
        self.groups = deque(maxlen=self.KEEP)
        self._tls = threading.local()
        self._lock = threading.RLock()
        self._seq = 0
        self._instrument()

    # ================================================================== groups
    def begin(self, text, source="voice"):
        with self._lock:
            self._seq += 1
            g = {"id": self._seq, "text": (text or "").strip(), "source": source, "ts": time.time(), "items": [],
                 "undone": False}
            self.groups.append(g)
        self._tls.group = g
        return g

    def end(self, g=None):
        g = g or getattr(self._tls, "group", None)
        self._tls.group = None
        if g is not None and not g["items"]:
            with self._lock:
                try:
                    self.groups.remove(g)
                except ValueError:
                    pass
        elif g is not None:
            self.core.emit("journal")

    def current(self):
        return getattr(self._tls, "group", None)

    def record(self, desc, undo=None, kind="", **data):
        g = self.current()
        if g is None or getattr(self._tls, "replaying", False):
            return
        g["items"].append({"desc": desc, "undo": undo, "kind": kind, "data": data, "ts": time.time()})

    def last(self, max_age=900, undoable=False):
        now = time.time()
        with self._lock:
            for g in reversed(self.groups):
                if g["undone"] or not g["items"] or now - g["ts"] > max_age:
                    continue
                if g is self.current():
                    continue
                if undoable and not any(i["undo"] for i in g["items"]):
                    continue
                return g
        return None

    def undo(self, n=1, max_age=900):
        """Undo the last n requests. Returns a phrase for the user."""
        done, failed, nothing = [], 0, 0
        for _ in range(max(1, n)):
            g = self.last(max_age)
            if not g:
                break
            g["undone"] = True
            any_undo = False
            for it in reversed(g["items"]):
                if not it["undo"]:
                    continue
                any_undo = True
                try:
                    self._tls.replaying = True
                    it["undo"]()
                except Exception as e:
                    failed += 1
                    self.core.log(f"Отмена «{it['desc']}»: {e}", "warn")
                finally:
                    self._tls.replaying = False
            if any_undo:
                done.append(g)
            else:
                nothing += 1
        self.core.emit("journal")
        if not done:
            return "Это отменить нельзя, сэр." if nothing else "Отменять нечего, сэр."
        what = "; ".join(self.describe(g, short=True) for g in done)
        return ("Отменил: " + what + "." + (" Кое-что вернуть не вышло." if failed else "")).replace("..", ".")

    def describe(self, g, short=False):
        items = [i["desc"] for i in g["items"]]
        if short:
            return items[-1] if len(items) == 1 else f"{items[0]} и ещё {len(items) - 1}"
        return ", ".join(items)

    def recent(self, n=5):
        with self._lock:
            gs = [g for g in self.groups if g["items"] and not g["undone"] and g is not self.current()][-n:]
        return gs

    def snapshot(self, n=20):
        out = []
        with self._lock:
            for g in list(self.groups)[-n:]:
                if not g["items"]:
                    continue
                out.append({"id": g["id"], "text": g["text"], "at": dt.datetime.fromtimestamp(g["ts"]).strftime("%H:%M:%S"),
                            "items": [i["desc"] for i in g["items"]], "undone": g["undone"],
                            "undoable": any(i["undo"] for i in g["items"]) and not g["undone"]})
        return list(reversed(out))

    def undo_id(self, gid):
        with self._lock:
            g = next((x for x in self.groups if x["id"] == gid), None)
        if not g or g["undone"]:
            return "Уже отменено."
        g["undone"] = True
        for it in reversed(g["items"]):
            if it["undo"]:
                try:
                    self._tls.replaying = True
                    it["undo"]()
                except Exception as e:
                    self.core.log(f"Отмена «{it['desc']}»: {e}", "warn")
                finally:
                    self._tls.replaying = False
        self.core.emit("journal")
        return "Отменил: " + self.describe(g, short=True)

    # ================================================================== corrections
    def correct(self, old, new, max_age=300):
        """'не X, а Y': the last request with X replaced by Y (or with the changed part guessed when X is empty).
        Returns (corrected text, group) or (None, None)."""
        # the fix may be about an earlier step: "поставь таймер на 1 минуту и сделай громче" -> "не на 1, а на 2"
        now = time.time()
        with self._lock:
            cands = [g for g in reversed(self.groups) if g["items"] and g["text"] and not g["undone"]
                     and g is not self.current() and now - g["ts"] <= max_age][:4]
        if not cands:
            return None, None
        best = None
        for g in cands:  # newest first: an older request wins only with a clearly better match
            text = re.sub(r"^\W*(?:джарвис|jarvis)\w*\W*", "", g["text"], flags=re.I)
            if old:
                s, fixed = replace_fuzzy_scored(text, old, new)
            else:
                fixed = guess_replace(text, new, g)
                s = 70 if fixed else 0
            if not fixed or fixed.strip().lower() == text.strip().lower():
                continue
            if best is None or s > best[0] + 5:
                best = (s, fixed, g)
        if not best:
            return None, cands[0]
        return best[1], best[2]

    # ================================================================== instrumentation
    def _guarded(self, fn, before, after):
        """Wrap a function: before(*a, **kw) -> state, after(result, state, *a, **kw) records."""
        tls = self._tls

        def wrapper(*a, **kw):
            if getattr(tls, "depth", 0):
                return fn(*a, **kw)
            tls.depth = 1
            try:
                st = None
                try:
                    st = before(*a, **kw) if before else None
                except Exception:
                    pass
                res = fn(*a, **kw)
                try:
                    if self.current() is not None:
                        after(res, st, *a, **kw)
                except Exception as e:
                    self.core.log(f"Журнал: {e}", "warn")
                return res
            finally:
                tls.depth = 0
        wrapper.__wrapped__ = fn
        return wrapper

    def _instrument(self):
        core = self.core
        J = self

        # ---- browser pages: undo closes the tab (only while that browser is in front)
        def close_tab():
            if S.active_exe() in S.BROWSERS:
                S.hotkey("ctrl+w")
            else:
                raise RuntimeError("браузер уже не на переднем плане")

        def after_open(res, st, target, *a, **kw):
            t = str(target)
            if re.match(r"^(?:https?://|www\.)", t):
                core.ctx.note_url(t)
                host = re.sub(r"^https?://(?:www\.)?", "", t).split("/")[0]
                J.record(f"открыл {host}", close_tab, "open", url=t)
            elif t.startswith(("ms-settings:", "shell:")):
                J.record("открыл настройки" if t.startswith("ms-") else "открыл папку", None, "open", target=t)
            else:
                J.record(f"открыл {t.rsplit(chr(92), 1)[-1]}", None, "open", target=t)
        S.open_target = self._guarded(S.open_target, None, after_open)

        def after_search(res, st, query, *a, **kw):
            core.ctx.query = query
            core.ctx.site, core.ctx.site_at = "google", time.time()
            J.record(f"искал «{query}»", close_tab, "search", query=query)
        S.search_web = self._guarded(S.search_web, None, after_search)

        # ---- apps
        def after_launch(res, st, app, *a, **kw):
            ok, name = res if isinstance(res, tuple) else (res, app)
            if ok:
                core.ctx.app_at = time.time()
                J.record(f"запустил {name}", lambda: S.close_app(app, core.apps, core.cfg.get("aliases")), "app", app=app)
        S.launch_app = self._guarded(S.launch_app, None, after_launch)

        def after_close(res, st, app, *a, **kw):
            if res:
                J.record(f"закрыл {app}", lambda: S.launch_app(app, core.apps, core.cfg.get("aliases")), "close", app=app)
        S.close_app = self._guarded(S.close_app, None, after_close)

        # ---- sound
        def vol_before(*a, **kw):
            return S.get_volume()

        def after_vol(res, prev, *a, **kw):
            if prev is not None and res is not None and prev != res:
                J.record(f"громкость {prev} → {res}", lambda: S.set_volume(prev), "volume", prev=prev)
        S.set_volume = self._guarded(S.set_volume, vol_before, after_vol)
        S.change_volume = self._guarded(S.change_volume, vol_before, after_vol)

        def after_mute(res, st, on=True, *a, **kw):
            J.record("выключил звук" if on else "включил звук", lambda: S.mute(not on), "mute")
        S.mute = self._guarded(S.mute, None, after_mute)

        # ---- typing: undo erases exactly what was typed, if the same window is still in front
        def type_before(text, *a, **kw):
            import win32gui
            return win32gui.GetForegroundWindow()

        def after_type(res, hwnd, text, *a, **kw):
            n = len(text.replace("\r\n", "\n"))

            def erase():
                import win32gui
                if win32gui.GetForegroundWindow() != hwnd:
                    raise RuntimeError("окно уже другое")
                for _ in range(min(n, 3000)):
                    S.send_vks([0x08])
            short = text if len(text) <= 30 else text[:28] + "…"
            J.record(f"напечатал «{short}»", erase, "type", text=text)
        S.type_text = self._guarded(S.type_text, type_before, after_type)

        # ---- hotkeys that have an obvious opposite
        OPP = {"ctrl+w": "ctrl+shift+t", "windows+d": "windows+d", "windows+m": "windows+shift+m",
               "ctrl+=": "ctrl+-", "ctrl+-": "ctrl+=", "f11": "f11", "windows+left": "windows+right",
               "windows+right": "windows+left", "windows+up": "windows+down", "alt+shift": "alt+shift",
               "ctrl+shift+w": "ctrl+shift+t", "ctrl+x": "ctrl+z", "ctrl+v": "ctrl+z"}

        def after_hotkey(res, st, combo, *a, **kw):
            c = str(combo).lower().replace(" ", "")
            if c in OPP:
                J.record(f"нажал {c}", lambda: S.hotkey(OPP[c]), "hotkey", keys=c)
        S.hotkey = self._guarded(S.hotkey, None, after_hotkey)

        # ---- windows
        def win_before(query, action, *a, **kw):
            import win32gui
            if query in ("", "это", "окно", "текущее", "активное", "текущее окно", "это окно"):
                return [win32gui.GetForegroundWindow()]
            return [w["hwnd"] for w in S.find_windows(query)]

        def after_win(res, hwnds, query, action, *a, **kw):
            if not res or not hwnds or action in ("focus", "close", "show"):
                return
            import win32con
            import win32gui

            def back():
                for h in hwnds:
                    if win32gui.IsWindow(h):
                        win32gui.ShowWindow(h, win32con.SW_SHOW if action == "hide" else win32con.SW_RESTORE)
            word = {"minimize": "свернул", "maximize": "развернул", "hide": "скрыл", "restore": "восстановил"}.get(action, action)
            J.record(f"{word} {query or 'окно'}", back, "window")
        S.window_action = self._guarded(S.window_action, win_before, after_win)

        # ---- planner
        P = core.planner

        def after_timer(t, st, seconds, label="", *a, **kw):
            from .planner import say_duration
            J.record(f"таймер на {say_duration(int(seconds))}" + (f" «{label}»" if label else ""),
                     lambda: P.cancel_timer(t["id"]), "timer", id=t["id"], seconds=int(seconds), label=label)
        P.add_timer = self._guarded(P.add_timer, None, after_timer)

        def timer_cancel_before(tid=None, *a, **kw):
            return [dict(t, left=P.timer_left(t)) for t in P.timers.items if tid is None or t["id"] == tid]

        def after_timer_cancel(res, gone, *a, **kw):
            if gone:
                def back():
                    for t in gone:
                        P.add_timer(max(1, int(t["left"])), t.get("label") or "")
                J.record("отменил таймер" if len(gone) == 1 else f"отменил таймеры ({len(gone)})", back, "timer_cancel")
        P.cancel_timer = self._guarded(P.cancel_timer, timer_cancel_before, after_timer_cancel)

        def after_rem(r, st, text, when, repeat=None, alarm=False, *a, **kw):
            from .planner import say_when
            J.record(("будильник " if alarm else "напоминание ") + say_when(when) + (f": {text}" if text and not alarm else ""),
                     lambda: P.delete_reminder(r["id"]), "reminder", id=r["id"])
        P.add_reminder = self._guarded(P.add_reminder, None, after_rem)

        def rem_del_before(rid, *a, **kw):
            return next((dict(r) for r in P.reminders.items if r["id"] == rid), None)

        def after_rem_del(res, gone, *a, **kw):
            if gone:
                def back():
                    with P.lock:
                        if not any(r["id"] == gone["id"] for r in P.reminders.items):
                            P.reminders.items.append(gone)
                            P.reminders.save()
                    P.on_change("reminders")
                J.record(f"удалил напоминание «{gone['text']}»", back, "reminder_del")
        P.delete_reminder = self._guarded(P.delete_reminder, rem_del_before, after_rem_del)

        def rem_upd_before(rid, **kw):
            r = next((r for r in P.reminders.items if r["id"] == rid), None)
            return {k: r.get(k) for k in kw} if r else None

        def after_rem_upd(res, old, rid, **kw):
            if old:
                J.record("изменил напоминание", lambda: P.update_reminder(rid, **old), "reminder_upd")
        P.update_reminder = self._guarded(P.update_reminder, rem_upd_before, after_rem_upd)

        def after_task(t, st, text, *a, **kw):
            tags = t.get("tags") or []
            what = "заметка" if "заметка" in tags else "покупка" if "покупки" in tags else "задача"
            J.record(f"{what} «{t['text']}»", lambda: P.delete_task(t["id"]), "task", id=t["id"])
        P.add_task = self._guarded(P.add_task, None, after_task)

        def after_task_del(gone, st, *a, **kw):
            if gone:
                J.record(f"удалил «{gone['text']}»", lambda: P.restore_task(gone), "task_del")
        P.delete_task = self._guarded(P.delete_task, None, after_task_del)

        def task_upd_before(tid, **kw):
            t = next((t for t in P.tasks.items if t["id"] == tid), None)
            return {k: t.get(k) for k in kw} if t else None

        def after_task_upd(res, old, tid, **kw):
            if old is not None:
                word = "отметил выполненной" if kw.get("done") else "изменил"
                J.record(f"{word} «{(res or {}).get('text', 'задачу')}»", lambda: P.update_task(tid, **old), "task_upd")
        P.update_task = self._guarded(P.update_task, task_upd_before, after_task_upd)

        # ---- look and feel
        for name, label in (("set_theme", "тема"), ("set_voice", "голос"), ("set_night", "ночной режим")):
            fn = getattr(core, name)
            key = {"set_theme": "theme", "set_voice": "voice.silero_speaker", "set_night": "night.enabled"}[name]

            def before(*a, _key=key, **kw):
                return core.cfg.get(_key)

            def after(res, prev, *a, _fn=fn, _label=label, **kw):
                if prev is not None:
                    J.record(f"{_label}: {res}", lambda: _fn(prev), "look")
            setattr(core, name, self._guarded(fn, before, after))


# ====================================================================== text surgery
def _norm(s):
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s:.]", " ", s.lower().replace("ё", "е"))).strip()


def replace_fuzzy(text, old, new):
    return replace_fuzzy_scored(text, old, new)[1]


def replace_fuzzy_scored(text, old, new):
    """Replace the words of `old` in `text` (word forms may differ: 'котиков' ~ 'котики').
    Numbers must match exactly: 'на 1' is not 'на 2'. Returns (score, fixed text) or (0, None)."""
    from rapidfuzz import fuzz
    from .planner import words_to_digits
    words = words_to_digits(text).split()
    ow = _norm(words_to_digits(old)).split()
    if not ow:
        return 0, None
    digits = re.findall(r"\d+", " ".join(ow))
    best = None
    for i in range(len(words)):
        for j in range(i + 1, min(len(words), i + len(ow) + 2) + 1):
            cand = _norm(" ".join(words[i:j]))
            if digits and re.findall(r"\d+", cand) != digits:
                continue
            s = fuzz.ratio(cand, " ".join(ow))
            if best is None or s > best[0]:
                best = (s, i, j)
    if not best or best[0] < 62:
        return 0, None
    s, i, j = best
    nw = words_to_digits(new.strip()).split()
    tail = words[j:]
    # "на 1 минуту" fixed with "на 2 минуты" must not become "на 2 минуты минуту": the old word after the
    # replaced part is dropped when it is a form of the new last word
    if nw and tail and _norm(tail[0])[:4] == _norm(nw[-1])[:4]:
        tail = tail[1:]
    out = words[:i] + nw + tail
    out = [w for k, w in enumerate(out) if k == 0 or _norm(w) != _norm(out[k - 1])]
    return s, " ".join(out)


DUR = re.compile(r"(?:на\s+)?(?:\d+(?:[.,]\d+)?\s*(?:сек\w*|мин\w*|час\w*|ч\b)\s*)+|(?:на\s+)?(?:полчаса|час|минуту)\b")
TIME = re.compile(r"(?:в|на)\s+\d{1,2}(?::\d{2})?(?:\s+(?:утра|вечера|дня|ночи))?")


def guess_replace(text, new, g):
    """'я имел в виду собачек' after 'найди котиков': replace the part that looks like what changed."""
    from .planner import words_to_digits
    t, n = words_to_digits(text), words_to_digits(new.strip())
    if DUR.fullmatch(n.strip()) or re.match(r"^(?:на\s+)?\d+\s*(?:сек|мин|час)", n):
        m = DUR.search(t)
        if m:
            return t[:m.start()] + (n if n.startswith("на ") or not m.group(0).startswith("на ") else "на " + n) + " " + t[m.end():]
    if TIME.fullmatch(n.strip()) or re.match(r"^\d{1,2}(:\d{2})?$", n.strip()):
        m = TIME.search(t)
        if m:
            nn = n if re.match(r"^(?:в|на)\s", n) else m.group(0).split()[0] + " " + n
            return t[:m.start()] + nn + t[m.end():]
    q = next((i["data"].get("query") for i in g["items"] if i["data"].get("query")), None)
    if q and q in text:
        return text.replace(q, new.strip())
    return None
