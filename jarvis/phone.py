"""The phone through KDE Connect (free, Android): its notifications and calls spoken on the PC, "найди телефон",
links and the clipboard sent to the phone, SMS, a photo from the phone's camera.

Jarvis talks to KDE Connect only through its command line tool (kdeconnect-cli.exe), every 5 s while a paired phone
is in the same Wi-Fi. Everything stays in the local network: no cloud, no account. iPhone is not supported by KDE
Connect in this way."""
import collections
import datetime as dt
import os
import re
import subprocess
import threading
import time
from pathlib import Path

CREATE_NO_WINDOW = 0x08000000
CANDIDATES = [Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "KDE Connect" / "bin",
              Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "KDE Connect" / "bin"]
CALL_RX = re.compile(r"(?i)входящ\w*\s+(?:вызов|звонок)|звонит|incoming\s+call|calling")
# a missed call is news, not a ringing phone: it was announced as "Звонит Мама: Пропущенный вызов"
# only a real missed call: the bare word "missed" inside a Telegram message became "Пропущенный звонок от missed"
MISSED_RX = re.compile(r"(?i)пропущенн\w*\s+(?:вызов|звонок|аудиозвонок|видеозвонок)|missed\s+(?:voice\s+|video\s+)?call")
# the SMS app's own status lines are not senders: "SMS от Сообщения не отправлены"
SMS_SYSTEM_RX = re.compile(r"(?i)сообщени\w*\s+не\s+(?:отправлен|доставлен)|не\s+удалось\s+отправить|новы\w+\s+сообщени|"
                           r"^\d+\s+(?:новы\w+\s+)?сообщени|черновик|ошибк")
DIALERS = ("телефон", "phone", "dialer", "звонки", "вызовы", "contacts")
MUTE_DEFAULT = ["KDE Connect", "Android System", "Система Android", "Google Play", "Google Play Маркет", "Загрузки",
                "Downloads", "Battery", "Батарея"]
MESSENGERS = ["Telegram", "WhatsApp", "Viber", "VK", "ВКонтакте", "VK Мессенджер", "Discord", "Signal", "MAX", "Skype",
              "Snapchat", "Instagram", "Messenger", "WeChat", "Element", "Тамтам", "TamTam",
              "WilliGram", "Nekogram", "Plus Messenger", "Telegram X", "Forkgram", "AyuGram", "Cherrygram", "Nicegram"]
# the phone's own SMS app: a message from a person, answered by SMS when the notification has no "Reply" field
SMS_APPS = ["Сообщения", "Messages", "Google Messages", "SMS", "СМС", "Сообщения Samsung", "Samsung Messages", "Mi Сообщения"]
MESSENGERS += SMS_APPS
# shown in Settings even before the phone sent anything, so rules can be set up front
POPULAR = ["MAX", "Telegram", "WhatsApp", "VK", "Discord", "Viber", "Instagram", "Gmail", "Почта", "Сбербанк", "Т-Банк",
           "Госуслуги", "YouTube"]
# what to do with a notification: say nothing / say which app / ask "Прочитать?" / read it right away
RULES = {"silent": "Молчать", "name": "Сказать, от кого", "ask": "Спросить, прочитать ли", "read": "Читать сразу"}
# not worth a word: ads, progress bars, system chatter. Matched on the text, the text itself is never spoken
NOISE_RX = re.compile(r"(?i)скидк|акци[яи]|распродаж|промокод|кешб[эе]к|кэшб[эе]к|бонус|выгодн|спецпредложени|реклам|"
                      r"\bsale\b|\boffer\b|\d+\s*%|скачивани|загрузка\s|загружено|обновлени[ея]\s+(?:доступно|установлено)|"
                      r"синхронизац|резервн\w+\s+копи|работает\s+в\s+фоне|запущен[оа]?\s+в\s+фоне|vpn|подключено\s+к|"
                      r"usb|зарядк|заряжается|батаре|погода\s+на|шагов|экран\s+включ|"
                      r"приложение\s+устарел|обновите\s+приложени")
PROMO_RX = re.compile(r"(?i)играй\s+сейчас|играть\s+сейчас|сыграй|вернись\s+в\s+игру|возвращайся\s+в\s+игру|"
                      r"забери\s+(?:награду|подарок|бонус)|награда\s+(?:ждёт|ждет|готова)|бесплатн\w+\s+(?:спин|попытк|жизн)|"
                      r"энерги[яи]\s+(?:восстановлен|полн)|новый\s+уровень|тебя\s+ждут?|не\s+пропусти")
# emoji in a notification are read as nonsense and break the "no system emoji" look of the HUD
EMOJI_RX = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u200D\u20E3]+")
NOISE_APPS = ["Google", "Play Маркет", "Galaxy Store", "GetApps", "Security", "Безопасность", "Темы", "Themes",
              "Погода", "Weather", "Часы", "Clock", "Mi Fit", "Zepp", "Яндекс Маркет", "Ozon", "Wildberries", "AliExpress"]


class KdeBus:
    """KDE Connect's own D-Bus, for the phone's notifications.

    kdeconnect-cli --list-notifications always answers an empty list on Windows (it reads the model before the
    service replies), so Jarvis listens to the service directly: the private dbus-daemon of KDE Connect listens on a
    127.0.0.1 TCP port and lets in the same Windows user (EXTERNAL auth with the user's SID; the daemon checks the
    connecting process). Signals notificationPosted / notificationUpdated arrive the moment the phone sends one."""
    IFACE = "org.kde.kdeconnect.device.notifications"

    def __init__(self, phone):
        self.phone = phone
        self.bus = None
        self.ok = False

    def start(self):
        threading.Thread(target=self._run, daemon=True, name="kde-dbus").start()

    def _run(self):
        import asyncio
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as r:   # dbus-next needs add_reader: not on Proactor
            r.run(self._main())

    async def _main(self):
        import asyncio
        await asyncio.sleep(20)
        while True:
            try:
                if self.phone.cfg.get("phone.enabled", True) and self.phone.bin_dir():
                    await self._session()
            except Exception as e:
                self.phone.last_error = f"уведомления телефона: {e}"[:200]
            self.ok = False
            await asyncio.sleep(15)

    @staticmethod
    def _port():
        import psutil
        for p in psutil.process_iter(["name", "exe"]):
            if (p.info["name"] or "").lower() == "dbus-daemon.exe" and "kde connect" in (p.info["exe"] or "").lower():
                for c in p.net_connections(kind="tcp"):
                    if c.status == "LISTEN" and c.laddr.ip in ("127.0.0.1", "::1"):
                        return c.laddr.port
        return None

    @staticmethod
    def _sid():
        r = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True, timeout=10, creationflags=CREATE_NO_WINDOW)
        return r.stdout.decode("cp866", "replace").strip().split(",")[-1].strip('"')

    async def _session(self):
        import asyncio
        from dbus_next import Message, MessageType
        from dbus_next.aio import MessageBus
        from dbus_next.auth import Authenticator

        class AuthSid(Authenticator):
            def __init__(self, sid):
                self.sid = sid

            def _authentication_start(self, negotiate_unix_fd=False):
                return "AUTH EXTERNAL " + self.sid.encode().hex()

            def _receive_line(self, line):
                if line.startswith("OK"):
                    return "BEGIN"
                raise RuntimeError("KDE Connect не пустил: " + line)

        port = self._port()
        if not port:
            return
        bus = await MessageBus(bus_address=f"tcp:host=127.0.0.1,port={port}", auth=AuthSid(self._sid())).connect()
        self.bus = bus
        self.loop = asyncio.get_running_loop()
        await bus.call(Message(destination="org.freedesktop.DBus", path="/org/freedesktop/DBus", interface="org.freedesktop.DBus",
                               member="AddMatch", signature="s", body=[f"type='signal',interface='{self.IFACE}'"]))

        async def fetch(path, nid, updated):
            try:
                r = await bus.call(Message(destination="org.kde.kdeconnect.daemon", path=f"{path}/{nid}",
                                           interface="org.freedesktop.DBus.Properties", member="GetAll", signature="s",
                                           body=[self.IFACE + ".notification"]))
                if r.message_type == MessageType.METHOD_RETURN and r.body:
                    self.phone._from_bus({k: v.value for k, v in r.body[0].items()} | {"id": nid}, updated)
            except Exception as e:
                self.phone.last_error = f"уведомление телефона: {e}"[:200]

        def handler(msg):
            if msg.message_type == MessageType.SIGNAL and msg.interface == self.IFACE and \
                    msg.member in ("notificationPosted", "notificationUpdated") and msg.body:
                asyncio.ensure_future(fetch(msg.path, msg.body[0], msg.member == "notificationUpdated"))

        bus.add_message_handler(handler)
        self.ok = True
        self.phone.last_error = ""
        # the signals do not always reach a client on Windows: the list itself is compared every 3 s as well
        # (a messenger often rewrites one notification per chat, so a changed text counts as new)
        first = True
        while bus.connected:
            d = self.phone.device()
            if d:
                await self._poll(bus, d["id"], first)
                first = False
            else:
                first = True
            await asyncio.sleep(3)

    async def _poll(self, bus, dev, baseline):
        from dbus_next import Message, MessageType
        path = f"/modules/kdeconnect/devices/{dev}/notifications"
        r = await bus.call(Message(destination="org.kde.kdeconnect.daemon", path=path, interface=self.IFACE, member="activeNotifications"))
        if r.message_type != MessageType.METHOD_RETURN or not r.body:
            return
        for nid in r.body[0]:
            p = await bus.call(Message(destination="org.kde.kdeconnect.daemon", path=f"{path}/{nid}", interface="org.freedesktop.DBus.Properties",
                                       member="GetAll", signature="s", body=[self.IFACE + ".notification"]))
            if p.message_type != MessageType.METHOD_RETURN or not p.body:
                continue
            props = {k: v.value for k, v in p.body[0].items()} | {"id": nid}
            if baseline:                  # already on the phone when Jarvis connected: not news, but can be answered
                self.phone._last_text[str(props.get("internalId") or nid)] = (str(props.get("title") or "").strip(),
                                                                              str(props.get("text") or "").strip())
                self.phone._remember_reply(props)
            else:
                self.phone._from_bus(props, True)


    def send_reply(self, dev, reply_id, message):
        """Answer a messenger notification on the phone (its own "Reply" field): KDE Connect's sendReply."""
        import asyncio
        from dbus_next import Message, MessageType
        if not self.ok or not self.bus or not getattr(self, "loop", None):
            raise RuntimeError("нет связи с KDE Connect")

        async def go():
            return await self.bus.call(Message(destination="org.kde.kdeconnect.daemon",
                                               path=f"/modules/kdeconnect/devices/{dev}/notifications",
                                               interface=self.IFACE, member="sendReply", signature="ss",
                                               body=[reply_id, message]))
        r = asyncio.run_coroutine_threadsafe(go(), self.loop).result(timeout=10)
        if r.message_type != MessageType.METHOD_RETURN:
            raise RuntimeError(str(r.body[0] if r.body else r.error_name))
        return True


class Phone:
    def __init__(self, core):
        self.core = core
        self.cfg = core.cfg
        self.recent = collections.deque(maxlen=40)
        self.repliable = collections.deque(maxlen=20)
        self.searching = False                         # "найди телефон": ringing again and again until found   # messenger notifications with a "Reply" field on the phone
        self.seen = None              # notification lines already on the phone (None = not looked yet)
        self.devices = []             # [{"id", "name", "reachable"}]
        self._dev_t = 0
        self._knock_t = 0
        self.last_error = ""
        self._last_text = {}          # notification id -> (title, text): an update without changes is not new
        self.bus = KdeBus(self)

    # ---------------------------------------------------------------- KDE Connect tool
    def bin_dir(self):
        p = self.cfg.get("phone.bin")
        for d in ([Path(p)] if p else []) + CANDIDATES:
            if (d / "kdeconnect-cli.exe").exists():
                return d
        return None

    def cli(self, *args, timeout=12):
        d = self.bin_dir()
        if not d:
            raise RuntimeError("KDE Connect не установлен")
        r = subprocess.run([str(d / "kdeconnect-cli.exe"), *args], capture_output=True, timeout=timeout,
                           creationflags=CREATE_NO_WINDOW)
        out = r.stdout.decode("utf-8", "replace").replace("\r\n", "\n")
        if r.returncode != 0 and not out.strip():
            raise RuntimeError((r.stderr.decode("utf-8", "replace") or "kdeconnect-cli: ошибка").strip()[-300:])
        return out

    def ensure_daemon(self):
        """kdeconnect-cli needs the KDE Connect app running; start its tray app quietly if it is not."""
        import psutil
        names = {(p.info["name"] or "").lower() for p in psutil.process_iter(["name"])}
        if names & {"kdeconnectd.exe", "kdeconnect-indicator.exe", "kdeconnect-app.exe"}:
            return True
        d = self.bin_dir()
        if d and (d / "kdeconnect-indicator.exe").exists():
            subprocess.Popen([str(d / "kdeconnect-indicator.exe")], creationflags=CREATE_NO_WINDOW, cwd=str(d))
            time.sleep(3)
            return True
        return False

    def refresh_devices(self):
        known = self.cli("-l", "--id-name-only")
        avail = {l.split(" ", 1)[0] for l in self.cli("-a", "--id-name-only").splitlines() if l.strip()}
        self.devices = [{"id": l.split(" ", 1)[0], "name": (l.split(" ", 1) + [""])[1].strip(), "reachable": l.split(" ", 1)[0] in avail}
                        for l in known.splitlines() if l.strip()]
        self._dev_t = time.time()
        return self.devices

    def device(self):
        want = self.cfg.get("phone.device")
        reach = [d for d in self.devices if d["reachable"]]
        return next((d for d in reach if d["id"] == want), None) or (reach[0] if reach else None)

    def _dev(self):
        if time.time() - self._dev_t > 30 or not self.device():
            self.refresh_devices()
        d = self.device()
        if not d:
            raise RuntimeError("телефон не в сети: откройте KDE Connect на телефоне, он должен быть в той же Wi-Fi")
        return d["id"]

    # ---------------------------------------------------------------- notifications
    def start(self):
        threading.Thread(target=self._loop, daemon=True, name="phone").start()
        self.bus.start()

    def _loop(self):
        time.sleep(30)
        started = False
        while True:
            time.sleep(5)
            if not self.cfg.get("phone.enabled", True) or not self.bin_dir():
                continue
            try:
                if not started:
                    started = self.ensure_daemon()
                if time.time() - self._dev_t > 60:
                    self.refresh_devices()
                d = self.device()
                if not d:
                    self.seen = None
                    # a VPN (Red Shield, WireGuard) may drop connections coming from the LAN but let through the answer
                    # to a recent outgoing packet: keep knocking on the phone so its reply finds the door open
                    if self.cfg.get("phone.ip") and time.time() - self._knock_t > 20:
                        self._knock_t = time.time()
                        self.cli("--refresh")
                        time.sleep(3)
                        self.refresh_devices()
                    continue
                pass                      # notifications come by D-Bus (KdeBus); its errors stay in last_error
            except Exception as e:
                self.last_error = str(e)[:200]

    def _from_bus(self, d, updated):
        """A notification the phone posted (D-Bus signal from KDE Connect): appName, title (sender), text, silent."""
        if not self.cfg.get("phone.enabled", True) or not (self.cfg.get("phone.announce", True) or self.cfg.get("phone.calls", True)):
            return
        nid, app = str(d.get("internalId") or d.get("id") or ""), str(d.get("appName") or "").strip()
        title, text = self._clean(str(d.get("title") or "")), self._clean(str(d.get("text") or ""))
        if not app or not (title or text):
            return
        if self._last_text.get(nid) == (title, text):
            return                       # seen already (by the signal or the 3 s check), or re-sent without changes
        self._last_text[nid] = (title, text)
        body = f"{title}: {text}" if title and title.lower() != app.lower() and text else (text or title)
        self._remember_reply(d, live=True)
        self._new(f"{app}: {body}", quiet=bool(d.get("silent")))

    @staticmethod
    def _clean(s):
        """MAX and some others send HTML: "<b>Галина</b> Привет<br/><b>Михаил</b> Хорошо" was read aloud with the tags.
        -> "Галина: Привет. Михаил: Хорошо" (a group chat's authors become "Name:", line breaks become sentences)."""
        import html
        s = re.sub(r"(?is)<b>(.*?)</b>\s*", r"\1: ", s)
        s = re.sub(r"(?i)<br\s*/?>|</p>|</div>", "\n", s)
        s = html.unescape(re.sub(r"<[^>]+>", "", s))
        lines, out = [x.strip(" :") for x in s.split("\n")], []
        for ln in lines:
            if ln and (not out or not out[-1].endswith(ln)):   # a group summary repeats a line: "…: Бараненков / Бараненков"
                out.append(ln)
        s = ". ".join(out)
        s = re.sub(r"([.!?…])\.\s", r"\1 ", s)
        return re.sub(r"\s{2,}", " ", s).strip()

    def last_text(self, max_age_min=30):
        """The newest phone notification that is not just noise, for "прочитай уведомление": (app, text) or None."""
        for x in self.recent:
            if x.get("call") or x.get("rule") == "silent":
                continue
            try:
                h, mnt = map(int, x["at"].split(":"))
                age = (dt.datetime.now() - dt.datetime.now().replace(hour=h, minute=mnt)).total_seconds() / 60 % 1440
            except Exception:
                age = 0
            return (x["app"], x["text"]) if age <= max_age_min else None
        return None

    def _remember_reply(self, d, live=False):
        """A message Jarvis can answer by voice: the phone offers "Reply" on it (replyId), or it is an SMS
        (answered by SMS to the sender's number from the contacts)."""
        app = EMOJI_RX.sub("", str(d.get("appName") or "")).strip()
        title = EMOJI_RX.sub("", str(d.get("title") or "")).strip()
        rid = str(d.get("replyId") or "")
        sms = self._is(app, SMS_APPS)
        if not (rid or sms) or not title or title.lower() == app.lower() or SMS_SYSTEM_RX.search(title):
            return
        nid = str(d.get("internalId") or d.get("id") or "")
        for r in list(self.repliable):
            if r.get("nid") == nid:
                self.repliable.remove(r)
        self.repliable.appendleft({"app": "SMS" if sms else app, "who": title, "text": str(d.get("text") or ""),
                                   "reply_id": rid, "sms": sms and not rid, "nid": nid, "t": time.time(),
                                   "live": live})   # live: arrived while Jarvis was on (and was announced)

    @staticmethod
    def _gent(name):
        """"Иван Петров" -> "Ивана Петрова" for "от …"; anything that is not a person's name stays as it is."""
        from .nlu import morph
        m = morph()
        words = name.split()
        if not m or not 0 < len(words) <= 3:
            return name
        out = []
        for w in words:
            ps = m.parse(w)
            p = next((p for p in ps if {"Name", "Surn", "Patr"} & set(str(p.tag).replace(" ", ",").split(","))), None)
            if p is None and len(words) == 1 and ps and ps[0].tag.POS == "NOUN" and "nomn" in ps[0].tag:
                p = ps[0]                 # "Мама" -> "мамы", "Шеф" -> "шефа": contacts saved by role
            g = p.inflect({"gent"}) if p else None
            if not g:
                return name
            out.append(g.word.capitalize() if w[:1].isupper() else g.word)
        return " ".join(out)

    @staticmethod
    def _is(app, names):
        a = app.lower()
        return any(n.lower() == a or (len(n) > 3 and n.lower() in a) for n in names)

    def rule_for(self, app, text):
        """(rule, why): an explicit rule for the app wins; then the noise filter; then messengers / everything else."""
        rules = {k.lower(): v for k, v in (self.cfg.get("phone.rules") or {}).items()}
        if app.lower() in rules:
            return rules[app.lower()], "правило"
        if self._is(app, self.cfg.get("phone.mute") or MUTE_DEFAULT):
            return "silent", "системное"
        if self.cfg.get("phone.filter", True) and (self._is(app, NOISE_APPS) or NOISE_RX.search(text)
                                                   or (not self._is(app, MESSENGERS) and PROMO_RX.search(text))):
            return "silent", "неважное"
        if self._is(app, MESSENGERS):
            return self.cfg.get("phone.messenger_rule", "ask"), "мессенджер"
        return self.cfg.get("phone.default_rule", "name"), ""

    def _new(self, line, quiet=False):
        app, _, text = line.partition(": ")
        app, text = EMOJI_RX.sub("", app).strip(), re.sub(r"\s{2,}", " ", EMOJI_RX.sub("", text)).strip()
        missed = bool(MISSED_RX.search(line)) or (app.lower().startswith(DIALERS) and bool(re.search(r"(?i)пропущен", text)))
        call = not missed and (CALL_RX.search(line) or (app.lower().startswith(DIALERS) and text))
        # the phone's "silent" mark is not used: messengers set it when they add a message to a chat's notification
        rule, why = ("call", "") if call else self.rule_for(app, text)
        item = {"app": app, "text": text, "at": dt.datetime.now().strftime("%H:%M"), "call": bool(call), "rule": rule, "why": why}
        self.recent.appendleft(item)
        self.core.emit("phone", item=item)
        if call:
            if self.cfg.get("phone.calls", True):
                src = line if CALL_RX.search(app) else text      # "Входящий вызов: Мама" or "Телефон: Входящий вызов Мама"
                who = re.sub(r"(?i)входящ\w*\s+(?:вызов|звонок)\s*(?:от\s+)?[:•·-]?\s*|incoming call\s*(?:from\s+)?[:•-]?\s*|звонит\s*", "",
                             src).strip(" :•·-") or "кто-то"
                self.core.initiative.propose(f"call:{who}", f"Звонит {who}.", priority=4, title="Телефон", cooldown_min=1, kind="phone")
            return
        if missed:
            if self.cfg.get("phone.calls", True):
                who = re.sub(r"(?i)пропущенн\w*\s+(?:вызов|звонок)\w*\s*(?:от\s+)?|missed\s+call\s*(?:from\s+)?", "",
                             text.partition(": ")[0] if ": " in text else text).strip(" :•·-")
                if len(re.sub(r"\D", "", who)) >= 5:
                    name = self.contact_for_number(who)
                    who = name or f"номера, оканчивающегося на {self._last_digits(who)}"
                self.core.initiative.propose(f"missed:{who}", f"Пропущенный звонок от {self._gent(who)}." if who
                                             else "Пропущенный звонок.", priority=3, title="Телефон", cooldown_min=5, kind="phone")
            return
        if rule == "silent" or not self.cfg.get("phone.announce", True):
            return
        # "Иван: привет" -> the sender is the part before the first colon, when there is one
        who, _, body = text.partition(": ") if ": " in text[:60] else ("", "", text)
        messenger = self._is(app, MESSENGERS) and not (self._is(app, SMS_APPS) and SMS_SYSTEM_RX.search(who or text))
        if not messenger:   # a game's "Играй сейчас" or a bank's "Приложение устарело" is a title, not a sender
            who, body = "", text.replace(": ", ". ", 1)
        frm = f" от {self._gent(who.strip())}" if who.strip() else ""
        if who.strip() and len(re.sub(r"\D", "", who)) >= 5:
            # an unknown number: "от 79615857349" was read as "семь миллиардов…"; a contact's name, or the last digits
            name = self.contact_for_number(who)
            frm = f" от {self._gent(name)}" if name else f" с номера, оканчивающегося на {self._last_digits(who)}"
        # priority 3: the user chose these rules, so they are said even at "говорить первым: о важном" (2 was only
        # shown, and with no toast nothing happened at all). One sender: once per 30 s — a chat sends several updates
        key = f"phone:{app.lower()}:{who.strip().lower()}"
        if rule == "read":
            self.core.initiative.propose(f"phone:{line}", f"{app}{frm}: {body}", priority=3, title=f"Телефон · {app}", cooldown_min=0, kind="phone")
        elif rule == "ask":
            # the text stays on the phone until "да": no toast with it either
            self.core.initiative.propose(key, f"SMS{frm}. Прочитать?" if messenger and self._is(app, SMS_APPS) else
                                         f"Сообщение в {app}{frm}. Прочитать?" if messenger else
                                         f"Уведомление от {app}. Прочитать?", priority=3, title=f"Телефон · {app}",
                                         on_yes=lambda: self.core.say(body or "Текста нет."), cooldown_min=0.5, toast=False, kind="phone")
        else:
            self.core.initiative.propose(key, f"На телефоне SMS{frm}." if messenger and self._is(app, SMS_APPS) else
                                         f"На телефоне сообщение в {app}{frm}." if messenger
                                         else f"На телефоне уведомление от {app}.", priority=3, title=f"Телефон · {app}",
                                         cooldown_min=0.5, toast=False, kind="phone")

    # ---------------------------------------------------------------- actions
    def _ring_once(self, dev, tries=3):
        """One ring that survives a dropped link: HyperOS/MIUI puts KDE Connect to sleep, the phone's "findmyphone"
        then vanishes from the bus for a few seconds ("No such object path"). Reconnect and try again."""
        last = None
        for i in range(tries):
            try:
                out = self.cli("-d", dev, "--ring")
                if not re.search(r"(?i)error|no such object|not reachable|не на связи", out):
                    # HyperOS keeps "Find my phone" silent until its notification is tapped; a ping is an ordinary
                    # notification with the phone's notification sound, so something is heard every cycle
                    try:
                        self.cli("-d", dev, "--ping-msg", "Джарвис ищет телефон", timeout=8)
                    except Exception:
                        pass
                    return True
                last = out.strip()
            except Exception as e:
                last = str(e)
            if i < tries - 1:
                try:
                    self.cli("--refresh", timeout=8)          # look for the phone again and re-establish the link
                except Exception:
                    pass
                time.sleep(3)
                self._dev_t = 0                              # the device list is stale now
        raise RuntimeError(last or "телефон не отвечает")

    def ring(self):
        """"Найди телефон": one ring is a short beep on many phones (MIUI) — not enough to find it under a cushion.
        It rings again every 8 s for up to 2 minutes, until "нашёл" / "стоп" (no name needed while searching)."""
        if self.searching:
            return "Уже звоню на телефон. Скажите «нашёл», когда найдёте."
        try:
            dev = self._dev()
            self._ring_once(dev)                      # the first ring right away: a failure is told to the user
        except Exception as e:
            self.core.log(f"Поиск телефона: {e}", "warn")
            return ("Телефон сейчас не на связи с KDE Connect, сэр: похоже, система телефона его усыпила. "
                    "Откройте KDE Connect на телефоне или разрешите ему работать в фоне без ограничений.")
        self.searching = True
        gen = self.core.cancel_gen

        def loop():
            t0, fails, told = time.time(), 0, False
            try:
                while self.searching and gen == self.core.cancel_gen and time.time() - t0 < 120:
                    for _ in range(16):
                        if not self.searching or gen != self.core.cancel_gen:
                            return
                        time.sleep(0.5)
                    try:
                        self._ring_once(dev, tries=2)
                        fails = 0
                    except Exception as e:      # a dropped link is not the end of the search: keep trying
                        fails += 1
                        self.core.log(f"Поиск телефона: {e}", "warn")
                        if fails >= 3 and not told:
                            told = True
                            self.core.say("Телефон пропал со связи, сэр. Продолжаю пытаться.")
            finally:
                self.searching = False
        threading.Thread(target=loop, daemon=True, name="find-phone").start()
        return "Звоню на телефон. Скажите «нашёл», когда найдёте. Если не слышно — «найди телефон через гугл»."

    def find_google(self):
        """Google's Find My Device rings at full volume even on silent: the sure way when KDE Connect only posts
        a notification. The user presses "Прозвонить" on the page (signed in with the phone's Google account)."""
        import webbrowser
        webbrowser.open("https://www.google.com/android/find")
        return "Открыл «Найти устройство» от Google, сэр. Нажмите «Прозвонить» — телефон зазвонит на полную громкость, даже на беззвучном."

    def stop_search(self):
        was, self.searching = self.searching, False
        return was

    def rpc_phone_find(self, on=True):
        """The quick panel / Settings button: start or stop ringing the phone."""
        return self.ring() if on else ("Перестал звонить." if self.stop_search() else "Не звонил.")

    def share_text(self, text):
        text = text.strip()
        if re.match(r"^https?://\S+$", text):
            self.cli("-d", self._dev(), "--share", text)       # a link opens on the phone
            return "Открыл ссылку на телефоне."
        self.cli("-d", self._dev(), "--share-text", text)
        return "Отправил на телефон."

    def share_file(self, path):
        self.cli("-d", self._dev(), "--share", str(path), timeout=120)
        return f"Отправил файл {Path(path).name} на телефон."

    def ping(self, text):
        self.cli("-d", self._dev(), "--ping-msg", text)
        return "Отправил уведомление на телефон."

    def photo(self):
        folder = Path.home() / "Pictures" / "Jarvis"
        folder.mkdir(parents=True, exist_ok=True)
        p = folder / f"phone_{dt.datetime.now():%Y-%m-%d_%H%M%S}.jpg"
        self.cli("-d", self._dev(), "--photo", str(p), timeout=60)
        for _ in range(40):            # the phone takes the picture, then the file arrives
            if p.exists() and p.stat().st_size > 0:
                break
            time.sleep(0.5)
        return p

    def number_for(self, who):
        """'мама' -> '+7…' from Settings → Телефон → контакты для SMS (name = number), or a number said as is."""
        from rapidfuzz import fuzz
        digits = re.sub(r"[^\d+]", "", who)
        if len(digits) >= 5:
            return digits, who
        book = self.cfg.get("phone.contacts") or {}
        best = max(book.items(), key=lambda kv: fuzz.ratio(who.lower(), kv[0].lower()), default=None)
        if best and fuzz.ratio(who.lower(), best[0].lower()) >= 70:
            return best[1], best[0]
        return None, who

    def contact_for_number(self, number):
        """"+7 961 585-73-49" -> the name it is saved under in Settings → Телефон → контакты, or None."""
        digits = re.sub(r"\D", "", number)[-10:]
        for name, num in (self.cfg.get("phone.contacts") or {}).items():
            if digits and re.sub(r"\D", "", str(num))[-10:] == digits:
                return self._nominative(name)
        return None

    @staticmethod
    def _nominative(name):
        """Contacts are often saved the way they were said: "маме" -> "мама" (then "от мамы", "маме")."""
        from .nlu import morph
        m = morph()
        if m and len(name.split()) == 1:
            ps = m.parse(name)
            if ps and ps[0].tag.POS == "NOUN":
                return ps[0].normal_form
        return name

    @staticmethod
    def _last_digits(number):
        """The end of a number, said in pairs: "…73 49" reads "семьдесят три сорок девять"."""
        d = re.sub(r"\D", "", number)[-4:]
        return f"{d[:2]} {d[2:]}" if len(d) == 4 else d

    def sms(self, number, text):
        self.cli("-d", self._dev(), "--send-sms", text, "--destination", number)
        return "SMS отправлено."

    def call(self, who):
        """KDE Connect cannot place a call by itself: it opens the phone's dialer with the number (a tel: link),
        and the call starts with one tap on the phone."""
        number, name = self.number_for(who)
        if not number:
            raise LookupError(f"не знаю номер «{who}»")
        self.cli("-d", self._dev(), "--share", "tel:" + re.sub(r"[^\d+]", "", number))
        return f"Набираю {name} на телефоне. Нажмите «позвонить»."

    def tool_phone(self, a):
        act = (a.get("action") or "").lower()
        to, text = str(a.get("to") or ""), str(a.get("text") or "")
        if act == "call":
            return self.call(to)
        if act == "sms":
            number, name = self.number_for(to)
            if not number:
                raise LookupError(f"не знаю номер «{to}»: его можно добавить в Настройки → Телефон → контакты")
            # never sent straight from the AI: the user says "да" first (the AI once sent one on its own)
            self.core.ask_confirm(lambda: self.core.say(self.sms(number, text)), f"Отправить SMS {name}: «{text}»?")
            return "спросил подтверждение — отправлю после «да»"
        if act == "reply":
            target = self.reply_target(to)
            if not target:
                raise LookupError("нечему отвечать: нет свежих сообщений с кнопкой «Ответить»" + (f" от «{to}»" if to else ""))
            self.ask_reply(target, text)
            return "спросил подтверждение — отвечу после «да»"
        if act == "ring":
            return self.ring()
        if act == "share":
            return self.share_text(text or to)
        if act == "photo":
            return str(self.photo())
        if act == "status":
            d = self.device() if self.devices else None
            return {"подключён": bool(d), "телефон": (d or {}).get("name"), "контакты": sorted((self.cfg.get("phone.contacts") or {}))}
        raise ValueError("action: call | sms | reply | ring | share | photo | status")

    # ---------------------------------------------------------------- answering a message by voice
    def reply_target(self, who="", max_age=1800, live_only=False):
        """The message to answer: the latest one (half an hour at most), or the latest from "who" ("Ване" ~ "Ваня").
        live_only: skip what was already in the phone's shade when Jarvis started (an old SMS is not "just now")."""
        fresh = [r for r in self.repliable if time.time() - r["t"] < max_age and (r.get("live") or not live_only)]
        if not who:
            return fresh[0] if fresh else None
        from rapidfuzz import fuzz
        w = who.lower().replace("ё", "е")
        stem = w[:max(3, len(w) - 2)]          # "ване" / "ваню" / "ваня" -> "ван"
        best, score = None, 0
        for r in fresh:
            name = r["who"].lower().replace("ё", "е")
            s = 100 if stem in name else fuzz.partial_ratio(w, name)
            if s > score:
                best, score = r, s
        return best if score >= 75 else None

    def ask_reply(self, target, text):
        """Never sent without "да": speech recognition slips, and the message goes out in the user's name."""
        text = text.strip().strip("«»\"").strip()
        if not text:
            raise ValueError("что ответить?")
        dev = (self.device() or {}).get("id")
        if not dev:
            raise RuntimeError("телефон не подключён")

        number = None
        if not target.get("reply_id"):
            number, _ = self.number_for(target["who"])
            if not number:
                raise LookupError(f"не знаю номер «{target['who']}»: добавьте его в Настройки → Телефон → контакты для SMS")

        def send():
            try:
                if target.get("reply_id"):
                    self.bus.send_reply(dev, target["reply_id"], text)
                else:
                    self.sms(number, text)
                self.core.say(f"Ответил {self._to(target['who'])}." if target.get("reply_id")
                              else f"Отправил SMS {self._to(target['who'])}.")
            except Exception as e:
                self.core.say(f"Не получилось отправить: {e}")
        # an SMS goes to the number as a new message: "Отправить SMS маме", not "Ответить" (the user just wants to write)
        self.core.ask_confirm(send, f"Ответить {self._to(target['who'])} в {target['app']}: «{text}»?" if target.get("reply_id")
                              else f"Отправить SMS {self._to(target['who'])}: «{text}»?")

    def _to(self, who):
        """Whom, for speech: "Ване", "маме" for a saved number, "на номер, оканчивающийся на 73 49" for an unknown one."""
        if len(re.sub(r"\D", "", who)) >= 5:
            name = self.contact_for_number(who)
            return self._dat(name) if name else f"на номер, оканчивающийся на {self._last_digits(who)}"
        return self._dat(who)

    @staticmethod
    def _dat(name):
        """"Ваня" -> "Ване" for "ответить …"; anything that is not a person's name stays as it is."""
        from .nlu import morph
        m = morph()
        words = name.split()
        if not m or not 0 < len(words) <= 3:
            return name
        out = []
        for w in words:
            ps = m.parse(w)
            p = next((p for p in ps if {"Name", "Surn", "Patr"} & set(str(p.tag).replace(" ", ",").split(","))), None)
            if p is None and len(words) == 1 and ps and ps[0].tag.POS == "NOUN" and "nomn" in ps[0].tag:
                p = ps[0]                 # "Мама" -> "маме", "Шеф" -> "шефу": contacts saved by role
            g = p.inflect({"datv"}) if p else None
            if not g:
                return name
            out.append(g.word.capitalize() if w[:1].isupper() else g.word)
        return " ".join(out)

    # ---------------------------------------------------------------- connecting by IP
    @staticmethod
    def lan_ips():
        """This PC's addresses in the home network (VPN adapters skipped): what to type on the phone."""
        import psutil
        skip = ("vpn", "radmin", "redlink", "wireguard", "tun", "tap", "vethernet", "loopback", "virtual", "hamachi", "zerotier")
        stats = psutil.net_if_stats()
        out = []
        for name, addrs in psutil.net_if_addrs().items():
            if any(s in name.lower() for s in skip) or not (stats.get(name) and stats[name].isup):
                continue
            for a in addrs:
                if a.family.name == "AF_INET" and re.match(r"^(192\.168\.|10\.|172\.(1[6-9]|2\d|3[01])\.)", a.address):
                    out.append(a.address)
        return sorted(out, key=lambda ip: not ip.startswith("192.168."))

    def set_phone_ip(self, ip):
        """Discovery by broadcast fails behind some routers and VPNs (Radmin, WireGuard): KDE Connect then knocks on
        the phone's address directly (customDevices in its config). Takes effect after its service restarts."""
        ip = ip.strip()
        if not re.match(r"^\d{1,3}(\.\d{1,3}){3}$", ip):
            raise ValueError("нужен IP вида 192.168.1.105")
        p = Path(os.environ.get("LOCALAPPDATA", "")) / "kdeconnect" / "config"
        p.parent.mkdir(parents=True, exist_ok=True)
        lines = p.read_text("utf-8").splitlines() if p.exists() else ["[General]"]
        lines = [l for l in lines if not l.startswith("customDevices=")]
        if "[General]" not in lines:
            lines.insert(0, "[General]")
        lines.insert(lines.index("[General]") + 1, f"customDevices={ip}")
        p.write_text("\n".join(lines) + "\n", "utf-8")
        self.cfg.set("phone.ip", ip)
        self.restart_kde()
        return True

    def restart_kde(self):
        """Only KDE Connect's own background processes; its settings window, if open, reconnects by itself."""
        import psutil
        d = self.bin_dir()
        for p in psutil.process_iter(["name", "exe"]):
            if (p.info["name"] or "").lower() in ("kdeconnectd.exe", "kdeconnect-indicator.exe", "dbus-daemon.exe") and \
                    d and str(d).lower() in (p.info["exe"] or "").lower():
                try:
                    p.terminate()
                except Exception:
                    pass
        time.sleep(2)
        self.seen, self._dev_t = None, 0
        return self.ensure_daemon()

    # ---------------------------------------------------------------- UI
    def rpc_phone_ip(self, ip):
        self.set_phone_ip(ip)
        time.sleep(6)
        try:
            self.cli("--refresh")
        except Exception:
            pass
        time.sleep(4)
        return self.rpc_phone()

    def rpc_phone(self):
        installed = bool(self.bin_dir())
        if installed and time.time() - self._dev_t > 10:
            try:
                self.ensure_daemon()
                self.refresh_devices()
            except Exception as e:
                self.last_error = str(e)[:200]
        g = lambda k, d: self.cfg.get("phone." + k, d)
        return {"installed": installed, "devices": self.devices, "device": (self.device() or {}).get("id"),
                "error": self.last_error, "recent": list(self.recent)[:15], "pc_ips": self.lan_ips(), "phone_ip": g("ip", ""),
                "settings": {"enabled": g("enabled", True), "announce": g("announce", True), "read_text": g("read_text", False),
                             "calls": g("calls", True), "filter": g("filter", True), "messenger_rule": g("messenger_rule", "ask"),
                             "default_rule": g("default_rule", "name"), "rules": g("rules", {}), "contacts": g("contacts", {})},
                "rule_titles": RULES, "apps": self.known_apps()}

    def known_apps(self):
        """Apps to set rules for: popular ones, the ones with a rule and the ones seen on the phone lately; messengers first."""
        seen = {x["app"] for x in self.recent if not x.get("call")}
        apps = set(POPULAR) | set(self.cfg.get("phone.rules") or {}) | seen
        uniq = {}
        for a in apps:                    # "telegram" from a voice command and "Telegram" from the phone are one app
            uniq.setdefault(a.lower(), a)
        return sorted(uniq.values(), key=lambda a: (not self._is(a, MESSENGERS), a.lower()))

    def rpc_phone_rule(self, app, rule):
        rules = dict(self.cfg.get("phone.rules") or {})
        if rule in RULES:
            rules[app] = rule
        else:
            rules.pop(app, None)       # "auto": back to the messenger / default rule and the noise filter
        self.cfg.set("phone.rules", rules)
        return self.rpc_phone()

    def rpc_phone_set(self, **kw):
        for k in ("enabled", "announce", "calls", "filter", "messenger_rule", "default_rule", "contacts", "device"):
            if k in kw:
                self.cfg.set("phone." + k, kw[k])
        return self.rpc_phone()

    def rpc_phone_action(self, action, text=""):
        if action == "ring":
            return self.ring()
        if action == "clipboard":
            from . import system as S
            return self.share_text(S.clipboard_get() or "")
        if action == "text":
            return self.share_text(text)
        if action == "photo":
            p = self.photo()
            os.startfile(p)
            return str(p)
        if action == "file":
            return self.share_file(text)
        raise ValueError("неизвестное действие")

    def rpc_phone_install(self):
        def run(job):
            r = subprocess.run(["winget", "install", "--id", "KDE.KDEConnect", "--exact", "--silent",
                                "--accept-package-agreements", "--accept-source-agreements", "--disable-interactivity"],
                               capture_output=True, timeout=1800, creationflags=CREATE_NO_WINDOW)
            if r.returncode != 0 and not self.bin_dir():
                raise RuntimeError("winget не смог установить KDE Connect (код %s)" % r.returncode)
            self.ensure_daemon()
            return "KDE Connect установлен. Поставьте приложение KDE Connect на телефон и подтвердите сопряжение."
        self.core.background.submit("install", "Устанавливаю KDE Connect", run)
        return True


# ====================================================================== voice rules
def register(R, core):
    PH = lambda: core.phone
    from . import system as S

    def do(fn):
        def go():
            try:
                core.say(fn())
            except Exception as e:
                core.log(f"Телефон: {e}", "warn")
                core.say("Не получилось: " + str(e).split(":")[-1].strip() if "не в сети" in str(e) or "не установлен" in str(e)
                         else "Телефон не ответил. Проверьте, что KDE Connect открыт на телефоне.")
        threading.Thread(target=go, daemon=True).start()
        return True

    PHONE = r"(?:телефон|телефона|телефону|мобильн\w+|смартфон\w*)"

    @R(rf"^(?:найди|где|позвони\s+на|прозвони|звякни\s+на)\s+(?:мой\s+)?{PHONE}$|^(?:я\s+)?потерял\s+телефон$")
    def _ring(m, t):
        return do(PH().ring)

    @R(r"^(?:найди|прозвони|позвони\s+на)\s+(?:мой\s+)?телефон\w*\s+(?:через\s+)?(?:гугл\w*|google)$|^(?:громко\s+)?найди\s+телефон\s+громко$")
    def _find_google(m, t):
        PH().stop_search()
        return PH().find_google()

    @R(r"^(?:я\s+)?(?:нашел|нашла|нашёл)(?:\s+(?:его|телефон|мобильник|трубку))?$|^(?:телефон\s+)?(?:нашелся|нашёлся|нашлась)$|"
       r"^(?:хватит|перестань|прекрати|не\s+надо)\s+(?:звонить|искать)(?:\s+(?:на\s+)?телефон\w*)?$")
    def _found(m, t):
        return "Отлично, сэр." if PH().stop_search() else "Хорошо."

    @R(rf"^(?:отправь|скинь|перекинь|кинь)\s+(?:это|буфер|ссылку|из\s+буфера)\s+на\s+{PHONE}$|^(?:открой)\s+(?:это|ссылку)\s+на\s+{PHONE}$")
    def _clip(m, t):
        return do(lambda: PH().share_text(S.clipboard_get() or ""))

    @R(rf"^(?:отправь|скинь|напиши)\s+на\s+{PHONE}\s+(.+)$")
    def _text(m, t):
        return do(lambda: PH().share_text(m.group(1)))

    @R(rf"^(?:сфоткай|сфотографируй)\s+(?:с\s+)?{PHONE}\w*$|^(?:сделай\s+)?фото\s+(?:с|на)\s+{PHONE}$")
    def _photo(m, t):
        def f():
            p = PH().photo()
            os.startfile(p)
            return "Фото с телефона готово."
        return do(f)

    # "позвони маме" / "набери Ивана" / "позвони на номер 8 900…" — the dialer opens on the phone with the number
    @R(rf"^(?:позвони|набери|звякни|вызови|позвонить)\s+(?!(?:мне\s+)?(?:на\s+)?(?:мой\s+)?{PHONE}\b|мне\b)(?:на\s+номер\s+)?(.+?)$")
    def _call(m, t):
        who = m.group(1).strip()
        if re.search(r"\s(?:в|по|через)\s+(?:телеграм\w*|ватсап\w*|whatsapp|telegram|дискорд\w*|discord|вк|вайбер\w*|макс\w*)$", who):
            return False          # a messenger call is the AI's job
        number, name = PH().number_for(who)
        if not number:
            return (f"Не знаю номер «{who}». Добавьте его: Настройки → Телефон → контакты, строкой «{who} = +7…». "
                    f"Или скажите номер: «позвони на номер …».")
        return do(lambda: PH().call(who))

    @R(r"^(?:напиши|отправь|скинь)\s+(?:смс|sms|эсэмэску)\s+(.+?)\s*(?:что|:)\s+(.+)$|^(?:напиши|отправь)\s+(?:смс|sms)\s+(?:на\s+номер\s+)?([\d +()-]{5,})\s+(.+)$")
    def _sms(m, t):
        who, text = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        number, name = PH().number_for(who.strip())
        if not number:
            return f"Не знаю номер «{who}». Добавьте его в Настройки → Телефон → контакты для SMS."
        core.ask_confirm(lambda: do(lambda: PH().sms(number, text)), f"Отправить SMS {name}: «{text}»?")
        return True

    @R(r"^(?:напиши|ответь|отправь|скажи)\s+([а-яёa-z-]{3,})\s+(?:(в|на|с|через)\s+телефон\w*\s+|((?:в\s+|по\s+)?(?:смс\w*|sms|эсэмэс\w*|смск\w*))\s+)?(?:что\s+)?(.+)$")
    def _write_back(m, t):
        """"напиши маме хорошо": mum has just written to the phone, so this is the answer to her, on the phone.
        Without a fresh message from that person it stays the Telegram / Discord command on the PC — unless the
        phone was named ("напиши маме в телефоне хорошо")."""
        who, phone_said, text = m.group(1), bool(m.group(2) or m.group(3)), m.group(4).strip()
        ph = PH()
        if who.lower() in ("ему", "ей", "им", "мне", "ответ", "туда", "сюда"):
            return False
        # "напиши маме …" is the Telegram command on the PC, unless mum has JUST written to the phone (3 min)
        # or the phone was named: it took over every "напиши маме" for half an hour, even with an old SMS
        sms_said = bool(m.group(3))
        target = None if sms_said else (ph.reply_target(who) if phone_said else ph.reply_target(who, max_age=180, live_only=True))
        if not target and not phone_said:
            return False
        if not target:
            number, name = ph.number_for(who)
            if not number:
                return f"На телефоне нет свежего сообщения от «{who}», и номера в контактах нет."
            core.ask_confirm(lambda: do(lambda: ph.sms(number, text)), f"Отправить SMS {ph._dat(name)}: «{text}»?")
            return True
        try:
            ph.ask_reply(target, text)
        except Exception as e:
            return f"Не получилось: {e}"
        return True

    @R(r"^(?:ответь|ответить|напиши\s+в\s+ответ)(?:\s+(?!на\s+(?:это\s+|последнее\s+)?письмо|на\s+выделен|на\s+вопрос)(.+))?$")
    def _reply(m, t):
        """"ответь, что буду через 10 минут", "ответь Ване: ок" — the answer goes into the messenger on the phone."""
        ph, rest = PH(), (m.group(1) or "").strip(" ,:")
        if not ph.repliable:
            return False                  # nothing from the phone to answer: maybe it is about mail or the AI
        who = ""
        mm = re.match(r"^(?:ему|ей|им|в\s+ответ)(?:[\s,:]+|$)", rest)
        if mm:
            rest = rest[mm.end():]
        else:                             # "ответь ване что буду" — a sender's name first?
            first = re.match(r"^([а-яёa-z-]{3,})(?:\s+|$)", rest, re.I)
            if first and ph.reply_target(first.group(1)):
                who, rest = first.group(1), rest[first.end():]
        text = re.sub(r"^(?:что|мол|:)\s*", "", rest.strip(" ,:"), flags=re.I).strip()
        target = ph.reply_target(who)
        if not target:
            return False
        mail = getattr(core, "mail", None)
        if mail is not None and getattr(mail, "current", None) and getattr(mail, "current_at", 0) > target["t"]:
            return False                  # the e-mail was read after this message: "ответь" is about the e-mail
        if not text:
            def take(raw):
                try:
                    ph.ask_reply(target, raw)
                except Exception as e:
                    core.say(f"Не получилось: {e}")
            core.say(f"Что ответить {ph._dat(target['who'])}?")
            core.capture = (take, time.time() + 25)
            core.listen_next(25)
            return True
        try:
            ph.ask_reply(target, text)
        except Exception as e:
            return f"Не получилось: {e}"
        return True

    @R(r"^(?:прочитай|зачитай|озвучь|прочти)\s+(?:последнее\s+|это\s+|новое\s+|пришедшее\s+)?(?:уведомлени\w*|сообщени\w*|смс\w*|sms|смску)$|"
       r"^что\s+(?:там\s+)?(?:пришло|написали|за\s+(?:уведомление|сообщение|смс))$|^кто\s+(?:там\s+)?(?:написал|пишет)$")
    def _read_last(m, t):
        last = PH().last_text()
        if not last:
            return False                  # nothing fresh from the phone: maybe mail, the screen or the AI
        app, text = last
        return f"{app}: {text}" if text else f"Уведомление от {app}, без текста."

    @R(rf"^(?:что|какие\s+уведомления)\s+(?:нового\s+)?на\s+{PHONE}$|^(?:прочитай\s+)?уведомления\s+(?:с\s+)?{PHONE}$")
    def _list(m, t):
        r = [x for x in PH().recent if x.get("rule") != "silent"][:5]
        if not r:
            return "Новых уведомлений с телефона не было."
        # "спросить" apps: only where it came from, the text stays private
        return "С телефона: " + "; ".join(x["app"] if x.get("rule") in ("ask", "name") else f"{x['app']}: {x['text']}" for x in r) + "."

    @R(rf"^(?:не\s+читай|перестань\s+читать)\s+уведомления\s+(?:с\s+)?{PHONE}$|^(?:читай)\s+уведомления\s+(?:с\s+)?{PHONE}$")
    def _toggle(m, t):
        on = not t.startswith(("не", "перестань"))
        core.cfg.set("phone.default_rule", "read" if on else "name")
        return "Хорошо, буду читать уведомления с телефона сразу." if on else "Хорошо, только скажу, от какого приложения уведомление."

    @R(r"^(?:не\s+говори|молчи)\s+(?:про|о|об)\s+(?:уведомлени\w+|сообщени\w+)\s+(?:из|от|в)\s+(.+)$"
       r"|^(читай|спрашивай\s+(?:про|о))\s+(?:сообщени\w+|уведомлени\w+)\s+(?:из|от|в)\s+(.+)$")
    def _app_rule(m, t):
        app = (m.group(1) or m.group(3)).strip()
        rule = "silent" if m.group(1) else "read" if m.group(2) == "читай" else "ask"
        known = next((a for a in PH().known_apps() + MESSENGERS if a.lower() == app.lower() or app.lower() in a.lower()), app)
        PH().rpc_phone_rule(known, rule)
        return {"silent": f"Хорошо, про {known} молчу.", "read": f"Буду сразу читать сообщения из {known}.",
                "ask": f"Буду спрашивать, читать ли сообщения из {known}."}[rule]
