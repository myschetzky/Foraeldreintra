"""Asynkron klient til ForældreIntra (*.m.skoleintra.dk).

Ingen afhængighed af Home Assistant — kan køres direkte via tools/probe.py.

Login-flowet følger browseren: GET /Account/IdpLogin -> udfyld UserName/Password
(RoleType=Parent) -> auto-submit SAML-formularer -> /parent/<id>/<navn>/Index.
Sessionen er en cookie; når den udløber, sender skolen os tilbage til login-siden.
Det opdages på hvert kald, og så logger klienten ind igen én gang og gentager kaldet.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import urljoin, urlsplit

import aiohttp

from . import parsers
from .errors import AuthError, BlockedError, LayoutError, ManualActionRequired, TransientError
from .models import CalendarItem, Child, ContactNote, HomeworkItem, MessageThread, WeekPlan

_LOGGER = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (HomeAssistant; skoleintra-integration) like Gecko"
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30, connect=10)
MIN_REQUEST_GAP = 1.0  # sekunder mellem kald — vi er en høflig gæst
MAX_LOGIN_STEPS = 8


def normalize_school_url(url: str) -> str:
    """'vinderoedprivatskole' eller en hel URL -> 'https://vinderoedprivatskole.m.skoleintra.dk'."""
    url = (url or "").strip().rstrip("/")
    if not url.startswith("http"):
        url = "https://" + url
    netloc = urlsplit(url).netloc
    if "." not in netloc:
        netloc = f"{netloc}.m.skoleintra.dk"
    return f"https://{netloc}"


class SkoleIntraClient:
    # Kun https, og adgangskoden sendes kun til *.skoleintra.dk. Slås fra i tests mod en lokal server.
    STRICT = True

    def __init__(
        self,
        session: aiohttp.ClientSession,
        school_url: str,
        username: str,
        password: str,
        login_type: str = "alm",
    ) -> None:
        self._session = session
        self.base_url = normalize_school_url(school_url) if self.STRICT else school_url.rstrip("/")
        self._username = username
        self._password = password
        self._login_type = login_type
        self._lock = asyncio.Lock()
        self._last_request = 0.0
        self._logged_in = False
        self.children: list[Child] = []
        self.request_count = 0
        self.login_count = 0

    # ------------------------------------------------------------------ HTTP

    async def _raw(self, method: str, url: str, data: dict[str, str] | None = None) -> tuple[int, str, str]:
        url = urljoin(self.base_url + "/", url)
        if self.STRICT and not url.startswith("https://"):
            raise TransientError(f"Afviser ukrypteret forbindelse: {urlsplit(url).netloc}")
        wait = MIN_REQUEST_GAP - (time.monotonic() - self._last_request)
        if wait > 0:
            await asyncio.sleep(wait)
        self.request_count += 1
        try:
            async with self._session.request(
                method, url, data=data, timeout=REQUEST_TIMEOUT, allow_redirects=True,
                headers={"User-Agent": USER_AGENT,
                         "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
                         "Accept-Language": "da-DK,da;q=0.9"},
            ) as resp:
                text = await resp.text(errors="replace")
                status, final = resp.status, str(resp.url)
        except TimeoutError as err:
            raise TransientError(f"Timeout ved {method} {urlsplit(url).path}") from err
        except aiohttp.ClientError as err:
            raise TransientError(f"Netværksfejl ved {method} {urlsplit(url).path}: {err}") from err
        finally:
            self._last_request = time.monotonic()

        if "/updating" in urlsplit(final).path:
            raise TransientError("SkoleIntra er i vedligeholdelsestilstand")
        if parsers.looks_blocked(status, text):
            raise BlockedError("Skolens automatiseringsbeskyttelse afviste kaldet")
        if status in (429, 502, 503, 504) or status >= 500:
            raise TransientError(f"HTTP {status} fra {urlsplit(final).path}")
        return status, final, text

    @staticmethod
    def _is_login_page(final_url: str, text: str) -> bool:
        path = urlsplit(final_url).path.lower()
        return (
            "/account/idplogin" in path
            or "login.emu.dk" in final_url
            or parsers.find_login_form(text) is not None
            or parsers.find_sso_form(text) is not None
        )

    async def get(self, path: str) -> str:
        """GET med automatisk genlogin, hvis sessionen er udløbet."""
        async with self._lock:
            if not self._logged_in:
                await self._login()
            status, final, text = await self._raw("GET", path)
            if self._is_login_page(final, text):
                _LOGGER.debug("Session udløbet (landede på %s) — logger ind igen", urlsplit(final).path)
                self._logged_in = False
                await self._login()
                status, final, text = await self._raw("GET", path)
                if self._is_login_page(final, text):
                    # Login lykkedes (ellers var AuthError rejst), så det er ikke adgangskoden.
                    raise LayoutError(f"{path} sender stadig til login efter genlogin")
            if status in (401, 403):
                # Kun denne side — fx en funktion, skolen har slået fra for barnet. Ikke et loginproblem.
                raise LayoutError(f"Ingen adgang (HTTP {status}) til {path}")
            if status == 404:
                raise LayoutError(f"HTTP 404 på {path} (funktionen findes måske ikke på skolen)")
            if status >= 400:
                raise TransientError(f"HTTP {status} på {path}")
            return text

    # ----------------------------------------------------------------- Login

    async def login(self) -> list[Child]:
        async with self._lock:
            await self._login()
        return self.children

    async def _login(self) -> None:
        self.login_count += 1
        status, url, text = await self._raw("GET", "/Account/IdpLogin")
        if status == 404:
            raise AuthError("Ingen /Account/IdpLogin på adressen — er skolens URL korrekt?")

        credentials_sent = False
        for _step in range(MAX_LOGIN_STEPS):
            path = urlsplit(url).path.lower()
            host = urlsplit(url).netloc.lower()

            if "/parent/" in path and not self._is_login_page(url, text):
                self.children = parsers.parse_children(text, url) or self.children
                if not self.children:
                    raise LayoutError("Logget ind, men kunne ikke finde nogen børn i menuen")
                self._logged_in = True
                _LOGGER.debug("Logget ind; børn: %s", [c.name for c in self.children])
                return

            if path.endswith("/confirmcontacts"):
                raise ManualActionRequired(
                    "Skolen beder om bekræftelse af kontaktoplysninger. Log ind i en browser og bekræft — "
                    "integrationen gør det bevidst ikke på dine vegne."
                )

            if form := parsers.find_sso_form(text):
                status, url, text = await self._raw("POST", self._form_target(form, url), parsers.form_inputs(form))
                continue

            if "emu.dk" in host or "unilogin" in host:
                if self._login_type != "uni":
                    raise AuthError("Skolen sendte os til UNI-Login, men login-type er 'alm'.")
                if credentials_sent and (err := parsers.login_error_text(text)):
                    raise AuthError(f"UNI-Login afviste: {err}")
                form = parsers.find_uni_login_form(text)
                if not form or "mitid" in text.lower() and not form.find("input", {"type": "password"}):
                    raise ManualActionRequired("UNI-Login kræver MitID/JavaScript og kan ikke automatiseres.")
                data = parsers.form_inputs(form)
                for name in [i.get("name") for i in form.find_all("input") if i.get("name")]:
                    if name.lower() in ("user", "username"):
                        data[name] = self._username
                    elif name.lower() in ("pass", "password"):
                        data[name] = self._password
                credentials_sent = True
                status, url, text = await self._raw("POST", self._form_target(form, url), data)
                continue

            if form := parsers.find_login_form(text):
                if credentials_sent:
                    raise AuthError(parsers.login_error_text(text) or "Login afvist — tjek brugernavn/adgangskode")
                if self._login_type == "uni":
                    link = next((a["href"] for a in parsers.soup_of(text).find_all("a", href=True)
                                 if "unilogin" in a["href"].lower()), None)
                    if not link:
                        raise AuthError("Login-type 'uni', men skolen viser intet UNI-Login-link")
                    status, url, text = await self._raw("GET", urljoin(url, link))
                    continue
                target = self._form_target(form, url)
                if self.STRICT and not urlsplit(target).netloc.lower().endswith("skoleintra.dk"):
                    raise LayoutError(f"Login-formularen peger på ukendt domæne {urlsplit(target).netloc}")
                data = parsers.form_inputs(form)
                for name in [i.get("name") for i in form.find_all("input") if i.get("name")]:
                    if name.lower() == "username":
                        data[name] = self._username
                    elif name.lower() == "password":
                        data[name] = self._password
                data["RoleType"] = "Parent"  # som browseren, når man vælger "Forælder"
                credentials_sent = True
                status, url, text = await self._raw("POST", target, data)
                continue

            if "mitid" in text.lower() or "nemlog-in" in host:
                raise ManualActionRequired("Login kræver MitID, som ikke kan automatiseres.")
            if err := parsers.login_error_text(text):
                raise AuthError(err)
            break

        raise LayoutError(f"Login-flowet endte et ukendt sted: {urlsplit(url).path}")

    @staticmethod
    def _form_target(form, current_url: str) -> str:
        return urljoin(current_url, form.get("action") or current_url)

    # ------------------------------------------------------------- Datakilder

    async def get_diary(self, child: Child) -> list[HomeworkItem]:
        """Lektiebog (Storebror). Nuværende + næste måned (lektier for de næste uger)."""
        overview = await self.get(f"{child.path}item/weeklyplansandhomework/diary")
        notes_url = parsers.extract_diary_url(overview, child.path)
        if not notes_url:
            try:
                overview = await self.get(f"{child.path}/diaries/list")
                notes_url = parsers.extract_diary_url(overview, child.path)
            except LayoutError:
                notes_url = None
        if not notes_url:
            if "ikke autoriseret" in overview.lower():
                return []
            raise LayoutError("Lektiebog: fandt ikke link til noter (bruger skolen lektiebog for dette barn?)")
        items = parsers.parse_diary(await self.get(notes_url))
        try:
            nxt = parsers.parse_diary(await self.get(notes_url + "/NextMonth"))
        except (LayoutError, TransientError) as err:
            _LOGGER.debug("NextMonth for %s fejlede: %s", child.name, err)
            nxt = []
        # NextMonth-visningen vinder for de dage, den dækker (den er nyest).
        next_days = {i.day for i in nxt}
        merged = [i for i in items if i.day not in next_days] + nxt
        return sorted(merged, key=lambda i: (i.day or date.max, i.subject))

    async def get_weekplans(self, child: Child, today: date | None = None) -> list[WeekPlan]:
        """Ugeplan (Lillesøster): denne og næste uge."""
        today = today or date.today()
        wanted = []
        for offset in (0, 7):
            y, w, _ = (today + timedelta(days=offset)).isocalendar()
            wanted.append(f"{w}-{y}")
        listing = await self.get(f"{child.path}item/weeklyplansandhomework/list")
        available = {p["week_id"]: p["href"] for p in parsers.parse_weekplan_list(listing)}
        if not available and "ikke autoriseret" in listing.lower():
            return []
        plans = []
        for n, wid in enumerate(wanted):
            href = available.get(wid)
            if href is None and available:
                continue  # skolen har ikke lagt den uges plan op endnu
            if href is None:
                # Ingen liste (andet layout?) -> prøv den URL, CavaleriDK/skoleintra bruger direkte.
                w, y = wid.split("-")
                href = f"{child.path}item/weeklyplansandhomework/item/class/{int(w):02d}-{y}"
                try:
                    page = await self.get(href)
                except LayoutError:
                    if n == 0:
                        raise
                    continue  # næste uges plan findes typisk ikke før fredag
            else:
                page = await self.get(href)  # et link fra listen, der fejler, er en rigtig fejl
            plans.append(parsers.parse_weekplan(page, wid, urljoin(self.base_url, href)))
        return plans

    async def get_messages(self, child: Child, known: set[str] | None = None) -> list[MessageThread]:
        """Beskedtråde. Brødtekst hentes kun for tråde, vi ikke har set før (sparer kald).

        Kaldet loadmessagesforselectedconversation ændrer ikke læst-status
        (verificeret af pyskoleintra), så beskeder forbliver ulæste i ForældreIntra.
        """
        threads = parsers.parse_conversations(await self.get(f"{child.path}/messages/conversations"))
        known = known or set()
        fetched = 0
        for t in threads:
            if t.key in known or not t.latest_message_id or fetched >= 5:
                continue
            fetched += 1
            if t.thread_id:
                path = (f"{child.path}/messages/conversations/loadmessagesforselectedconversation"
                        f"?threadId={t.thread_id}&takeFromRootMessageId={t.latest_message_id}"
                        f"&takeToMessageId=0&searchRequest=&_={int(time.time() * 1000)}")
            else:
                path = (f"{child.path}/messages/conversations/getmessageforthreadlessconversation"
                        f"?messageId={t.latest_message_id}&_={int(time.time() * 1000)}")
            try:
                sender, body = parsers.parse_thread_body(await self.get(path), t.latest_message_id)
                t.body = body
                t.sender = sender or t.sender
            except (LayoutError, TransientError) as err:
                _LOGGER.debug("Kunne ikke hente brødtekst for tråd %s: %s", t.key, err)
        return threads

    async def get_contact_book(self, child: Child, own_names: list[str] | None = None) -> list[ContactNote]:
        return parsers.parse_contact_book(await self.get(f"{child.path}/contactbook/notes"), own_names)

    async def get_calendar(self, child: Child, start: datetime, end: datetime) -> list[CalendarItem]:
        query = f"start={int(start.timestamp())}&end={int(end.timestamp())}"
        for dept in ("departmentIds=[0]", "departmentIds=%5B%220%22%5D"):
            text = await self.get(f"{child.path}/calendareventsource/SchoolEvents?{dept}&{query}")
            try:
                return parsers.parse_calendar(text)
            except LayoutError:
                continue
        raise LayoutError("Kalender: SchoolEvents gav ikke JSON")

    def diagnostics(self) -> dict[str, Any]:
        return {
            "base_url": self.base_url,
            "login_type": self._login_type,
            "logged_in": self._logged_in,
            "children": [c.name for c in self.children],
            "requests": self.request_count,
            "logins": self.login_count,
        }
