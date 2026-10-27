"""Datamodeller. Ingen afhængighed af Home Assistant, så de kan testes og bruges i tools/probe.py."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any


def fingerprint(*parts: Any) -> str:
    """Stabil, kort hash af indhold — bruges til at opdage ændrede elementer."""
    raw = "\x1f".join("" if p is None else str(p) for p in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class Child:
    """Et barn på forældrekontoen. `path` er fx /parent/1234/Storebror."""

    child_id: str
    name: str
    path: str

    @property
    def slug(self) -> str:
        return f"{self.child_id}_{self.name}".lower()


@dataclass
class HomeworkItem:
    """Lektie fra lektiebog eller dagsindhold fra ugeplan."""

    day: date | None
    subject: str
    text: str
    source: str  # "lektiebog" | "ugeplan"
    links: list[dict[str, str]] = field(default_factory=list)
    day_label: str = ""

    @property
    def key(self) -> str:
        return f"{self.source}:{self.day.isoformat() if self.day else self.day_label}:{self.subject}"

    @property
    def fp(self) -> str:
        return fingerprint(self.text, [link.get("url") for link in self.links])

    def as_dict(self) -> dict[str, Any]:
        return {
            "dato": self.day.isoformat() if self.day else None,
            "dag": self.day_label,
            "fag": self.subject,
            "tekst": self.text,
            "kilde": self.source,
            "links": self.links,
        }


@dataclass
class WeekPlan:
    """Én ugeplan (typisk den aktuelle uge)."""

    week_id: str  # "44-2026"
    title: str
    url: str
    general: list[HomeworkItem] = field(default_factory=list)
    days: list[HomeworkItem] = field(default_factory=list)

    @property
    def key(self) -> str:
        return self.week_id

    @property
    def fp(self) -> str:
        return fingerprint(*(i.fp for i in self.general + self.days))

    def as_dict(self) -> dict[str, Any]:
        return {
            "uge": self.week_id,
            "titel": self.title,
            "url": self.url,
            "generelt": [i.as_dict() for i in self.general],
            "dage": [i.as_dict() for i in self.days],
        }


@dataclass
class MessageThread:
    thread_id: str
    latest_message_id: str
    subject: str
    sender: str
    date_text: str
    unread: bool
    participants: str = ""
    body: str = ""  # hentes kun for nye/ændrede tråde

    @property
    def key(self) -> str:
        return f"{self.thread_id or 'x'}:{self.latest_message_id}"

    @property
    def fp(self) -> str:
        return fingerprint(self.subject, self.latest_message_id)

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["body"] = self.body[:500]
        return data


@dataclass
class ContactNote:
    note_id: str
    author: str
    date_text: str
    text: str
    is_reply: bool
    seen_by: str = ""
    from_us: bool | None = None  # None = ukendt (sæt `own_names` i indstillinger)

    @property
    def key(self) -> str:
        return self.note_id or fingerprint(self.author, self.date_text, self.text[:200])

    @property
    def fp(self) -> str:
        return fingerprint(self.text)

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["text"] = self.text[:1000]
        return data


@dataclass
class CalendarItem:
    uid: str
    title: str
    start: datetime | date
    end: datetime | date
    all_day: bool
    description: str = ""
    location: str = ""

    @property
    def key(self) -> str:
        return self.uid or fingerprint(self.title, self.start)

    @property
    def fp(self) -> str:
        return fingerprint(self.title, self.start, self.end, self.description, self.location)
