"""Rene HTML/JSON-parsere for ForældreIntra.

Selektorerne er samlet fra tre uafhængige open source-projekter, der kører mod rigtige
skoler (svalgaard/fskintra, fezdk/pyskoleintra, CagosDk/ha-foraeldreintra og
CavaleriDK/skoleintra). Hver parser rejser LayoutError, hvis siden ikke ligner det
forventede, så et layoutskift aldrig bliver tolket som "ingen data".
"""

from __future__ import annotations

import html as html_lib
import json
import re
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import unquote
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup, Tag

from .errors import LayoutError
from .models import CalendarItem, Child, ContactNote, HomeworkItem, MessageThread, WeekPlan

TZ = ZoneInfo("Europe/Copenhagen")

MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "maj": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "okt": 10, "nov": 11, "dec": 12,
}
WEEKDAYS = ["mandag", "tirsdag", "onsdag", "torsdag", "fredag", "lørdag", "søndag"]
SSO_FIELDS = {"samlresponse", "samlrequest", "relaystate", "wa", "wresult", "wctx"}


def soup_of(text: str) -> BeautifulSoup:
    return BeautifulSoup(text, "html.parser")


def clean(text: str | None) -> str:
    return " ".join((text or "").replace("\xa0", " ").split())


def html_to_text(fragment: str) -> str:
    """HTML -> læsbar tekst med linjeskift bevaret."""
    if not fragment:
        return ""
    soup = soup_of(fragment)
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for block in soup.find_all(["p", "div", "li", "tr"]):
        block.insert_after("\n")
    lines = [clean(line) for line in soup.get_text().splitlines()]
    return "\n".join(line for line in lines if line)


# ---------------------------------------------------------------------------
# Datoer
# ---------------------------------------------------------------------------


def parse_danish_date(text: str, *, default_year: int | None = None) -> date | None:
    """Fortolker 'Mandag, 27. okt. 2026', 'Mandag 26 maj 2025', '27.10.2026', '2026-10-27'."""
    if not text:
        return None
    s = text.strip().lower()
    if m := re.search(r"(\d{4})-(\d{2})-(\d{2})", s):
        return _safe_date(int(m[1]), int(m[2]), int(m[3]))
    if m := re.search(r"(\d{1,2})[./-](\d{1,2})[./-](\d{4})", s):
        return _safe_date(int(m[3]), int(m[2]), int(m[1]))
    if m := re.search(r"(\d{1,2})\.?\s+([a-zæøå]{3,})\.?\s*(\d{4})?", s):
        month = MONTHS.get(m[2][:3])
        year = int(m[3]) if m[3] else default_year
        if month and year:
            return _safe_date(year, month, int(m[1]))
    return None


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_dotnet_or_iso(value: Any) -> datetime | None:
    """'/Date(1774821600000)/', epoch-ms, ISO 8601 -> tidszonebevidst datetime."""
    if value in (None, "") or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, TZ)
    s = str(value)
    if m := re.fullmatch(r"/Date\((-?\d+)\)/", s):
        return datetime.fromtimestamp(int(m[1]) / 1000, TZ)
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.replace(tzinfo=TZ) if dt.tzinfo is None else dt.astimezone(TZ)


# ---------------------------------------------------------------------------
# Login / sessionsgenkendelse
# ---------------------------------------------------------------------------


def form_inputs(form: Tag) -> dict[str, str]:
    data: dict[str, str] = {}
    for inp in form.find_all("input"):
        name = inp.get("name")
        if not name:
            continue
        if inp.get("type", "").lower() in ("checkbox", "radio") and not inp.has_attr("checked"):
            continue
        data[name] = inp.get("value", "")
    return data


def find_login_form(text: str) -> Tag | None:
    """ForældreIntras egen login-formular (felterne UserName + Password)."""
    for form in soup_of(text).find_all("form"):
        names = {(i.get("name") or "").lower() for i in form.find_all("input")}
        if "username" in names and "password" in names:
            return form
    return None


def find_sso_form(text: str) -> Tag | None:
    """Mellemliggende SAML/WS-Fed-formular, som en browser ville auto-submitte."""
    for form in soup_of(text).find_all("form"):
        names = {(i.get("name") or "").lower() for i in form.find_all("input")}
        if names & SSO_FIELDS or form.get("id") == "samlform" or form.get("name") == "relay":
            return form
    return None


def find_uni_login_form(text: str) -> Tag | None:
    for form in soup_of(text).find_all("form"):
        names = {(i.get("name") or "").lower() for i in form.find_all("input")}
        if names & {"user", "username"} or names & {"pass", "password"}:
            return form
    return None


def login_error_text(text: str) -> str | None:
    """Fejlbesked på login-siden (forkert kode osv.)."""
    soup = soup_of(text)
    for sel in (".validation-summary-errors", ".field-validation-error", ".sk-error", ".alert-danger"):
        if el := soup.select_one(sel):
            if msg := clean(el.get_text()):
                return msg
    low = text.lower()
    for needle in ("forkert brugernavn", "ikke adgang", "forkert adgangskode", "ugyldig"):
        if needle in low:
            return needle
    return None


def looks_blocked(status: int, text: str) -> bool:
    """CavaleriDK/skoleintra: automatiseringsbeskyttelse svarer med 500 + én h1 og én h2."""
    if status != 500:
        return False
    soup = soup_of(text)
    return len(soup.find_all("h1")) == 1 and len(soup.find_all("h2")) == 1


PARENT_RE = re.compile(r"/parent/(\d+)/([^/?#\"']+?)(?:item)?/")


def parse_children(text: str, current_url: str = "") -> list[Child]:
    """Find alle børn ud fra nuværende URL og links i menuen."""
    found: dict[str, Child] = {}

    def add(child_id: str, raw_name: str) -> None:
        raw_name = raw_name[:-4] if raw_name.lower().endswith("item") else raw_name
        if raw_name.lower() in ("settings", "account") or not raw_name:
            return
        key = f"{child_id}/{raw_name}"
        if key not in found:
            found[key] = Child(child_id, unquote(raw_name), f"/parent/{child_id}/{raw_name}")

    if m := PARENT_RE.search(current_url + "/"):
        add(m[1], m[2])
    for a in soup_of(text).find_all("a", href=True):
        href = a["href"]
        if m := re.search(r"/parent/(\d+)/([^/?#]+)/Index\b", href):
            add(m[1], m[2])
    return list(found.values())


# ---------------------------------------------------------------------------
# Lektiebog (diary)
# ---------------------------------------------------------------------------


def extract_diary_url(text: str, child_path: str) -> str | None:
    """Link til lektiebogens noter. To kendte varianter afhængigt af skolens opsætning."""
    if m := re.search(r"weeklyplansandhomework/diary/(?:notes/)?(\d+)", text):
        return f"{child_path}item/weeklyplansandhomework/diary/notes/{m[1]}"
    if m := re.search(re.escape(child_path) + r"/diaries/list/notes/\d+", text):
        return m[0]
    return None


def _extract_links(cell: Tag) -> list[dict[str, str]]:
    links = []
    for a in cell.find_all("a"):
        links.append({"tekst": clean(a.get_text()) or "link", "url": a.get("href", "")})
    return links


def parse_diary(text: str, *, today: date | None = None) -> list[HomeworkItem]:
    soup = soup_of(text)
    root = soup.select_one("#sk-diary-notes-container") or soup
    entries = [li for li in root.select("ul.sk-list > li") if li.select_one("div.sk-white-box")]
    if not entries and "sk-diary" not in text and "diary" not in text.lower():
        raise LayoutError("Lektiebog: hverken notes-container eller sk-list fundet")

    year = (today or date.today()).year
    items: list[HomeworkItem] = []
    for li in entries:
        head = li.select_one("div.sk-white-box b") or li.select_one("div.sk-white-box")
        label = clean(head.get_text()).rstrip(":") if head else ""
        day = parse_danish_date(label, default_year=year)
        body = li.select_one("div.sk-user-input") or li

        rows_found = False
        for table in body.find_all("table"):
            for row in table.find_all("tr"):
                cells = row.find_all(["td", "th"], recursive=False)
                if len(cells) < 2 or cells[0].name == "th":
                    continue
                subject = clean(cells[0].get_text()).rstrip(":")
                if subject.lower() in ("fag", "lektier"):
                    continue
                links = _extract_links(cells[1])
                content = html_to_text(str(cells[1]))
                if content or links:
                    rows_found = True
                    items.append(HomeworkItem(day, subject.capitalize() or "Generelt",
                                              content, "lektiebog", links, label))
            table.decompose()
        if rows_found:
            continue

        # Fritekst-variant: fag i <strong>, tekst efter.
        current = "Generelt"
        blocks: dict[str, list[str]] = {}
        links_by: dict[str, list[dict[str, str]]] = {}
        for node in body.find_all(recursive=False):
            if node is head or (head and head in node.descendants):
                continue
            strong = node.find(["strong", "b"])
            if strong and clean(strong.get_text()):
                current = clean(strong.get_text()).rstrip(":").capitalize()
                strong.extract()
            links_by.setdefault(current, []).extend(_extract_links(node))
            if line := html_to_text(str(node)):
                blocks.setdefault(current, []).append(line)
        for subject, lines in blocks.items():
            items.append(HomeworkItem(day, subject, "\n".join(lines), "lektiebog",
                                      links_by.get(subject, []), label))
    return items


# ---------------------------------------------------------------------------
# Ugeplan
# ---------------------------------------------------------------------------

WEEK_LINK_RE = re.compile(r"/item/class/(\d{1,2})-(\d{4})")


def parse_weekplan_list(text: str) -> list[dict[str, str]]:
    plans = []
    seen = set()
    for a in soup_of(text).find_all("a", href=True):
        if m := WEEK_LINK_RE.search(a["href"]):
            wid = f"{int(m[1])}-{m[2]}"
            if wid not in seen:
                seen.add(wid)
                plans.append({"week_id": wid, "href": a["href"], "title": clean(a.get_text())})
    return plans


def _decode_content(raw: str) -> str:
    raw = html_lib.unescape(raw or "")
    if "<" not in raw and re.search(r"%[0-9A-Fa-f]{2}", raw):
        raw = unquote(raw)
    return html_to_text(raw)


def _subject(plan: dict[str, Any]) -> str:
    subj = plan.get("Subject") or {}
    title = clean(subj.get("FormattedTitle") or subj.get("Title") or "")
    return "Generelt" if not title or title.lower().startswith("(uden") else title


def _weekplan_json(soup: BeautifulSoup) -> dict[str, Any] | None:
    for el in soup.find_all(True):
        for attr, value in el.attrs.items():
            if attr.lower() == "data-clientlogic-settings-weeklyplansapp" and value:
                try:
                    return json.loads(html_lib.unescape(value))
                except ValueError as err:
                    raise LayoutError(f"Ugeplan: JSON kunne ikke læses ({err})") from err
    return None


def parse_weekplan(text: str, week_id: str, url: str) -> WeekPlan:
    soup = soup_of(text)
    data = _weekplan_json(soup)
    week_num, year = (int(x) for x in week_id.split("-"))
    monday = date.fromisocalendar(year, week_num, 1)

    if data is None:
        # Ældre server-renderet layout (fskintra pgWeekplans)
        legacy = soup.select_one("div.sk-weekly-plan-container")
        if legacy is None:
            raise LayoutError("Ugeplan: hverken WeeklyPlansApp-JSON eller sk-weekly-plan-container")
        title = clean((legacy.find("h3") or legacy).get_text())[:120]
        return WeekPlan(week_id, title, url,
                        general=[HomeworkItem(None, "Ugeplan", html_to_text(str(legacy)), "ugeplan")])

    sel = data.get("SelectedPlan") or {}
    group = clean(sel.get("ClassOrGroup") or "")
    title = f"Ugeplan {group} uge {week_num}".replace("  ", " ")
    plan = WeekPlan(week_id, title, url)

    for lp in (sel.get("GeneralPlan") or {}).get("LessonPlans") or []:
        if content := _decode_content(lp.get("Content") or ""):
            plan.general.append(HomeworkItem(None, _subject(lp), content, "ugeplan",
                                             day_label=f"Uge {week_num}"))

    for daily in sel.get("DailyPlans") or []:
        label = clean(daily.get("Day") or daily.get("FormattedDate") or "")
        day = _daily_date(daily, monday)
        for lp in daily.get("LessonPlans") or []:
            if content := _decode_content(lp.get("Content") or ""):
                plan.days.append(HomeworkItem(day, _subject(lp), content, "ugeplan", day_label=label))
    return plan


def _daily_date(daily: dict[str, Any], monday: date) -> date | None:
    if dt := parse_dotnet_or_iso(daily.get("Date")):
        return dt.date()
    for key in ("LongFormattedDate", "FormattedDate"):
        if d := parse_danish_date(str(daily.get(key) or ""), default_year=monday.year):
            return d
    name = clean(daily.get("Day") or "").lower()
    for i, wd in enumerate(WEEKDAYS):
        if name.startswith(wd[:3]):
            return monday + timedelta(days=i)
    return None


# ---------------------------------------------------------------------------
# Beskeder
# ---------------------------------------------------------------------------


def parse_conversations(text: str) -> list[MessageThread]:
    """Samtale-visningen gemmer listen som JSON i en data-clientlogic-settings-*-attribut."""
    soup = soup_of(text)
    for el in soup.find_all(True):
        for attr, value in el.attrs.items():
            if not attr.lower().startswith("data-clientlogic-settings") or not isinstance(value, str):
                continue
            if "Conversations" not in value:
                continue
            try:
                data = json.loads(html_lib.unescape(value))
            except ValueError:
                continue
            if isinstance(data, dict) and isinstance(data.get("Conversations"), list):
                return [_thread(c) for c in data["Conversations"]]

    # Gammel indbakke-visning (fskintra parseTrayMessages)
    items = soup.select(".sk-message-list-item")
    if items:
        threads = []
        for div in items:
            a = div.find("a", href=True)
            mid = re.search(r"/message/(\d+)", a["href"]) if a else None
            if not mid:
                continue
            sender = clean((div.select_one(".sk-message-senderrecipient-name") or div).get_text())
            title = clean((div.select_one(".sk-message-title") or a).get_text())
            unread = "sk-unread-message" in (div.get("class") or [])
            threads.append(MessageThread("", mid[1], title, sender, "", unread))
        return threads
    if "sk-message" in text:
        return []  # beskedsiden er genkendt, men indbakken er tom
    raise LayoutError("Beskeder: ingen Conversations-JSON og ingen beskedliste fundet")


def _thread(conv: dict[str, Any]) -> MessageThread:
    profile = conv.get("ProfileImageViewModel") or {}
    return MessageThread(
        thread_id=str(conv.get("ThreadId") or ""),
        latest_message_id=str(conv.get("LatestMessageId") or ""),
        subject=clean(conv.get("Subject") or ""),
        sender=clean(profile.get("Name") or conv.get("SenderName") or ""),
        date_text=clean(conv.get("Date") or ""),
        unread=bool(conv.get("IsUnread")),
        participants=clean(conv.get("ThreadParticipantsString") or ""),
    )


def parse_thread_body(text: str, latest_id: str) -> tuple[str, str]:
    """JSON fra loadmessagesforselectedconversation -> (afsender, brødtekst) for seneste besked."""
    try:
        data = json.loads(text)
    except ValueError as err:
        raise LayoutError("Besked: svar er ikke JSON") from err
    msgs = data if isinstance(data, list) else [data]
    msgs = [m for m in msgs if isinstance(m, dict)]
    if not msgs:
        return "", ""
    msg = next((m for m in msgs if str(m.get("Id")) == str(latest_id)), msgs[0])
    return clean(msg.get("SenderName") or ""), html_to_text(msg.get("BaseText") or "")


# ---------------------------------------------------------------------------
# Kontaktbog
# ---------------------------------------------------------------------------


def parse_contact_book(text: str, own_names: list[str] | None = None) -> list[ContactNote]:
    soup = soup_of(text)
    notes = []
    own = {n.strip().lower() for n in own_names or [] if n.strip()}
    for item in soup.select("div.sk-contactbook-note-container"):
        author_div = item.select_one("div.sk-news-item-author")
        author = clean(author_div.find("span").get_text()) if author_div and author_div.find("span") else ""
        date_el = item.select_one("div.sk-contactbook-note-date")
        content_el = item.select_one("div.sk-contactbook-note")
        content = html_to_text(str(content_el)) if content_el else ""
        if not content:
            continue
        note_id = ""
        for a in item.find_all("a", href=True):
            if m := re.search(r"/replynote/(\d+)", a["href"]):
                note_id = m[1]
                break
        seen = item.find("small")
        notes.append(ContactNote(
            note_id=note_id,
            author=author,
            date_text=clean(date_el.get_text()) if date_el else "",
            text=content,
            is_reply="answer" in (item.get("class") or []),
            seen_by=re.sub(r"^Set af:\s*", "", clean(seen.get_text())) if seen else "",
            from_us=(author.lower() in own) if own else None,
        ))
    if not notes and "contactbook" not in text.lower() and "kontaktbog" not in text.lower():
        raise LayoutError("Kontaktbog: ingen sk-contactbook-note-container og ingen kontaktbogs-markører")
    # Nyeste først (rækkefølgen på siden er ikke dokumenteret; sorteringen er stabil ved ukendt dato).
    today = date.today()
    notes.sort(key=lambda n: parse_danish_date(n.date_text, default_year=today.year) or date.min, reverse=True)
    return notes


# ---------------------------------------------------------------------------
# Kalender
# ---------------------------------------------------------------------------


def parse_calendar(text: str) -> list[CalendarItem]:
    try:
        data = json.loads(text)
    except ValueError as err:
        raise LayoutError("Kalender: svar er ikke JSON") from err
    if not isinstance(data, list):
        raise LayoutError("Kalender: forventede en JSON-liste")
    events = []
    for item in data:
        if not isinstance(item, dict):
            continue
        start = parse_dotnet_or_iso(item.get("startDate") or item.get("start") or item.get("Start"))
        end = parse_dotnet_or_iso(item.get("endDate") or item.get("end") or item.get("End"))
        if start is None:
            continue
        end = end or start
        all_day = bool(item.get("allDay", item.get("AllDay", False)))
        location = item.get("location", item.get("Location", ""))
        if isinstance(location, list):
            location = ", ".join(str(x) for x in location)
        if all_day:
            s, e = start.date(), end.date()
            if e <= s:
                e = s + timedelta(days=1)  # HA kræver eksklusiv slutdato
            start_v, end_v = s, e
        else:
            start_v, end_v = start, max(end, start)
        events.append(CalendarItem(
            uid=str(item.get("originalId") or item.get("id") or item.get("Id") or ""),
            title=clean(item.get("title") or item.get("Title") or "(uden titel)"),
            start=start_v,
            end=end_v,
            all_day=all_day,
            description=html_to_text(str(item.get("description") or item.get("Description") or "")),
            location=clean(str(location or "")),
        ))
    return events
