"""Skolekalender pr. barn (ture, fridage, arrangementer)."""

from __future__ import annotations

from datetime import date, datetime

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import SkoleIntraConfigEntry
from .const import CAT_CALENDAR
from .coordinator import SkoleIntraCoordinator
from .models import CalendarItem, Child
from .sensor import ChildEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: SkoleIntraConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(SchoolCalendar(coordinator, child) for child in coordinator.client.children)


def _to_event(item: CalendarItem) -> CalendarEvent:
    return CalendarEvent(start=item.start, end=item.end, summary=item.title,
                         description=item.description or None, location=item.location or None, uid=item.uid or None)


def _start_dt(value: datetime | date) -> datetime:
    if isinstance(value, datetime):
        return value
    return dt_util.start_of_local_day(value)


class SchoolCalendar(ChildEntity, CalendarEntity):
    _attr_icon = "mdi:calendar-school"

    def __init__(self, coordinator: SkoleIntraCoordinator, child: Child) -> None:
        super().__init__(coordinator, child, "calendar", "Skolekalender")

    @property
    def event(self) -> CalendarEvent | None:
        now = dt_util.now()
        upcoming = sorted((e for e in self.cd.calendar if _start_dt(e.end) > now), key=lambda e: _start_dt(e.start))
        return _to_event(upcoming[0]) if upcoming else None

    async def async_get_events(self, hass: HomeAssistant, start_date: datetime, end_date: datetime) -> list[CalendarEvent]:
        # Data dækker 7 dage tilbage til 120 dage frem (hentes hver 6. time).
        return [_to_event(e) for e in self.cd.calendar
                if _start_dt(e.start) < end_date and _start_dt(e.end) > start_date]

    @property
    def extra_state_attributes(self):
        return self._status_attrs(CAT_CALENDAR)
