"""Web agent: Jarvis browses on his own to do a task — find goods and compare prices, look up sessions and seats,
fill a search form — in a separate invisible Chrome (Playwright), so the user's mouse, keyboard and browser stay
free. "Джарвис, найди мне билеты в кино на вечер на места по центру и скинь ссылку на оплату".

Hard limits, enforced in code and not only asked of the model:
- it never types into password or payment-card fields and never types anything that looks like a card number;
- it never presses "Оплатить / Купить / Подтвердить заказ / Pay / Place order": at that point it stops and hands
  the user the link to finish themselves;
- it does not solve CAPTCHAs or log in: it stops and gives the link;
- a step limit and a time limit.
The result comes as a short spoken answer, links in the chat (and the main one in the clipboard), and an offer to
open it in the user's own browser."""
import json
import re
import threading
import time
from urllib.parse import quote_plus

from .llm import complete

PAY_RX = re.compile(r"(?i)\b(?:оплат\w*|оплачу|