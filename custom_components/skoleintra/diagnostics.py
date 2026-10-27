"""Diagnosticering (Indstillinger -> Enheder -> SkoleIntra -> Download diagnosticering).

Indeholder ingen beskedtekster, navne på afsendere eller loginoplysninger — kun antal og fejl,
så filen kan deles i et issue, hvis skolens layout ændrer sig.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import SkoleIntraConfigEntry
from .const import CONF_PASSWORD, CONF_USERNAME

TO_REDACT = {CONF_PASSWORD, CONF_USERNAME, "own_names"}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: SkoleIntraConfigEntry) -> dict[str, Any]:
    coordinator = entry.runtime_data
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "options": async_redact_data(dict(entry.options), TO_REDACT),
        "client": {**coordinator.client.diagnostics(), "children": len(coordinator.client.children)},
        "status": {k.split(":", 1)[1] + f"#{i}": v.as_dict()
                   for i, (k, v) in enumerate(sorted(coordinator.status.items()))},
        "counts": [
            {
                "diary": len(cd.diary),
                "weekplans": [(p.week_id, len(p.general), len(p.days)) for p in cd.weekplans],
                "messages": len(cd.messages),
                "contact_notes": len(cd.contact_notes),
                "calendar": len(cd.calendar),
            }
            for cd in (coordinator.data or {}).values()
        ],
    }
