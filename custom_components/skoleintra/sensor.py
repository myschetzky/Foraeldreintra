"""Sensorer pr. barn + en samlet statussensor."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from . import SkoleIntraConfigEntry
from .const import CAT_CONTACTBOOK, CAT_DIARY, CAT_MESSAGES, CAT_WEEKPLAN, DOMAIN
from .coordinator import ChildData, SkoleIntraCoordinator
from .models import Child, HomeworkItem

MAX_ATTR_ITEMS = 40


def next_school_day(today: date) -> date:
    nxt = today + timedelta(days=1)
    while nxt.weekday() >= 5:
        nxt += timedelta(days=1)
    return nxt


def homework_for(cd: ChildData, cats: list[str], day: date | None = None) -> list[HomeworkItem]:
    """Lektier fra de kilder, barnet er sat op til. day=None -> alle fra i dag og frem."""
    items: list[HomeworkItem] = []
    if CAT_DIARY in cats:
        items += cd.diary
    if CAT_WEEKPLAN in cats:
        items += [i for p in cd.weekplans for i in p.days]
    today = dt_util.now().date()
    if day is None:
        return sorted([i for i in items if i.day and i.day >= today], key=lambda i: (i.day, i.subject))
    return [i for i in items if i.day == day]


def markdown(items: list[HomeworkItem], empty: str = "Ingen lektier 🎉") -> str:
    if not items:
        return empty
    lines = []
    for i in items:
        src = " _(ugeplan)_" if i.source == "ugeplan" else ""
        text = i.text.replace("\n", "  \n")
        lines.append(f"**{i.subject}**{src}  \n{text}")
    return "\n\n".join(lines)


async def async_setup_entry(
    hass: HomeAssistant, entry: SkoleIntraConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = [StatusSensor(coordinator)]
    for child in coordinator.client.children:
        cats = coordinator.categories_for(child)
        entities += [UnreadMessagesSensor(coordinator, child), ContactBookSensor(coordinator, child)]
        if CAT_DIARY in cats or CAT_WEEKPLAN in cats:
            entities += [
                HomeworkDaySensor(coordinator, child, "today"),
                HomeworkDaySensor(coordinator, child, "next"),
                UpcomingHomeworkSensor(coordinator, child),
            ]
        if CAT_WEEKPLAN in cats:
            entities.append(WeekPlanSensor(coordinator, child))
    async_add_entities(entities)


class ChildEntity(CoordinatorEntity[SkoleIntraCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: SkoleIntraCoordinator, child: Child, key: str, name: str) -> None:
        super().__init__(coordinator)
        self.child = child
        self._attr_name = name
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{child.child_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{coordinator.entry.entry_id}_{child.child_id}")},
            name=child.name,
            manufacturer="itslearning (SkoleIntra)",
            model="ForældreIntra",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=f"{coordinator.client.base_url}{child.path}/Index",
        )

    @property
    def cd(self) -> ChildData:
        return (self.coordinator.data or {}).get(self.child.slug) or ChildData(self.child)

    @property
    def available(self) -> bool:
        # Vis sidste kendte data, også når et kald fejler. Fejlen ses i attributten og statussensoren.
        return self.coordinator.data is not None and self.child.slug in self.coordinator.data

    def _status_attrs(self, cat: str) -> dict[str, Any]:
        st = self.coordinator.status.get(f"{self.child.slug}:{cat}")
        if not st:
            return {}
        return {"sidst_opdateret": st.last_success.isoformat() if st.last_success else None,
                "fejl": st.last_error}


class HomeworkDaySensor(ChildEntity, SensorEntity):
    _attr_icon = "mdi:book-open-page-variant"
    _unrecorded_attributes = frozenset({"lektier", "markdown"})
    _attr_native_unit_of_measurement = "lektier"

    def __init__(self, coordinator: SkoleIntraCoordinator, child: Child, which: str) -> None:
        name = "Lektier i dag" if which == "today" else "Lektier næste skoledag"
        super().__init__(coordinator, child, f"homework_{which}", name)
        self.which = which

    def _day(self) -> date:
        today = dt_util.now().date()
        return today if self.which == "today" else next_school_day(today)

    def _items(self) -> list[HomeworkItem]:
        return homework_for(self.cd, self.coordinator.categories_for(self.child), self._day())

    @property
    def native_value(self) -> int:
        return len(self._items())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        items = self._items()
        cats = self.coordinator.categories_for(self.child)
        attrs = {"dato": self._day().isoformat(), "lektier": [i.as_dict() for i in items],
                 "markdown": markdown(items)}
        for cat in (CAT_DIARY, CAT_WEEKPLAN):
            if cat in cats:
                attrs[f"{cat}_status"] = self._status_attrs(cat)
        return attrs


class UpcomingHomeworkSensor(ChildEntity, SensorEntity):
    _attr_icon = "mdi:calendar-check"
    _unrecorded_attributes = frozenset({"lektier"})
    _attr_native_unit_of_measurement = "lektier"

    def __init__(self, coordinator: SkoleIntraCoordinator, child: Child) -> None:
        super().__init__(coordinator, child, "homework_upcoming", "Kommende lektier")

    @property
    def native_value(self) -> int:
        return len(homework_for(self.cd, self.coordinator.categories_for(self.child)))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        items = homework_for(self.cd, self.coordinator.categories_for(self.child))[:MAX_ATTR_ITEMS]
        return {"lektier": [i.as_dict() for i in items]}


class WeekPlanSensor(ChildEntity, SensorEntity):
    _attr_icon = "mdi:calendar-week"
    _unrecorded_attributes = frozenset({"denne_uge", "næste_uge", "ugens_opgaver_markdown"})

    def __init__(self, coordinator: SkoleIntraCoordinator, child: Child) -> None:
        super().__init__(coordinator, child, "weekplan", "Ugeplan")

    def _current(self):
        y, w, _ = dt_util.now().date().isocalendar()
        return next((p for p in self.cd.weekplans if p.week_id == f"{w}-{y}"), None)

    @property
    def native_value(self) -> str:
        plan = self._current()
        return f"Uge {plan.week_id.split('-')[0]}" if plan else "Ingen ugeplan"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        plan = self._current()
        nxt = next((p for p in self.cd.weekplans if p is not plan), None)
        return {
            "denne_uge": plan.as_dict() if plan else None,
            "næste_uge": nxt.as_dict() if nxt else None,
            "ugens_opgaver_markdown": markdown(plan.general, "Ingen ugeopgaver") if plan else "",
            **self._status_attrs(CAT_WEEKPLAN),
        }


class UnreadMessagesSensor(ChildEntity, SensorEntity):
    _attr_icon = "mdi:email-outline"
    _unrecorded_attributes = frozenset({"tråde"})
    _attr_native_unit_of_measurement = "ulæste"

    def __init__(self, coordinator: SkoleIntraCoordinator, child: Child) -> None:
        super().__init__(coordinator, child, "messages_unread", "Ulæste beskeder")

    @property
    def native_value(self) -> int:
        return sum(1 for t in self.cd.messages if t.unread)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        latest = self.cd.messages[0] if self.cd.messages else None
        return {
            "seneste_emne": latest.subject if latest else None,
            "seneste_afsender": latest.sender if latest else None,
            "tråde": [t.as_dict() for t in self.cd.messages[:10]],
            **self._status_attrs(CAT_MESSAGES),
        }


class ContactBookSensor(ChildEntity, SensorEntity):
    _attr_icon = "mdi:notebook-outline"
    _unrecorded_attributes = frozenset({"noter", "seneste_tekst"})

    def __init__(self, coordinator: SkoleIntraCoordinator, child: Child) -> None:
        super().__init__(coordinator, child, "contactbook", "Kontaktbog")

    @property
    def native_value(self) -> str | None:
        notes = self.cd.contact_notes
        if not notes:
            return "Ingen noter"
        return notes[0].date_text or "ukendt dato"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        notes = self.cd.contact_notes
        return {
            "antal": len(notes),
            "seneste_forfatter": notes[0].author if notes else None,
            "seneste_tekst": notes[0].text[:500] if notes else None,
            "noter": [n.as_dict() for n in notes[:10]],
            **self._status_attrs(CAT_CONTACTBOOK),
        }


class StatusSensor(CoordinatorEntity[SkoleIntraCoordinator], SensorEntity):
    """Samlet sundhedstjek: 'ok', 'delvis fejl', 'fejl'. Brug den i en automatisering."""

    _attr_has_entity_name = True
    _attr_name = "Status"
    _attr_icon = "mdi:school"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _unrecorded_attributes = frozenset({"kategorier"})

    def __init__(self, coordinator: SkoleIntraCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_status"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.entry.entry_id)},
            name="SkoleIntra",
            manufacturer="itslearning (SkoleIntra)",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=coordinator.client.base_url,
        )

    @property
    def available(self) -> bool:
        return True

    @property
    def native_value(self) -> str:
        if self.coordinator.account_error or not self.coordinator.last_update_success:
            return "fejl"
        if any(st.failures for st in self.coordinator.status.values()):
            return "delvis fejl"
        return "ok"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "konto_fejl": self.coordinator.account_error,
            "kategorier": {k: v.as_dict() for k, v in sorted(self.coordinator.status.items())},
            **self.coordinator.client.diagnostics(),
        }

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()
