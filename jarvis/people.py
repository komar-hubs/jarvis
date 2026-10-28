"""People and how the user calls them: "шеф" -> Антон <anton@firm.ru>, "мама" -> mama@mail.ru.

resolve("шефу") finds the person behind a role word (шеф = босс = начальник = руководитель), a name in any case
("Антону") or an address. With several people tagged "руководитель" the one contacted last wins. One correction —
"нет, под шефом я имею в виду Антона, а не Игоря" — rebinds the role for good and redoes what was just prepared
for the wrong person (a mail draft). Stored in data/people.json."""
import re
import time
import uuid

from .config import JsonStore

ROLES = {
    "руководитель": ["шеф", "босс", "бос", "начальник", "начальница", "руководитель", "руководительница", "директор",
                     "директриса", "шефиня", "начальство", "менеджер"],
    "мама": ["мама", "мать", "мамочка", "мамуля", "маман"],
    "папа": ["папа", "отец", "батя", "папочка", "папаня"],
    "жена": ["жена", "супруга", "жёнушка", "женушка"],
    "муж": ["муж", "супруг"],
    "брат": ["брат", "братишка", "братан"],
    "сестра": ["сестра", "сестрёнка", "сестренка", "систер"],
    "бабушка": ["бабушка", "бабуля", "ба"],
    "дедушка": ["дедушка", "дед", "деда"],
    "девушка": ["девушка", "любимая"],
    "парень": ["парень", "любимый"],
    "друг": ["друг", "дружище", "кореш", "бро"],
    "подруга": ["подруга", "подружка"],
    "коллега": ["коллега", "сотрудник"],
    "учитель": ["учитель", "учительница", "препод", "преподаватель", "классная", "классный руководитель"],
    "клиент": ["клиент", "заказчик"],
    "бухгалтер": ["бухгалтер", "бухгалтерия"],
}
WORD2ROLE = {w: r for r, ws in ROLES.items() for w in ws}


def lemma(word):
    from .nlu import _parse
    w = (word or "").lower().replace("ё", "е").strip(" ,.!?«»\"")
    ps = _parse(w)
    if not ps:
        return w
    # names first: "антону" -> "антон", not some rare verb form
    for p in ps[:4]:
        if "Name" in p.tag or "Surn" in p.tag or "Patr" in p.tag:
            return p.normal_form
    return ps[0].normal_form


def role_of(words):
    """'шефу' / 'моему начальнику' / 'классному руководителю' -> canonical role or None."""
    ws = [lemma(w) for w in re.findall(r"[а-яёa-z-]+", (words or "").lower())]
    joined = " ".join(ws)
    for r, syn in ROLES.items():
        for s in syn:
            if " " in s and s in joined:
                return r
    for w in ws:
        if w in WORD2ROLE:
            return WORD2ROLE[w]
    return None


class People:
    def __init__(self, core):
        self.core = core
        self.store = JsonStore("people", default={"people": [], "roles": {}})
        if not isinstance(self.store.items, dict):
            self.store.items = {"people": [], "roles": {}}
        self.store.items.setdefault("people", [])
        self.store.items.setdefault("roles", {})
        self.last = None   # {"word", "role", "person", "redo"}: the last resolution, for "нет, я имел в виду…"

    # ---------------------------------------------------------------- storage
    @property
    def people(self):
        return self.store.items["people"]

    def save(self):
        self.store.save()
        self.core.emit("people")

    def get(self, pid):
        return next((p for p in self.people if p["id"] == pid), None)

    def by_name(self, name):
        from rapidfuzz import fuzz
        n = lemma(name)
        best = None
        for p in self.people:
            for cand in [p["name"]] + p.get("aliases", []):
                c = cand.lower().replace("ё", "е")
                s = 100 if lemma(c.split()[0]) == n or c == n else fuzz.ratio(n, c.split()[0])
                if s >= 85 and (best is None or s > best[0]):
                    best = (s, p)
        return best[1] if best else None

    def by_email(self, email):
        e = (email or "").lower()
        return next((p for p in self.people if e in [x.lower() for x in p.get("emails", [])]), None)

    def add(self, name, email=None, roles=None):
        p = self.by_email(email) if email else None
        p = p or self.by_name(name)
        if not p:
            nm = name.strip()
            nm = nm.title() if nm.islower() else nm
            p = {"id": uuid.uuid4().hex[:8], "name": nm, "emails": [], "roles": [], "aliases": [], "last": 0}
            self.people.append(p)
        if email and email.lower() not in [x.lower() for x in p["emails"]]:
            p["emails"].insert(0, email.lower())
        for r in roles or []:
            if r not in p["roles"]:
                p["roles"].append(r)
        self.save()
        return p

    def touch(self, email=None, pid=None):
        p = self.get(pid) if pid else self.by_email(email)
        if p:
            p["last"] = time.time()
            self.save()

    # ---------------------------------------------------------------- resolving
    def _mail_lookup(self, name):
        """A name the base does not know: look it up in mail contacts and the inbox, and remember it."""
        try:
            addr, nm = self.core.mail.resolve(name)
        except Exception:
            addr, nm = None, None
        if addr:
            return self.add(nm or name, addr)
        return None

    def resolve(self, spoken, redo=None):
        """-> (person or None, why). why: 'email' | 'role' | 'name' | 'unknown role' | 'unknown'."""
        spoken = (spoken or "").strip()
        m = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", spoken.replace(" собака ", "@").replace(" точка ", "."))
        if m:
            addr = m.group(0).lower()
            p = self.by_email(addr) or {"id": None, "name": addr, "emails": [addr], "roles": []}
            self.last = {"word": spoken, "role": None, "person": p.get("id"), "redo": redo}
            return p, "email"
        r = role_of(spoken)
        if r:
            pid = self.store.items["roles"].get(r)
            p = self.get(pid) if pid else None
            if not p:
                tagged = [x for x in self.people if r in x.get("roles", [])]
                p = max(tagged, key=lambda x: x.get("last", 0)) if tagged else None
            self.last = {"word": spoken, "role": r, "person": p and p["id"], "redo": redo}
            return (p, "role") if p else (None, "unknown role")
        words = re.findall(r"[а-яёa-z-]+", spoken.lower())
        words = [w for w in words if w not in ("мой", "моему", "моей", "моя", "для", "к", "у")]
        p = self.by_name(words[0]) if words else None
        if not p and words:
            p = self._mail_lookup(lemma(words[0]))
        self.last = {"word": spoken, "role": None, "person": p and p["id"], "redo": redo}
        return (p, "name") if p else (None, "unknown")

    def email_of(self, p):
        return (p or {}).get("emails", [None])[0] if p and p.get("emails") else None

    # ---------------------------------------------------------------- learning
    def bind_role(self, role, name, email=None, not_name=None):
        """'шеф' = Антон (for good). Returns the person."""
        name = " ".join(lemma(w) for w in name.split())  # "Антона" -> "антон"
        p = self.by_name(name) or (self.by_email(email) if email else None)
        if not p:
            p = self._mail_lookup(name) or self.add(name, email)
        if email:
            self.add(p["name"], email)
        for x in self.people:
            if role in x.get("roles", []) and x is not p:
                x["roles"].remove(role)  # one person per role: the old one (Игорь) loses the tag
        if role not in p["roles"]:
            p["roles"].append(role)
        self.store.items["roles"][role] = p["id"]
        self.save()
        return p

    def forget(self, name):
        p = self.by_name(name)
        if not p:
            return False
        self.people.remove(p)
        self.store.items["roles"] = {r: pid for r, pid in self.store.items["roles"].items() if pid != p["id"]}
        self.save()
        return True

    def describe(self, p):
        roles = ", ".join(p.get("roles", []))
        mail = self.email_of(p)
        return f"{p['name']}" + (f" — {roles}" if roles else "") + (f", {mail}" if mail else "")

    # ---------------------------------------------------------------- UI
    def rpc_people(self):
        return {"people": self.people, "roles": self.store.items["roles"], "known_roles": list(ROLES)}

    def rpc_person_save(self, person):
        p = self.get(person.get("id")) if person.get("id") else None
        if not p:
            p = {"id": uuid.uuid4().hex[:8], "last": 0}
            self.people.append(p)
        p["name"] = (person.get("name") or "").strip() or "Без имени"
        p["emails"] = [e.strip().lower() for e in person.get("emails", []) if "@" in e]
        p["roles"] = [r.strip().lower() for r in person.get("roles", []) if r.strip()]
        p["aliases"] = [a.strip().lower() for a in person.get("aliases", []) if a.strip()]
        for r in p["roles"]:
            r = WORD2ROLE.get(r, r)
            self.store.items["roles"][r] = p["id"]
        self.save()
        return p

    def rpc_person_delete(self, id):
        p = self.get(id)
        if p:
            self.people.remove(p)
            self.store.items["roles"] = {r: pid for r, pid in self.store.items["roles"].items() if pid != id}
            self.save()
        return True


# ====================================================================== voice rules
def register(R, core):
    P = lambda: core.people

    def learned(role, name, email=None, not_name=None):
        p = P().bind_role(role, name, email, not_name)
        last = P().last
        msg = f"Запомнил: {role_word(role)} — это {p['name']}" + (f", {P().email_of(p)}" if P().email_of(p) else "") + "."
        # redo what was just prepared for the wrong person ("почту шефу" -> the draft goes to Антон now)
        if last and last.get("redo") and last.get("role") == role:
            core.pending = None
            redo = last["redo"]
            core.say(msg + " Переделываю.")
            core.after_speech(lambda: redo(p), 0.1)
            return True
        if not P().email_of(p):
            msg += f" Адрес его почты я пока не знаю — скажите «почта {p['name']} …» или добавьте во вкладке «Почта»."
        return msg

    # "нет, под шефом я имею в виду Антона, а не Игоря" / "под словом босс я имел в виду Антона"
    @R(r"^(?:нет\s+)?(?:под|говоря)\s+(?:словом\s+)?(\w+)\s+(?:я\s+)?(?:имею|имел|имела|подразумеваю|подразумевал\w*)\s+(?:в\s+виду\s+)?(\w+(?:\s+\w+)?)(?:\s+а\s+не\s+(\w+))?$")
    def _correct_role(m, t):
        role = role_of(m.group(1))
        if not role:
            return False
        return learned(role, m.group(2), not_name=m.group(3))

    # "шеф — это Антон, а не Игорь" / "мой шеф Антон" / "запомни, что мой начальник Антон anton@firm.ru"
    @R(r"^(?:нет\s+)?(?:запомни\s+)?(?:что\s+)?(?:мой|моя|мое|моё)\s+(\w+)\s+(?:это\s+|теперь\s+)?([а-яёa-z]+(?:\s+[а-яё]+)?)(?:\s+(\S+@\S+))?(?:\s+а\s+не\s+(\w+))?$"
       r"|^(?:нет\s+)?(\w+)\s+это\s+([а-яёa-z]+)(?:\s+а\s+не\s+(\w+))?$")
    def _bind(m, t):
        role_w = m.group(1) or m.group(5)
        name = m.group(2) or m.group(6)
        role = role_of(role_w)
        if not role or role_of(name):
            return False
        return learned(role, name, m.group(3), m.group(4) or m.group(7))

    @R(r"^(?:запомни\s+)?(?:почта|адрес|почту|емейл|email)\s+(?:у\s+)?([а-яё]+)\s+(\S+(?:\s+собака\s+\S+(?:\s+точка\s+\S+)+)?)$")
    def _email(m, t):
        addr = m.group(2).replace(" собака ", "@").replace(" точка ", ".").replace(" ", "")
        if "@" not in addr:
            return False
        r = role_of(m.group(1))
        if r:
            pid = P().store.items["roles"].get(r)
            p = P().get(pid) if pid else None
            if not p:
                return f"Сначала скажите, кто у вас {m.group(1)}: «мой {m.group(1)} — Антон»."
            P().add(p["name"], addr)
            return continue_waiting(p, f"Запомнил почту: {p['name']}, {addr}.")
        p = P().add(lemma(m.group(1)), addr)
        return continue_waiting(p, f"Запомнил: {p['name']}, {addr}.")

    def continue_waiting(p, msg):
        """A letter was waiting for this person's address ("не знаю почту Антона"): it goes on now."""
        last = P().last
        if last and last.get("redo") and (last.get("person") == p["id"] or
                                          (last.get("role") and P().store.items["roles"].get(last["role"]) == p["id"])):
            redo, last["redo"] = last["redo"], None
            core.say(msg + " Продолжаю письмо.")
            core.after_speech(lambda: redo(p), 0.1)
            return True
        return msg

    @R(r"^кто\s+(?:у\s+меня\s+)?(?:мой|моя|мое)\s+(\w+)$|^кто\s+такой\s+(\w+)$|^кто\s+такая\s+(\w+)$")
    def _who(m, t):
        w = m.group(1) or m.group(2) or m.group(3)
        p, why = P().resolve(w)
        if not p:
            return f"Пока не знаю, {'кто ваш ' + w if role_of(w) else 'кто такой ' + w}. Скажите, например: «мой {w if role_of(w) else 'шеф'} — Антон»."
        return P().describe(p) + "."

    @R(r"^(?:мои\s+контакты|кого\s+ты\s+знаешь|список\s+контактов|покажи\s+контакты)$")
    def _list(m, t):
        ps = P().people
        core.show_tab("mail")
        if not ps:
            return "Я пока никого не знаю. Скажите «мой шеф — Антон» или напишите кому-нибудь письмо."
        return "Знаю: " + "; ".join(P().describe(p) for p in ps[:6]) + "."

    @R(r"^(?:забудь|удали)\s+(?:контакт\s+)?([а-яё]+)\s+из\s+(?:контактов|связей)$")
    def _forget(m, t):
        return "Забыл." if P().forget(m.group(1)) else "Такого контакта нет."


def role_word(role):
    return {"руководитель": "шеф"}.get(role, role)


def name_case(name, case="gent"):
    """'Антон' -> 'Антона' (for "письмо для Антона"); unknown names stay as they are."""
    from .nlu import _parse
    out = []
    for w in (name or "").split():
        ps = [p for p in _parse(w.lower()) if "Name" in p.tag or "Surn" in p.tag]
        f = ps[0].inflect({case}) if ps else None
        out.append((f.word.capitalize() if w[:1].isupper() else f.word) if f else w)
    return " ".join(out)
