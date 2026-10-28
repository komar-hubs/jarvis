"""Notebook: the "Заметки" tab. Plain-text notes with a title, pinning and search; voice notes land here too
("запиши в заметки …", "допиши в заметку …", "прочитай заметку про ремонт"). Stored in data/notebook.json.

Older versions kept voice notes as tasks tagged "заметка"; they are moved here once, on the first start."""
import datetime as dt
import re
import threading
import uuid

from .config import JsonStore


def _now():
    return dt.datetime.now().isoformat(timespec="seconds")


def _title_of(text):
    first = next((l.strip() for l in (text or "").splitlines() if l.strip()), "")
    return (first[:60] + "…") if len(first) > 60 else first


class Notebook:
    def __init__(self, core):
        self.core = core
        self.store = JsonStore("notebook", default=[])
        if not isinstance(self.store.items, list):
            self.store.items = []
        self._lock = threading.RLock()
        self._migrate()

    @property
    def items(self):
        return self.store.items

    def _migrate(self):
        if self.core.cfg.get("notebook.migrated"):
            return
        pl = self.core.planner
        old = [t for t in pl.tasks.items if "заметка" in (t.get("tags") or [])]
        for t in old:
            text = t.get("notes") or t.get("text") or ""
            self.items.append({"id": uuid.uuid4().hex[:10], "title": "", "text": text, "pinned": False,
                               "created": t.get("created") or _now(), "updated": t.get("created") or _now()})
        if old:
            self.store.save()
            for t in old:
                pl.delete_task(t["id"])
        self.core.cfg.set("notebook.migrated", True)

    def save(self):
        self.store.save()
        self.core.emit("notes")

    def add(self, text, title=""):
        n = {"id": uuid.uuid4().hex[:10], "title": title.strip(), "text": text.strip(), "pinned": False,
             "created": _now(), "updated": _now()}
        with self._lock:
            self.items.append(n)
            self.save()
        return n

    def get(self, id):
        return next((n for n in self.items if n["id"] == id), None)

    def title(self, n):
        return n.get("title") or _title_of(n.get("text")) or "Без названия"

    def search(self, q):
        from rapidfuzz import fuzz
        q = (q or "").lower().strip()
        if not q:
            return []
        scored = []
        for n in self.items:
            hay = (self.title(n) + " " + n.get("text", "")).lower()
            sc = 100 if q in hay else fuzz.partial_ratio(q, hay)
            if sc >= 75:
                scored.append((sc, n["updated"], n))
        return [n for _, _, n in sorted(scored, key=lambda x: (x[0], x[1]), reverse=True)]

    def latest(self):
        return max(self.items, key=lambda n: n.get("updated", ""), default=None)

    # ---------------------------------------------------------------- UI
    def rpc_notebook(self):
        return sorted(self.items, key=lambda n: (not n.get("pinned"), n.get("updated", "")), reverse=False)

    def rpc_note_save(self, note):
        with self._lock:
            n = self.get(note.get("id")) if note.get("id") else None
            if not n:
                n = {"id": uuid.uuid4().hex[:10], "created": _now(), "pinned": False}
                self.items.append(n)
            for k in ("title", "text", "pinned"):
                if k in note:
                    n[k] = note[k]
            n["updated"] = _now()
            self.save()
        return n

    def rpc_note_delete(self, id):
        with self._lock:
            self.store.items = [n for n in self.items if n["id"] != id]
            self.save()
        return True


# ====================================================================== voice rules
def register(R, core):
    NB = lambda: core.notebook

    def add_note(text):
        text = text.strip(" :.")
        if not text:
            return "Пустая заметка, сэр."
        NB().add(text[:1].upper() + text[1:])
        return "Записал в заметки."

    @R(r"^(?:запиши|добавь|сохрани|внеси)\s+(?:в\s+)?(?:блокнот|заметк\w*)\s*:?\s*(.+)$|^(?:создай|сделай|новая|напиши)\s+(?:новую\s+)?заметк\w*\s*:?\s*(.*)$|^заметка\s*:?\s*(.+)$")
    def _note(m, t):
        import time
        text = m.group(1) or m.group(2) or m.group(3) or ""
        if text.strip():
            return add_note(text)
        core.capture = (lambda raw: core.say(add_note(raw)), time.time() + 15)  # the next phrase is the note
        core.listen_next(15)
        return "Слушаю. Что записать?"

    @R(r"^(?:допиши|добавь)\s+(?:в\s+)?(?:последнюю\s+)?заметк\w+\s*:?\s*(.+)$")
    def _append(m, t):
        n = NB().latest()
        if not n:
            return add_note(m.group(1))
        NB().rpc_note_save({"id": n["id"], "text": (n.get("text", "").rstrip() + "\n" + m.group(1).strip()).strip()})
        return "Дописал в последнюю заметку."

    @R(r"^(?:открой|прочитай|покажи|какая)\s+(?:мою\s+)?последн\w+\s+заметк\w*$")
    def _last(m, t):
        n = NB().latest()
        core.show_tab("notes")
        if not n:
            return "Заметок нет."
        core.emit("note_open", id=n["id"])
        return f"Последняя заметка: {n['text'][:400]}"

    @R(r"^(?:прочитай|найди|покажи|открой)\s+(?:мне\s+)?заметк\w+\s+(?:про|о|об|по|с)\s+(.+)$|^(?:найди|поищи)\s+в\s+заметках\s+(.+)$|^что\s+(?:я\s+)?(?:записывал|писал)\s+(?:про|о|об)\s+(.+)$")
    def _find(m, t):
        q = m.group(1) or m.group(2) or m.group(3)
        found = NB().search(q)
        core.show_tab("notes")
        if not found:
            return f"В заметках ничего про «{q}»."
        core.emit("note_open", id=found[0]["id"])
        more = f" Есть ещё {len(found) - 1}." if len(found) > 1 else ""
        return f"«{NB().title(found[0])}»: {found[0]['text'][:400]}{more}"

    @R(r"^(?:покажи|открой|прочитай)\s+(?:мои\s+|все\s+)?заметки$|^мои\s+заметки$|^открой\s+блокнот\s+джарвиса$")
    def _all(m, t):
        ns = NB().items
        core.show_tab("notes")
        if not ns:
            return "Заметок нет."
        last = sorted(ns, key=lambda n: n.get("updated", ""))[-3:]
        return f"{len(ns)} {'заметка' if len(ns) == 1 else 'заметки' if 2 <= len(ns) % 10 <= 4 and not 12 <= len(ns) % 100 <= 14 else 'заметок'}. Последние: " + "; ".join(NB().title(n) for n in last) + "."
