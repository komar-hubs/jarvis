"""E-mail: any IMAP/SMTP mailbox (Gmail, Яндекс, Mail.ru, Outlook, own server) with an app password.

Voice: "проверь почту", "прочитай последнее письмо", "что пишет Иван", "ответь ему, что буду в пять",
"напиши письмо ivan@mail.ru тема отчёт текст ...", "удали это письмо", "отметь всё прочитанным".
Sending always asks "Отправить?" first. New mail is checked in the background; an important letter (from someone
you wrote to, a VIP, or "срочно" in the subject) makes Jarvis speak up and offer to read it, everything else only
counts. Passwords stay in data/config.json and never go to the interface."""
import datetime as dt
import email
import email.header
import email.utils
import html
import imaplib
import re
import smtplib
import ssl
import threading
import time
import uuid
from email.message import EmailMessage

from .config import JsonStore

PRESETS = {
    "gmail.com": ("imap.gmail.com", 993, "smtp.gmail.com", 465, "https://mail.google.com"),
    "googlemail.com": ("imap.gmail.com", 993, "smtp.gmail.com", 465, "https://mail.google.com"),
    "yandex.ru": ("imap.yandex.ru", 993, "smtp.yandex.ru", 465, "https://mail.yandex.ru"),
    "ya.ru": ("imap.yandex.ru", 993, "smtp.yandex.ru", 465, "https://mail.yandex.ru"),
    "yandex.com": ("imap.yandex.com", 993, "smtp.yandex.com", 465, "https://mail.yandex.com"),
    "mail.ru": ("imap.mail.ru", 993, "smtp.mail.ru", 465, "https://e.mail.ru"),
    "bk.ru": ("imap.mail.ru", 993, "smtp.mail.ru", 465, "https://e.mail.ru"),
    "inbox.ru": ("imap.mail.ru", 993, "smtp.mail.ru", 465, "https://e.mail.ru"),
    "list.ru": ("imap.mail.ru", 993, "smtp.mail.ru", 465, "https://e.mail.ru"),
    "rambler.ru": ("imap.rambler.ru", 993, "smtp.rambler.ru", 465, "https://mail.rambler.ru"),
    "outlook.com": ("outlook.office365.com", 993, "smtp-mail.outlook.com", 587, "https://outlook.live.com"),
    "hotmail.com": ("outlook.office365.com", 993, "smtp-mail.outlook.com", 587, "https://outlook.live.com"),
    "icloud.com": ("imap.mail.me.com", 993, "smtp.mail.me.com", 587, "https://www.icloud.com/mail"),
}
BULK = re.compile(r"no-?reply|noreply|do-?not-?reply|notification|newsletter|mailer|news@|info@|promo|marketing|"
                  r"digest|support@|robot|bounce", re.I)
URGENT = re.compile(r"срочн|важн|urgent|asap|немедленно|сегодня до|дедлайн|deadline|счет на оплату|счёт на оплату|"
                    r"оплат|штраф|суд|повестк|задолженн|блокировк|взлом|подозрительн\w+ вход|security alert", re.I)
CODE = re.compile(r"код\w*\s+(?:подтверждения|для входа|доступа)|verification code|one-time|одноразов", re.I)


def _dec(s):
    if not s:
        return ""
    try:
        return str(email.header.make_header(email.header.decode_header(s))).strip()
    except Exception:
        return str(s).strip()


def _addr(s):
    name, a = email.utils.parseaddr(_dec(s))
    return (name or a.split("@")[0]).strip().strip('"'), a.lower()


def _text_of(msg):
    """Plain text of a message: text/plain, or text/html stripped of tags."""
    plain, htm = None, None
    for part in msg.walk() if msg.is_multipart() else [msg]:
        if part.get_content_maintype() == "multipart" or part.get("Content-Disposition", "").startswith("attachment"):
            continue
        try:
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            text = payload.decode(part.get_content_charset() or "utf-8", "replace")
        except Exception:
            continue
        if part.get_content_type() == "text/plain" and plain is None:
            plain = text
        elif part.get_content_type() == "text/html" and htm is None:
            htm = text
    if plain and plain.strip():
        text = plain
    elif htm:
        text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", htm)
        text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", text)
        text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    else:
        text = ""
    text = re.sub(r"[ \t\xa0]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
    # quoted history of a reply is noise when read aloud
    text = re.split(r"\n(?:-{2,}\s*(?:Original|Исходное)|On .{5,80} wrote:|\d{1,2}\.\d{1,2}\.\d{2,4}.{0,40}пиш\w+:|>)", text)[0]
    return text.strip()


def _attachments(msg):
    out = []
    for part in msg.walk():
        fn = part.get_filename()
        if fn:
            out.append(_dec(fn))
    return out


class Mailbox:
    def __init__(self, acc):
        self.acc = acc

    def imap(self):
        a = self.acc
        M = imaplib.IMAP4_SSL(a["imap_host"], int(a.get("imap_port") or 993), ssl_context=ssl.create_default_context(),
                              timeout=20)
        M.login(a["address"], a["password"])
        return M

    def _trash(self, M):
        typ, data = M.list()
        for raw in data or []:
            line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
            if "\\Trash" in line:
                name = re.findall(r'"([^"]+)"\s*$|(\S+)\s*$', line)
                if name:
                    return name[0][0] or name[0][1]
        for cand in ("[Gmail]/Trash", "[Gmail]/Корзина", "Trash", "Корзина", "Deleted", "Deleted Items", "Удаленные"):
            if any(cand in (x.decode("utf-8", "replace") if isinstance(x, bytes) else str(x)) for x in data or []):
                return cand
        return None

    def headers(self, limit=20, unseen_only=False):
        """Newest messages of INBOX: [{uid, from_name, from, subject, date, unseen, bulk}]."""
        M = self.imap()
        try:
            M.select("INBOX", readonly=True)
            typ, data = M.uid("search", None, "UNSEEN" if unseen_only else "ALL")
            uids = (data[0] or b"").split()[-limit:]
            unseen = set()
            if not unseen_only and uids:
                typ, d2 = M.uid("search", None, "UNSEEN")
                unseen = set((d2[0] or b"").split())
            out = []
            if not uids:
                return out
            typ, data = M.uid("fetch", b",".join(uids),
                              "(FLAGS BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE MESSAGE-ID LIST-UNSUBSCRIBE PRECEDENCE)])")
            for item in data or []:
                if not isinstance(item, tuple):
                    continue
                meta = item[0].decode("utf-8", "replace")
                m = re.search(r"UID (\d+)", meta)
                if not m:
                    continue
                uid = m.group(1)
                h = email.message_from_bytes(item[1])
                name, addr = _addr(h.get("From"))
                try:
                    when = email.utils.parsedate_to_datetime(h.get("Date")).astimezone().replace(tzinfo=None)
                except Exception:
                    when = None
                bulk = bool(h.get("List-Unsubscribe")) or (h.get("Precedence") or "").lower() in ("bulk", "list") \
                    or bool(BULK.search(addr))
                out.append({"uid": uid, "from_name": name, "from": addr, "subject": _dec(h.get("Subject")) or "(без темы)",
                            "date": when.isoformat(timespec="minutes") if when else "", "msgid": h.get("Message-ID") or "",
                            "unseen": unseen_only or uid.encode() in unseen, "bulk": bulk})
            out.sort(key=lambda x: int(x["uid"]))
            return out
        finally:
            try:
                M.logout()
            except Exception:
                pass

    def fetch(self, uid, mark_seen=True):
        M = self.imap()
        try:
            M.select("INBOX", readonly=not mark_seen)
            typ, data = M.uid("fetch", str(uid), "(BODY[])" if mark_seen else "(BODY.PEEK[])")
            raw = next((x[1] for x in data or [] if isinstance(x, tuple)), None)
            if not raw:
                return None
            msg = email.message_from_bytes(raw)
            name, addr = _addr(msg.get("From"))
            return {"uid": str(uid), "from_name": name, "from": addr, "subject": _dec(msg.get("Subject")) or "(без темы)",
                    "date": msg.get("Date") or "", "msgid": msg.get("Message-ID") or "", "refs": msg.get("References") or "",
                    "to": _dec(msg.get("To")), "text": _text_of(msg), "attachments": _attachments(msg)}
        finally:
            try:
                M.logout()
            except Exception:
                pass

    def set_seen(self, uids, seen=True):
        M = self.imap()
        try:
            M.select("INBOX")
            for u in uids:
                M.uid("store", str(u), "+FLAGS" if seen else "-FLAGS", "(\\Seen)")
        finally:
            try:
                M.logout()
            except Exception:
                pass

    def trash(self, uid):
        M = self.imap()
        try:
            M.select("INBOX")
            folder = self._trash(M)
            if not folder:
                raise RuntimeError("не нашёл папку «Корзина» на сервере")
            q = f'"{folder}"' if " " in folder and not folder.startswith('"') else folder
            typ, _ = M.uid("copy", str(uid), q)
            if typ != "OK":
                raise RuntimeError("сервер не дал перенести письмо в корзину")
            M.uid("store", str(uid), "+FLAGS", "(\\Deleted)")
            M.expunge()
        finally:
            try:
                M.logout()
            except Exception:
                pass

    def send(self, to, subject, body, reply=None):
        a = self.acc
        msg = EmailMessage()
        msg["From"] = email.utils.formataddr((a.get("name") or "", a["address"]))
        msg["To"] = to
        msg["Subject"] = subject
        msg["Date"] = email.utils.formatdate(localtime=True)
        msg["Message-ID"] = email.utils.make_msgid()
        if reply and reply.get("msgid"):
            msg["In-Reply-To"] = reply["msgid"]
            msg["References"] = (reply.get("refs", "") + " " + reply["msgid"]).strip()
        sig = (a.get("signature") or "").strip()
        msg.set_content(body.rstrip() + ("\n\n--\n" + sig if sig else "") + "\n")
        port = int(a.get("smtp_port") or 465)
        ctx = ssl.create_default_context()
        if port == 465:
            with smtplib.SMTP_SSL(a["smtp_host"], port, context=ctx, timeout=30) as s:
                s.login(a["address"], a["password"])
                s.send_message(msg)
        else:
            with smtplib.SMTP(a["smtp_host"], port, timeout=30) as s:
                s.starttls(context=ctx)
                s.login(a["address"], a["password"])
                s.send_message(msg)
        return True


class Mail:
    def __init__(self, core):
        self.core = core
        self.cfg = core.cfg
        self.state = JsonStore("mail_state", default={})   # {acc_id: {"seen": [uids announced]}, "sent_to": [...]}
        if not isinstance(self.state.items, dict):
            self.state.items = {}
        self.cache = {}            # acc_id -> (time, headers)
        self.current = None        # the letter we talk about ("ответь ему", "удали это письмо")
        self.current_at = 0.0
        self.unread = {}           # acc_id -> count
        self._lock = threading.RLock()
        self._last_poll = 0.0
        self.error = {}

    # ================================================================== accounts
    def accounts(self):
        return [a for a in (self.cfg.get("mail.accounts") or []) if a.get("address") and a.get("password")]

    def account(self, acc_id=None):
        accs = self.accounts()
        if not accs:
            return None
        return next((a for a in accs if a.get("id") == acc_id), accs[0])

    @staticmethod
    def preset(address):
        dom = (address or "").split("@")[-1].lower()
        return PRESETS.get(dom)

    def rpc_mail_accounts(self):
        return [{k: v for k, v in a.items() if k != "password"} | {"password_set": bool(a.get("password")),
                                                                    "error": self.error.get(a.get("id"))}
                for a in (self.cfg.get("mail.accounts") or [])]

    def rpc_mail_account_save(self, acc):
        accs = list(self.cfg.get("mail.accounts") or [])
        acc = dict(acc)
        acc["address"] = (acc.get("address") or "").strip()
        if not acc["address"] or "@" not in acc["address"]:
            raise ValueError("нужен адрес почты")
        p = self.preset(acc["address"])
        if p:
            for k, v in zip(("imap_host", "imap_port", "smtp_host", "smtp_port", "web"), p):
                if not acc.get(k):
                    acc[k] = v
        if not acc.get("imap_host") or not acc.get("smtp_host"):
            raise ValueError("для этого домена укажите серверы IMAP и SMTP")
        old = next((a for a in accs if a.get("id") and a.get("id") == acc.get("id")), None)
        if not acc.get("password") and old:
            acc["password"] = old.get("password")
        acc.pop("password_set", None)
        acc.pop("error", None)
        acc["id"] = acc.get("id") or uuid.uuid4().hex[:8]
        accs = [a for a in accs if a.get("id") != acc["id"]] + [acc]
        self.cfg.set("mail.accounts", accs)
        self.cache.pop(acc["id"], None)
        return {k: v for k, v in acc.items() if k != "password"}

    def rpc_mail_account_delete(self, id):
        self.cfg.set("mail.accounts", [a for a in (self.cfg.get("mail.accounts") or []) if a.get("id") != id])
        self.cache.pop(id, None)
        return True

    def rpc_mail_test(self, id):
        a = next((x for x in self.cfg.get("mail.accounts") or [] if x.get("id") == id), None)
        if not a:
            raise ValueError("ящик не найден")
        t = time.time()
        hs = Mailbox(a).headers(limit=5, unseen_only=True)
        self.error.pop(id, None)
        return {"ok": True, "unseen": len(hs), "ms": int((time.time() - t) * 1000)}

    # ================================================================== UI and AI
    def rpc_mail_inbox(self, fresh=False):
        hs = self.all_inbox(30, fresh=bool(fresh))
        return {"items": hs, "accounts": self.rpc_mail_accounts(), "unread": sum(self.unread.values())}

    def rpc_mail_read(self, acc, uid):
        m = self.read({"acc": acc, "uid": uid})
        if not m:
            raise ValueError("письмо не найдено")
        return m

    def rpc_mail_send(self, to, subject, body, reply_uid=None, acc=None):
        reply = None
        if reply_uid and self.current and self.current.get("uid") == str(reply_uid):
            reply = self.current
        self.send(to, subject, body, reply, acc)
        return True

    def rpc_mail_delete(self, acc, uid):
        Mailbox(self.account(acc)).trash(uid)
        self.cache.pop(acc, None)
        if self.current and self.current.get("uid") == str(uid):
            self.current = None
        self.core.emit("mail")
        return True

    def rpc_mail_seen(self, acc, uid, seen=True):
        Mailbox(self.account(acc)).set_seen([uid], seen)
        self.cache.pop(acc, None)
        return True

    def tool_mail_list(self, a):
        hs = self.find(who=a.get("from") or None, about=a.get("query") or None, unseen=bool(a.get("unread")))
        return [{"id": f"{h['acc']}:{h['uid']}", "от": f"{h['from_name']} <{h['from']}>", "тема": h["subject"],
                 "дата": h["date"], "новое": h["unseen"], "рассылка": h["bulk"]} for h in hs[:int(a.get("limit") or 8)]]

    def _by_id(self, i):
        acc, _, uid = str(i).partition(":")
        if not uid:
            acc, uid = None, acc
        return {"acc": acc or (self.account() or {}).get("id"), "uid": uid}

    def tool_mail_read(self, a):
        m = self.read(self._by_id(a.get("id")))
        if not m:
            return "письмо не найдено"
        return {"от": f"{m['from_name']} <{m['from']}>", "тема": m["subject"], "дата": m["date"],
                "текст": m["text"][:5000], "вложения": m["attachments"]}

    def tool_mail_send(self, a):
        to = a.get("to") or ""
        if "@" not in to:
            to, _ = self.resolve(to)
            if not to:
                raise ValueError("не знаю адрес получателя — спроси пользователя")
        reply = self.current if a.get("reply") and self.current else None
        self.propose_send(to, a.get("subject") or "Без темы", a.get("body") or "", reply)
        return "черновик показан, жду подтверждения пользователя"

    def tool_mail_delete(self, a):
        h = self._by_id(a.get("id"))

        def go():
            Mailbox(self.account(h["acc"])).trash(h["uid"])
            self.cache.pop(h["acc"], None)
            self.core.say("Удалил.")
        self.core.ask_confirm(go, "Удалить письмо? Скажите «да».")
        return "жду подтверждения"

    # ================================================================== reading
    def inbox(self, acc=None, limit=25, fresh=False):
        a = self.account(acc)
        if not a:
            raise RuntimeError("почта не подключена: Почта → Добавить ящик")
        c = self.cache.get(a["id"])
        if c and not fresh and time.time() - c[0] < 60:
            return c[1]
        hs = Mailbox(a).headers(limit=limit)
        for h in hs:
            h["acc"] = a["id"]
        self.cache[a["id"]] = (time.time(), hs)
        self.unread[a["id"]] = sum(1 for h in hs if h["unseen"])
        return hs

    def all_inbox(self, limit=25, fresh=False):
        out = []
        for a in self.accounts():
            try:
                out += self.inbox(a["id"], limit, fresh)
                self.error.pop(a["id"], None)
            except Exception as e:
                self.error[a["id"]] = str(e)
                self.core.log(f"Почта {a['address']}: {e}", "warn")
        return sorted(out, key=lambda h: h.get("date") or "", reverse=True)

    def read(self, h, mark_seen=True):
        a = self.account(h.get("acc"))
        m = Mailbox(a).fetch(h["uid"], mark_seen=mark_seen)
        if m:
            m["acc"] = a["id"]
            self.current = m
            self.current_at = time.time()
            for x in self.cache.get(a["id"], (0, []))[1]:
                if x["uid"] == h["uid"]:
                    x["unseen"] = False
            self.core.emit("mail")
        return m

    def find(self, who=None, about=None, unseen=False):
        from rapidfuzz import fuzz
        hs = self.all_inbox(40)
        if unseen:
            hs = [h for h in hs if h["unseen"]]
        if who:
            w = who.lower().replace("ё", "е")
            w = re.sub(r"(?:а|у|ы|ой|ом|ем|е|и|я|ю)$", "", w) if len(w) > 4 and "@" not in w else w
            hs = [h for h in hs if max(fuzz.partial_ratio(w, (h["from_name"] or "").lower().replace("ё", "е")),
                                       fuzz.partial_ratio(w, h["from"])) >= 80]
        if about:
            ab = about.lower()
            hs = [h for h in hs if fuzz.partial_ratio(ab, h["subject"].lower()) >= 75]
        return hs

    # ================================================================== sending
    def resolve(self, who):
        """'ivan@mail.ru' / 'Ивану' / 'маме' -> address (contacts in settings, then names from the inbox)."""
        from rapidfuzz import fuzz
        who = (who or "").strip()
        m = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", who.replace(" собака ", "@").replace(" точка ", "."))
        if m:
            return m.group(0).lower(), m.group(0).lower()
        base = re.sub(r"(?:у|е|ю|ой|ей)$", "", who.lower()) if len(who) > 4 else who.lower()
        contacts = self.cfg.get("mail.contacts") or {}
        best = None
        for name, addr in contacts.items():
            s = max(fuzz.ratio(who.lower(), name.lower()), fuzz.partial_ratio(base, name.lower()))
            if s >= 80 and (best is None or s > best[0]):
                best = (s, name, addr)
        if best:
            return best[2], best[1]
        try:
            for h in self.all_inbox(40):
                s = fuzz.partial_ratio(base, (h["from_name"] or "").lower())
                if s >= 85 and not h["bulk"] and (best is None or s > best[0]):
                    best = (s, h["from_name"], h["from"])
        except Exception:
            pass
        return (best[2], best[1]) if best else (None, None)

    def send(self, to, subject, body, reply=None, acc=None):
        a = self.account(acc or (reply or {}).get("acc"))
        if not a:
            raise RuntimeError("почта не подключена")
        Mailbox(a).send(to, subject, body, reply)
        sent = self.state.items.setdefault("sent_to", [])
        if to.lower() not in sent:
            sent.append(to.lower())
            self.state.items["sent_to"] = sent[-300:]
            self.state.save()
        self.core.journal.record(f"отправил письмо {to}", None, "mail")
        try:
            self.core.people.touch(email=to)  # "последний контакт" for roles with several people
        except Exception:
            pass
        self.core.emit("mail")
        return True

    def propose_send(self, to, subject, body, reply=None):
        """Show the draft and ask "Отправить?"; send on "да"."""
        self.core.emit("mail_draft", to=to, subject=subject, body=body)
        self.core._chat_add("assistant", f"**Черновик письма**\nКому: {to}\nТема: {subject}\n\n{body}", "voice")
        short = body if len(body) < 160 else body[:150].rsplit(" ", 1)[0] + "…"

        def go():
            try:
                self.send(to, subject, body, reply)
                self.core.say("Письмо отправлено.")
            except Exception as e:
                self.core.log(f"Отправка письма: {e}", "error")
                self.core.say("Не удалось отправить письмо, сэр. Подробности в журнале.")
        self.core.ask_confirm(go, f"Письмо для {to}: «{short}». Отправить?")
        return True

    # ================================================================== background check
    def importance(self, h):
        sent = set(self.state.items.get("sent_to", []))
        vip = [v.lower() for v in self.cfg.get("mail.vip") or []]
        if any(v and (v in h["from"] or v in (h["from_name"] or "").lower()) for v in vip):
            return 3
        if CODE.search(h["subject"]):
            return 2
        if URGENT.search(h["subject"]) and not h["bulk"]:
            return 3
        if h["from"] in sent:
            return 3
        return 1 if h["bulk"] else 2

    def poll(self):
        if not self.accounts():
            return
        every = max(1, int(self.cfg.get("mail.check_min", 3) or 3)) * 60
        if time.time() - self._last_poll < every:
            return
        self._last_poll = time.time()
        threading.Thread(target=self._poll, daemon=True, name="mail").start()

    def _poll(self):
        with self._lock:
            for a in self.accounts():
                try:
                    hs = Mailbox(a).headers(limit=30, unseen_only=True)
                    self.error.pop(a["id"], None)
                except Exception as e:
                    self.error[a["id"]] = str(e)
                    continue
                st = self.state.items.setdefault(a["id"], {})
                first = "seen" not in st
                seen = set(st.get("seen", []))
                new = [dict(h, acc=a["id"]) for h in hs if h["uid"] not in seen]
                self.unread[a["id"]] = len(hs)
                st["seen"] = sorted(seen | {h["uid"] for h in hs}, key=int)[-500:]
                self.state.save()
                self.cache.pop(a["id"], None)
                if first or not new:
                    continue  # the first look is the baseline: no "у вас 300 писем" after setup
                self.core.emit("mail")
                self._announce(new)

    def _announce(self, new):
        level = max(self.importance(h) for h in new)
        top = max(new, key=self.importance)
        who = top["from_name"] or top["from"]
        if CODE.search(top["subject"]) and len(new) == 1:
            self.core.notify("Почта", f"{who}: {top['subject']}")
            return
        if len(new) == 1:
            text = f"Новое письмо от {who}: «{top['subject']}»."
        else:
            text = f"{len(new)} новых писем, в том числе от {who}: «{top['subject']}»."
        ini = self.core.initiative
        if level >= 3:
            ini.propose("mail:" + top["uid"], text + " Прочитать?", priority=3, title="Почта",
                        on_yes=lambda: self.say_letter(top), cooldown_min=0)
        else:
            ini.propose("mail:" + top["uid"], text, priority=1 if level <= 1 else 2, title="Почта", cooldown_min=0)

    # ================================================================== speech
    def say_letter(self, h):
        try:
            m = self.read(h)
        except Exception as e:
            self.core.say(f"Не смог открыть письмо: {e}")
            return
        if not m:
            self.core.say("Письмо не нашлось, сэр.")
            return
        self.core.say(self.letter_phrase(m))
        self.core.follow_up()

    def letter_phrase(self, m):
        body = m["text"] or ""
        att = f" Вложения: {', '.join(m['attachments'][:3])}." if m["attachments"] else ""
        if len(body) > 600:
            summary = self.core.ask_ai_once(
                "Перескажи это письмо в 2-3 предложениях для озвучки, по-русски, без вступлений:\n\n"
                f"От: {m['from_name']} <{m['from']}>\nТема: {m['subject']}\n\n{body[:6000]}")
            if summary and not summary.startswith("ИИ недоступен"):
                body = summary
            else:
                body = body[:500].rsplit(" ", 1)[0] + "… Полностью — на экране."
        self.core._chat_add("assistant", f"**{m['subject']}**\nОт: {m['from_name']} <{m['from']}>\n\n{m['text'][:4000]}", "voice")
        return f"От {m['from_name'] or m['from']}. Тема: {m['subject']}. {body}{att}"


# ====================================================================== voice rules
def register(R, core):
    def addr():
        return core.cfg.get("user.address") or "сэр"

    def M():
        return core.mail

    def need():
        if not M().accounts():
            core.show_tab("mail")
            return "Почта ещё не подключена, сэр. Добавьте ящик во вкладке «Почта» — понадобится пароль приложения."
        return None

    def bg(fn):
        def go():
            try:
                fn()
            except Exception as e:
                core.log(f"Почта: {e}", "error")
                core.say("Почтовый сервер не ответил, сэр." if "timed out" in str(e).lower() or "connect" in str(e).lower()
                         else f"Ошибка почты: {str(e)[:120]}")
        threading.Thread(target=go, daemon=True).start()
        return True

    @R(r"^(?:проверь|посмотри|глянь)\s+(?:мою\s+)?(?:почту|почтовый\s+ящик|ящик|письма)$|^(?:есть\s+(?:ли\s+)?|какие\s+)?(?:новые\s+)?письма$"
       r"|^(?:есть\s+(?:ли\s+)?)?(?:новые\s+|непрочитанные\s+)письма\w*$|^что\s+(?:нового\s+)?(?:на|в)\s+почте$|^сколько\s+(?:у\s+меня\s+)?(?:новых\s+|непрочитанных\s+)?писем$")
    def _check(m, t):
        n = need()
        if n:
            return n

        def go():
            hs = M().all_inbox(30, fresh=True)
            new = [h for h in hs if h["unseen"]]
            core.emit("mail")
            if not new:
                core.say(random_phrase(["Новых писем нет, сэр.", "Почта пуста. Ни одного непрочитанного."]))
                return
            people = [h for h in new if not h["bulk"]]
            lead = people or new
            names = []
            for h in lead:
                nm = h["from_name"] or h["from"]
                if nm not in names:
                    names.append(nm)
            from .planner import plural
            msg = f"{len(new)} {plural(len(new), 'непрочитанное письмо', 'непрочитанных письма', 'непрочитанных писем')}"
            if people:
                msg += ". От людей: " + ", ".join(names[:3]) + (f" и ещё {len(names) - 3}" if len(names) > 3 else "")
                msg += f". Последнее — «{people[0]['subject']}». Прочитать?"
                core.ask_confirm(lambda: M().say_letter(people[0]), msg)
            else:
                core.say(msg + ", в основном рассылки.")
        return bg(go)

    @R(r"^(?:прочитай|зачитай|открой|покажи)\s+(?:мне\s+)?(?:последнее|новое|свежее|первое|это|его)\s+письмо$|^(?:прочитай|зачитай)\s+(?:мне\s+)?(?:новые\s+)?письма$|^что\s+в\s+письме$")
    def _read_last(m, t):
        n = need()
        if n:
            return n

        def go():
            if re.search(r"\b(?:это|его)\b|что в письме", t) and M().current:
                core.say(M().letter_phrase(M().current))
                return
            hs = M().all_inbox(30, fresh=True)
            pick = next((h for h in hs if h["unseen"] and not h["bulk"]), None) or next((h for h in hs if h["unseen"]), None) \
                or (hs[0] if hs else None)
            if not pick:
                core.say("Писем нет, сэр.")
                return
            M().say_letter(pick)
        return bg(go)

    @R(r"^(?:прочитай|зачитай|открой|покажи)\s+(?:мне\s+)?(?:последнее\s+)?письм\w*\s+(?:от|из)\s+(.+)$|^что\s+(?:мне\s+)?(?:пишет|написал\w*|прислал\w*)\s+(.+)$")
    def _read_from(m, t):
        n = need()
        if n:
            return n
        who = (m.group(1) or m.group(2)).strip()

        def go():
            hs = M().find(who=who)
            if not hs:
                core.say(f"Писем от «{who}» не вижу, {addr()}.")
                return
            M().say_letter(hs[0])
        return bg(go)

    @R(r"^(?:найди|покажи|поищи)\s+(?:все\s+)?письма\s+(?:от\s+(.+?)|про\s+(.+?)|о\s+(.+?)|с\s+темой\s+(.+?))$")
    def _find(m, t):
        n = need()
        if n:
            return n
        who, about = m.group(1), m.group(2) or m.group(3) or m.group(4)

        def go():
            hs = M().find(who=who, about=about)
            core.show_tab("mail")
            if not hs:
                core.say("Ничего не нашёл, сэр.")
                return
            core.emit("mail", filter={"who": who, "about": about})
            items = "; ".join(f"{h['from_name'] or h['from']} — «{h['subject']}»" for h in hs[:3])
            core.say(f"Нашёл {len(hs)}. {items}.")
        return bg(go)

    @R(r"^(?:ответь|ответить)\s+(?:ему|ей|им|на\s+(?:это\s+|последнее\s+)?письмо|на\s+него|отправителю)?\s*(?:что|:)?\s*(.*)$")
    def _reply(m, t):
        n = need()
        if n:
            return n
        cur = M().current
        if not cur or time.time() - M().current_at > 900 or re.search(r"вопрос|сообщени|телеграм|чат|дискорд|вк\b", t):
            return False  # "ответь на сообщение в телеграме", "ответь на вопрос": not about mail
        said = (m.group(1) or "").strip()
        if not said:
            def take(raw):
                compose_reply(raw)
            core.capture = (take, time.time() + 25)
            core.listen_next(25)
            return "Что ответить?"
        return bg(lambda: compose_reply(said))

    def compose_reply(said):
        cur = M().current
        body = core.ask_ai_once(
            "Составь короткий вежливый ответ на письмо от имени пользователя по его словам. Только текст письма, "
            "без темы, без подписи, без кавычек и пояснений.\n\n"
            f"Письмо от {cur['from_name']}, тема «{cur['subject']}»:\n{cur['text'][:2500]}\n\nЧто хочет сказать пользователь: {said}")
        if not body or body.startswith("ИИ недоступен"):
            body = said[:1].upper() + said[1:]
        subj = cur["subject"] if cur["subject"].lower().startswith("re:") else "Re: " + cur["subject"]
        M().propose_send(cur["from"], subj, body.strip(), reply=cur)

    @R(r"^(?:напиши|отправь|составь)\s+(?:новое\s+)?(?:письмо|email|имейл|емейл|мейл|на\s+почту)\s+(.+)$")
    def _compose(m, t):
        n = need()
        if n:
            return n
        rest = m.group(1).strip()
        mm = re.match(r"^(?:на\s+(?:адрес\s+|почту\s+)?|для\s+|адресату\s+)?(.+?)(?:\s+(?:с\s+)?тем(?:а|ой)\s+(.+?))?(?:\s+(?:текст|с\s+текстом|что|о\s+том\s+что|где)\s+(.+))?$", rest)
        who, subject, text = (mm.group(1), mm.group(2), mm.group(3)) if mm else (rest, None, None)

        def go():
            # "шефу" -> the person tagged "руководитель" (data/people.json); "Ивану" -> by name; an address as is
            p, why = core.people.resolve(who, redo=lambda person: compose_for(person))
            if not p:
                if why == "unknown role":
                    from .people import role_of, role_word
                    rw = role_word(role_of(who))
                    core.say(f"Не знаю, кто у вас {rw}, сэр. Скажите, например: «мой {rw} — Антон».")
                    return
                # the recipient may be the first word(s), the rest is the content: let the AI split it
                core.spawn_chat(f"напиши письмо {rest}", source="voice", add_user=False,
                                extra="[Составь письмо и вызови инструмент mail_send с полями to, subject, body. "
                                      "Если адрес не ясен — спроси адрес, ничего не выдумывай.]")
                return
            compose_for(p)

        def compose_for(p, body=None):
            from .people import name_case
            to = core.people.email_of(p)
            name = name_case(p["name"])
            if not to:
                core.say(f"Не знаю почту {name}. Скажите: «почта {name} …» или впишите адрес во вкладке «Почта».")
                last = core.people.last
                if last is not None and body:
                    last["redo"] = lambda person: compose_for(person, body)  # keep the dictated text for later
                return
            if body:
                finish(to, subject, body)
                return
            if not text:
                # the form opens with the address filled in; the next phrase is the letter
                core.show_window()
                core.show_tab("mail")
                core.emit("mail_compose", to=to, name=name, subject=subject or "")
                core.say(f"Письмо для {name}. Что написать?")
                core.capture = (lambda raw: finish(to, subject, raw), time.time() + 40)
                core.listen_next(40)
                return
            finish(to, subject, text)

        def finish(to, subject, said):
            last = core.people.last
            if last is not None:
                # "нет, под шефом я имею в виду Антона": the same letter goes to the right person
                last["redo"] = lambda person: compose_for(person, said)
            body = core.ask_ai_once("Оформи это как короткое вежливое письмо от лица пользователя. Только текст письма, "
                                    "без темы и подписи, без кавычек:\n" + said)
            if not body or body.startswith("ИИ недоступен"):
                body = said
            subj = subject or core.ask_ai_once("Придумай тему письма из 2-5 слов, только тему без кавычек:\n" + body)[:80]
            if not subj or subj.startswith("ИИ недоступен"):
                subj = "Без темы"
            M().propose_send(to, subj.strip().strip("«»\""), body.strip())
        return bg(go)

    @R(r"^(?:удали|выброси|сотри)\s+(?:это\s+|последнее\s+|прочитанное\s+)?письмо$")
    def _delete(m, t):
        n = need()
        if n:
            return n
        cur = M().current
        if not cur:
            return "Какое письмо удалить? Сначала скажите «прочитай последнее письмо»."

        def go():
            try:
                Mailbox(M().account(cur["acc"])).trash(cur["uid"])
                M().cache.pop(cur["acc"], None)
                M().current = None
                core.emit("mail")
                core.say("Письмо в корзине.")
            except Exception as e:
                core.say(f"Не получилось: {e}")
        core.ask_confirm(lambda: threading.Thread(target=go, daemon=True).start(),
                         f"Удалить письмо «{cur['subject']}» от {cur['from_name'] or cur['from']}? Скажите «да».")
        return True

    @R(r"^(?:отметь|пометь)\s+(?:все\s+)?(?:письма\s+|почту\s+|это\s+)?(?:как\s+)?прочитанн\w*$|^(?:прочитай\s+)?все\s+письма\s+прочитанными$")
    def _mark(m, t):
        n = need()
        if n:
            return n

        def go():
            cnt = 0
            for a in M().accounts():
                hs = Mailbox(a).headers(limit=200, unseen_only=True)
                if hs:
                    Mailbox(a).set_seen([h["uid"] for h in hs])
                    cnt += len(hs)
                M().cache.pop(a["id"], None)
            core.emit("mail")
            core.say(f"Отметил прочитанными: {cnt}." if cnt else "Непрочитанных нет.")
        return bg(go)

    @R(r"^(?:открой|покажи)\s+(?:мою\s+)?(?:почту|почтовый\s+ящик|ящик)(?:\s+в\s+браузере)?$")
    def _open(m, t):
        from . import system as S
        a = M().account()
        if a and a.get("web"):
            S.open_target(a["web"])
            return "Открываю почту."
        if a:
            core.show_tab("mail")
            return True
        return False  # the old rule opens Gmail


def random_phrase(xs):
    import random
    return random.choice(xs)
