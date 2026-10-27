"""Koordinator: differentieret polling, fejlhåndtering, ændringsdetektion og events."""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import SkoleIntraClient
from .const import (
    CAT_CALENDAR,
    CAT_CONTACTBOOK,
    CAT_DIARY,
    CAT_MESSAGES,
    CAT_WEEKPLAN,
    DEFAULT_CALENDAR_HOURS,
    DEFAULT_FAST_MINUTES,
    DEFAULT_QUIET_END,
    DEFAULT_QUIET_START,
    DEFAULT_SLOW_MINUTES,
    DOMAIN,
    EVENT_NEW_ITEM,
    FAST_CATEGORIES,
    LAYOUT_ISSUE_THRESHOLD,
    MAX_EVENTS_PER_CATEGORY,
    NIGHT_FACTOR,
    OPT_CALENDAR_HOURS,
    OPT_CHILD_SOURCES,
    OPT_FAST_MINUTES,
    OPT_OWN_NAMES,
    OPT_QUIET_END,
    OPT_QUIET_START,
    OPT_SLOW_MINUTES,
    SEEN_RETENTION_DAYS,
    SOURCE_BOTH,
    SOURCE_DIARY,
    SOURCE_WEEKPLAN,
    STORAGE_VERSION,
    TICK_SECONDS,
)
from .errors import AuthError, BlockedError, LayoutError, ManualActionRequired, SkoleIntraError, TransientError
from .models import CalendarItem, Child, ContactNote, HomeworkItem, MessageThread, WeekPlan
from .tracker import Change, SeenStore

_LOGGER = logging.getLogger(__name__)

MAX_BACKOFF = timedelta(hours=4)
BLOCKED_BACKOFF = timedelta(hours=2)


@dataclass
class ChildData:
    child: Child
    diary: list[HomeworkItem] = field(default_factory=list)
    weekplans: list[WeekPlan] = field(default_factory=list)
    messages: list[MessageThread] = field(default_factory=list)
    contact_notes: list[ContactNote] = field(default_factory=list)
    calendar: list[CalendarItem] = field(default_factory=list)


@dataclass
class CategoryStatus:
    last_success: datetime | None = None
    last_attempt: datetime | None = None
    last_error: str | None = None
    error_kind: str | None = None
    failures: int = 0
    next_due: datetime | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "sidst_ok": self.last_success.isoformat() if self.last_success else None,
            "sidste_forsøg": self.last_attempt.isoformat() if self.last_attempt else None,
            "fejl": self.last_error,
            "fejltype": self.error_kind,
            "fejl_i_træk": self.failures,
            "næste_kørsel": self.next_due.isoformat() if self.next_due else None,
        }


class SkoleIntraCoordinator(DataUpdateCoordinator[dict[str, ChildData]]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: SkoleIntraClient) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=timedelta(seconds=TICK_SECONDS),
                         config_entry=entry)
        self.entry = entry
        self.client = client
        self.status: dict[str, CategoryStatus] = {}
        self.account_error: str | None = None
        self._store: Store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}.seen")
        self.seen = SeenStore(retention_days=SEEN_RETENTION_DAYS)
        self._force: set[str] = set()
        self._login_failures = 0
        self._login_retry_at: datetime | None = None

    # ------------------------------------------------------------ indstillinger

    def _opt(self, key: str, default: Any) -> Any:
        return self.entry.options.get(key, default)

    def source_for(self, child: Child) -> str:
        return self._opt(OPT_CHILD_SOURCES, {}).get(child.name, SOURCE_BOTH)

    def categories_for(self, child: Child) -> list[str]:
        source = self.source_for(child)
        cats = [CAT_MESSAGES, CAT_CONTACTBOOK, CAT_CALENDAR]
        if source in (SOURCE_DIARY, SOURCE_BOTH):
            cats.append(CAT_DIARY)
        if source in (SOURCE_WEEKPLAN, SOURCE_BOTH):
            cats.append(CAT_WEEKPLAN)
        return cats

    def own_names(self) -> list[str]:
        return [n.strip() for n in str(self._opt(OPT_OWN_NAMES, "")).split(",") if n.strip()]

    def interval_for(self, category: str, now: datetime) -> timedelta:
        if category == CAT_CALENDAR:
            return timedelta(hours=self._opt(OPT_CALENDAR_HOURS, DEFAULT_CALENDAR_HOURS))
        minutes = (self._opt(OPT_FAST_MINUTES, DEFAULT_FAST_MINUTES) if category in FAST_CATEGORIES
                   else self._opt(OPT_SLOW_MINUTES, DEFAULT_SLOW_MINUTES))
        start, end = self._opt(OPT_QUIET_START, DEFAULT_QUIET_START), self._opt(OPT_QUIET_END, DEFAULT_QUIET_END)
        quiet = (now.hour >= start or now.hour < end) if start > end else (start <= now.hour < end)
        return timedelta(minutes=minutes * (NIGHT_FACTOR if quiet else 1))

    # ---------------------------------------------------------------- livscyklus

    async def async_load(self) -> None:
        stored = await self._store.async_load()
        if stored:
            self.seen.data = stored.get("seen", {})

    def _save(self) -> None:
        self._store.async_delay_save(lambda: {"seen": self.seen.data}, 30)

    def request_refresh(self, category: str | None = None) -> None:
        self._force.add(category or "*")

    # ---------------------------------------------------------------- opdatering

    async def _async_update_data(self) -> dict[str, ChildData]:
        now = dt_util.now()
        data: dict[str, ChildData] = dict(self.data or {})

        if not self.client.children and self._login_retry_at and now < self._login_retry_at and data:
            return data  # venter på næste login-forsøg (backoff)
        try:
            if not self.client.children:
                await self.client.login()
        except AuthError as err:
            # Home Assistant stopper polling og beder om ny adgangskode -> ingen konto-lockout.
            raise ConfigEntryAuthFailed(str(err)) from err
        except SkoleIntraError as err:
            self._login_failures += 1
            wait = BLOCKED_BACKOFF if isinstance(err, (ManualActionRequired, BlockedError)) else min(
                timedelta(minutes=5) * 2 ** min(self._login_failures - 1, 6), MAX_BACKOFF)
            self._login_retry_at = now + wait
            self.account_error = str(err)
            if isinstance(err, ManualActionRequired):
                self._issue("manual_action", str(err))
            _LOGGER.warning("Login fejlede (%s): %s — prøver igen %s", type(err).__name__, err,
                            self._login_retry_at.strftime("%H:%M"))
            if not data:
                raise UpdateFailed(str(err)) from err
            return data
        self._login_failures, self._login_retry_at = 0, None

        due: list[tuple[Child, str]] = []
        for child in self.client.children:
            data.setdefault(child.slug, ChildData(child))
            for cat in self.categories_for(child):
                st = self.status.setdefault(f"{child.slug}:{cat}", CategoryStatus())
                if "*" in self._force or cat in self._force or st.next_due is None or st.next_due <= now:
                    due.append((child, cat))
        self._force.clear()

        stopped_by: str | None = None
        for child, cat in due:
            st = self.status[f"{child.slug}:{cat}"]
            st.last_attempt = now
            try:
                changes = await self._fetch(data[child.slug], cat, now)
            except AuthError as err:
                self.client.children = []
                raise ConfigEntryAuthFailed(str(err)) from err
            except ManualActionRequired as err:
                self._issue("manual_action", str(err))
                self._fail(st, "manuel", str(err), now, cat, BLOCKED_BACKOFF, who=child.name)
                self._postpone_all(now + BLOCKED_BACKOFF)
                stopped_by = str(err)
                break
            except BlockedError as err:
                self._fail(st, "blokeret", str(err), now, cat, BLOCKED_BACKOFF, who=child.name)
                self._postpone_all(now + BLOCKED_BACKOFF)  # ingen kald overhovedet i 2 timer
                stopped_by = str(err)
                break
            except TransientError as err:
                self._fail(st, "midlertidig", str(err), now, cat, who=child.name)
                continue
            except LayoutError as err:
                self._fail(st, "layout", str(err), now, cat, who=child.name)
                if st.failures >= LAYOUT_ISSUE_THRESHOLD:
                    self._issue(f"layout_{cat}", f"{child.name}: {err}")
                continue
            except Exception as err:  # noqa: BLE001 — en ukendt parserfejl må ikke vælte resten
                _LOGGER.exception("Uventet fejl for %s/%s", child.name, cat)
                self._fail(st, "ukendt", repr(err), now, cat, who=child.name)
                continue

            st.last_success, st.last_error, st.error_kind, st.failures = now, None, None, 0
            st.next_due = now + self._jitter(self.interval_for(cat, now))
            ir.async_delete_issue(self.hass, DOMAIN, f"layout_{cat}")
            self._fire(child, cat, changes)

        self.account_error = stopped_by
        if stopped_by is None:
            ir.async_delete_issue(self.hass, DOMAIN, "manual_action")
        self._save()
        return data

    def _postpone_all(self, until: datetime) -> None:
        for st in self.status.values():
            if st.next_due is None or st.next_due < until:
                st.next_due = until

    async def _fetch(self, cd: ChildData, cat: str, now: datetime) -> list[Change]:
        child = cd.child
        if cat == CAT_MESSAGES:
            scope = "konto:beskeder"  # samme tråd kan ses under begge børn — notificér én gang
            cd.messages = await self.client.get_messages(child, self.seen.known_keys(scope))
            return self.seen.diff(scope, cd.messages, now, marker=f"{scope}@{child.slug}")
        if cat == CAT_CONTACTBOOK:
            cd.contact_notes = await self.client.get_contact_book(child, self.own_names())
            return self.seen.diff(f"{child.slug}:{cat}", cd.contact_notes, now)
        if cat == CAT_DIARY:
            cd.diary = await self.client.get_diary(child)
            return self.seen.diff(f"{child.slug}:{cat}", self._upcoming(cd.diary, now.date()), now)
        if cat == CAT_WEEKPLAN:
            cd.weekplans = await self.client.get_weekplans(child, now.date())
            scope = f"{child.slug}:{cat}"
            # En helt ny ugeplan giver én notifikation — ikke én pr. punkt.
            new_weeks = {c.item.week_id for c in self.seen.diff(f"{scope}:uger", cd.weekplans, now) if c.kind == "ny"}
            week_of = {id(i): p.week_id for p in cd.weekplans for i in p.general + p.days}
            items = [i for p in cd.weekplans for i in p.general + p.days]
            changes = [c for c in self.seen.diff(scope, self._upcoming(items, now.date()), now)
                       if week_of.get(id(c.item)) not in new_weeks]
            return [Change("ny", f"uge:{p.week_id}", p) for p in cd.weekplans if p.week_id in new_weeks] + changes
        if cat == CAT_CALENDAR:
            start = now - timedelta(days=7)
            cd.calendar = await self.client.get_calendar(child, start, now + timedelta(days=120))
            upcoming = [e for e in cd.calendar if _as_date(e.end) >= now.date()]
            return self.seen.diff(f"{child.slug}:{cat}", upcoming, now)
        raise ValueError(cat)

    @staticmethod
    def _upcoming(items: list[HomeworkItem], today: date) -> list[HomeworkItem]:
        """Kun fremtidige lektier giver notifikationer — ikke rettelser i gamle."""
        return [i for i in items if i.day is None or i.day >= today]

    def _fail(self, st: CategoryStatus, kind: str, msg: str, now: datetime, cat: str,
              minimum: timedelta | None = None, *, who: str = "") -> None:
        st.failures += 1
        st.last_error, st.error_kind = msg, kind
        backoff = min(self.interval_for(cat, now) * (2 ** min(st.failures - 1, 6)), MAX_BACKOFF)
        st.next_due = now + self._jitter(max(backoff, minimum or timedelta()))
        log = _LOGGER.warning if st.failures in (1, LAYOUT_ISSUE_THRESHOLD) else _LOGGER.debug
        log("%s/%s fejlede (%s, %d. gang i træk): %s — næste forsøg %s", who, cat, kind, st.failures, msg,
            st.next_due.strftime("%H:%M"))

    @staticmethod
    def _jitter(delta: timedelta) -> timedelta:
        return delta * random.uniform(0.9, 1.1)

    def _issue(self, issue_id: str, detail: str) -> None:
        ir.async_create_issue(
            self.hass, DOMAIN, issue_id, is_fixable=False, severity=ir.IssueSeverity.WARNING,
            translation_key="manual_action" if issue_id == "manual_action" else "layout_changed",
            translation_placeholders={"detail": detail[:300]},
        )

    # ------------------------------------------------------------------- events

    def _fire(self, child: Child, cat: str, changes: list[Change]) -> None:
        if not changes:
            return
        base = {"entry_id": self.entry.entry_id, "barn": child.name, "kategori": cat}
        for change in changes[:MAX_EVENTS_PER_CATEGORY]:
            payload = {**base, "ændring": change.kind, "nøgle": change.key, **_describe(cat, change.item)}
            payload["url"] = self._deep_link(child, cat)
            self.hass.bus.async_fire(EVENT_NEW_ITEM, payload)
        if len(changes) > MAX_EVENTS_PER_CATEGORY:
            self.hass.bus.async_fire(EVENT_NEW_ITEM, {
                **base, "ændring": "mange", "titel": f"{len(changes)} nye/ændrede elementer",
                "tekst": "Åbn ForældreIntra for at se resten.", "url": self._deep_link(child, cat),
            })

    def _deep_link(self, child: Child, cat: str) -> str:
        paths = {
            CAT_MESSAGES: "/messages/conversations",
            CAT_CONTACTBOOK: "/contactbook/notes",
            CAT_DIARY: "item/weeklyplansandhomework/diary",
            CAT_WEEKPLAN: "item/weeklyplansandhomework/list",
            CAT_CALENDAR: "/Index",
        }
        return f"{self.client.base_url}{child.path}{paths[cat]}"


def _as_date(value: datetime | date) -> date:
    return value.date() if isinstance(value, datetime) else value


def _describe(cat: str, item: Any) -> dict[str, Any]:
    if isinstance(item, MessageThread):
        return {"titel": item.subject, "afsender": item.sender, "dato": item.date_text,
                "tekst": (item.body or "")[:1000], "ulæst": item.unread}
    if isinstance(item, ContactNote):
        retning = "ukendt" if item.from_us is None else ("fra_os" if item.from_us else "til_os")
        return {"titel": f"Kontaktbog: {item.author}", "afsender": item.author, "dato": item.date_text,
                "tekst": item.text[:1000], "retning": retning, "er_svar": item.is_reply}
    if isinstance(item, HomeworkItem):
        when = item.day.strftime("%d.%m") if item.day else item.day_label
        return {"titel": f"{item.subject} ({when})", "dato": item.day.isoformat() if item.day else None,
                "tekst": item.text[:1000], "kilde": item.source}
    if isinstance(item, WeekPlan):
        subjects = sorted({i.subject for i in item.general + item.days})
        return {"titel": f"Ny ugeplan: {item.title}", "dato": None,
                "tekst": f"{len(item.general) + len(item.days)} punkter: {', '.join(subjects)}"[:1000]}
    if isinstance(item, CalendarItem):
        return {"titel": item.title, "dato": item.start.isoformat(), "tekst": item.description[:1000],
                "sted": item.location}
    return {"titel": str(item)}
