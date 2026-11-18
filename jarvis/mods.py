"""«Моды»: add-ons the user switches on or off in their own tab.

A mod is a whole piece of the app that not everybody needs (the phone remote's holographic keyboard).
Off means gone: no buttons, no endpoints answering. The state lives in config `mods.<id>`; a mod that was never
touched keeps its default. The phone remote gets the state in its hello and a WS "mods" message on every switch.
"""

# id, title, where it lives, what it is, what is inside, default
MODS = [
    {"id": "keyboard", "title": "Голографическая клавиатура", "where": "Пульт с телефона", "icon": "keyboard", "default": True,
     "what": "Клавиатура-купол на пульте: печать на ПК с телефона, русская и английская раскладки.",
     "inside": ["Точное попадание: клавиша срабатывает, когда отпускаете палец", "Ctrl, Alt, Shift, Win: касание, двойное касание, зажим",
                "Цифры, F-клавиши, стрелки, Del, Esc", "Полоса медиа: громкость, пауза, треки, «Звук ПК»"],
     "off": "Без неё тачпад занимает всю правую половину пульта."},
]


class Mods:
    def __init__(self, core):
        self.core, self.cfg = core, core.cfg

    def on(self, mid):
        m = next((m for m in MODS if m["id"] == mid), None)
        if not m:
            return False
        v = self.cfg.get("mods." + mid)
        return m["default"] if v is None else bool(v)

    def state(self):
        return {m["id"]: self.on(m["id"]) for m in MODS}

    def rpc_mods(self):
        return [{**m, "on": self.on(m["id"])} for m in MODS]

    def rpc_mod_set(self, id, on):
        m = next((m for m in MODS if m["id"] == id), None)
        if not m:
            raise ValueError("нет такого мода")
        self.cfg.set("mods." + id, bool(on))
        self.core.log(f"Мод «{m['title']}» {'включён' if on else 'выключен'}")
        remote = getattr(self.core, "remote", None)
        if remote:
            remote.mods_changed()
        return self.rpc_mods()
