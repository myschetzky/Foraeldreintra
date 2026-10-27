"""Ændringsdetektion uden Home Assistant-afhængighed.

Gemmer for hver (barn, kategori) et kort {nøgle: [fingeraftryk, sidst_set_iso]}.
- Første gang en kategori ses, gemmes alt uden notifikationer (ingen storm ved opstart).
- Ny nøgle -> "ny". Kendt nøgle med nyt fingeraftryk -> "ændret".
- Nøgler, der forsvinder, bliver liggende i SEEN_RETENTION_DAYS. Så giver et midlertidigt tomt
  svar (fx layoutfejl eller skolen der rydder op) ikke en ny bølge af "nye" elementer bagefter.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol


class Trackable(Protocol):
    @property
    def key(self) -> str: ...
    @property
    def fp(self) -> str: ...


@dataclass
class Change:
    kind: str  # "ny" | "ændret"
    key: str
    item: Any


def unique_keys(items: Iterable[Trackable]) -> list[tuple[str, Trackable]]:
    """To lektier i samme fag samme dag får hver sin nøgle (#2, #3 ...)."""
    counts: dict[str, int] = {}
    out = []
    for item in items:
        base = item.key
        counts[base] = counts.get(base, 0) + 1
        out.append((base if counts[base] == 1 else f"{base}#{counts[base]}", item))
    return out


class SeenStore:
    SEEDED = "_seeded"

    def __init__(self, data: dict[str, Any] | None = None, retention_days: int = 120) -> None:
        self.data: dict[str, Any] = data or {}
        self.retention = timedelta(days=retention_days)

    def diff(self, scope: str, items: Iterable[Trackable], now: datetime, marker: str | None = None) -> list[Change]:
        """`marker` adskiller kilder, der deler scope (fx beskeder set fra hvert barn).

        Første gang en marker ses, registreres alt stille — også efter genstart huskes det,
        så beskeder, der kom mens Home Assistant var nede, stadig giver notifikation.
        """
        seeded: list[str] = self.data.setdefault(self.SEEDED, [])
        marker = marker or scope
        first_time = marker not in seeded
        if first_time:
            seeded.append(marker)
        known = self.data.setdefault(scope, {})
        stamp = now.isoformat(timespec="seconds")
        changes: list[Change] = []
        for key, item in unique_keys(items):
            fp = item.fp
            previous = known.get(key)
            if previous is None:
                if not first_time:
                    changes.append(Change("ny", key, item))
            elif previous[0] != fp:
                changes.append(Change("ændret", key, item))
            known[key] = [fp, stamp]
        self._prune(scope, now)
        return changes

    def known_keys(self, scope: str) -> set[str]:
        return set(self.data.get(scope, {}))

    def forget(self, prefix: str) -> None:
        """Glem alt for et scope-præfiks (fx et barn, der er fjernet)."""
        for key in [k for k in self.data if k != self.SEEDED and k.startswith(prefix)]:
            del self.data[key]
        self.data[self.SEEDED] = [m for m in self.data.get(self.SEEDED, []) if not m.startswith(prefix)]

    def _prune(self, scope: str, now: datetime) -> None:
        cutoff = (now - self.retention).isoformat(timespec="seconds")
        known = self.data[scope]
        for key in [k for k, (_fp, seen) in known.items() if seen < cutoff]:
            del known[key]
