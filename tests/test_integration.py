"""Fuld Home Assistant-test: config flow, entiteter, events og re-autentificering."""

from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.skoleintra import parsers
from custom_components.skoleintra.const import DOMAIN, EVENT_NEW_ITEM, OPT_CHILD_SOURCES, OPT_OWN_NAMES
from custom_components.skoleintra.errors import AuthError, LayoutError, TransientError
from custom_components.skoleintra.models import Child
from tests import fixtures as fx

STOREBROR = Child("1111", "Storebror", "/parent/1111/Storebror")
LILLE = Child("2222", "Lillesøster", "/parent/2222/Lilles%C3%B8ster")
DATA = {"school_url": "https://vinderoedprivatskole.m.skoleintra.dk", "username": "forælder",
        "password": "hemmelig", "login_type": "alm"}


class FakeClient:
    """Erstatter netværksdelen med de rigtige parsere på fixture-sider."""

    def __init__(self, *args, **kwargs):
        self.base_url = DATA["school_url"]
        self.children = []
        self.threads = list(fx.THREADS)
        self.weeks = ["44-2026"]
        self.login_calls = 0
        self.fail_with = None

    async def login(self):
        self.login_calls += 1
        self._check()
        self.children = [STOREBROR, LILLE]
        return self.children

    def _check(self):
        if self.fail_with:
            raise self.fail_with

    async def get_diary(self, child):
        self._check()
        return parsers.parse_diary(fx.DIARY_NOTES_TABLE)

    async def get_weekplans(self, child, today=None):
        self._check()
        return [parsers.parse_weekplan(fx.weekplan_page(), w, "u") for w in self.weeks]

    async def get_messages(self, child, known=None):
        self._check()
        threads = parsers.parse_conversations(fx.conversations_page(self.threads))
        for t in threads:
            t.body = "Husk madpakke"
        return threads

    async def get_contact_book(self, child, own_names=None):
        self._check()
        return parsers.parse_contact_book(fx.CONTACT_BOOK, own_names)

    async def get_calendar(self, child, start, end):
        self._check()
        return parsers.parse_calendar(fx.CALENDAR_JSON)

    def diagnostics(self):
        return {"base_url": self.base_url}


@pytest.fixture
def fake_client():
    client = FakeClient()
    with patch("custom_components.skoleintra.SkoleIntraClient", return_value=client):
        yield client


async def _setup(hass: HomeAssistant, options=None) -> MockConfigEntry:
    await hass.config.async_set_time_zone("Europe/Copenhagen")
    entry = MockConfigEntry(domain=DOMAIN, data=DATA, unique_id="x:forælder", options=options or {
        OPT_CHILD_SOURCES: {"Storebror": "lektiebog", "Lillesøster": "ugeplan"},
        OPT_OWN_NAMES: "Anna Forælder",
    })
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_config_flow_success_and_bad_password(hass: HomeAssistant):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    with patch("custom_components.skoleintra.config_flow._try_login", AsyncMock(side_effect=AuthError("nej"))):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], DATA)
    assert result["errors"] == {"base": "invalid_auth"}
    with patch("custom_components.skoleintra.config_flow._try_login",
               AsyncMock(return_value=["Storebror", "Lillesøster"])), \
            patch("custom_components.skoleintra.async_setup_entry", AsyncMock(return_value=True)):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], DATA)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "vinderoedprivatskole (Storebror, Lillesøster)"


async def test_entities_per_child(hass: HomeAssistant, fake_client, freezer: FrozenDateTimeFactory):
    freezer.move_to("2026-10-27 09:00:00+01:00")
    await _setup(hass)

    today = hass.states.get("sensor.storebror_lektier_i_dag")
    assert today.state == "2"  # Dansk + Matematik fra lektiebogen
    assert "Opgave 4.1-4.6" in today.attributes["markdown"]
    assert hass.states.get("sensor.storebror_lektier_naeste_skoledag").state == "2"  # Engelsk + Tysk
    assert hass.states.get("sensor.storebror_ugeplan") is None  # kun lektiebog for storebror

    lille_today = hass.states.get("sensor.lillesoster_lektier_i_dag")
    assert lille_today.state == "1" and lille_today.attributes["lektier"][0]["fag"] == "Natur/teknologi"
    assert hass.states.get("sensor.lillesoster_ugeplan").state == "Uge 44"
    assert hass.states.get("sensor.storebror_lektier_i_dag").attributes["lektier"][0]["kilde"] == "lektiebog"

    assert hass.states.get("sensor.storebror_ulaeste_beskeder").state == "1"
    kb = hass.states.get("sensor.storebror_kontaktbog")
    assert kb.state == "26. okt. 2026" and kb.attributes["noter"][1]["from_us"] is True
    assert hass.states.get("calendar.lillesoster_skolekalender") is not None
    assert hass.states.get("sensor.skoleintra_status").state == "ok"


async def test_new_message_fires_one_event(hass: HomeAssistant, fake_client, freezer):
    freezer.move_to("2026-10-27 09:00:00+01:00")
    entry = await _setup(hass)
    events = []
    hass.bus.async_listen(EVENT_NEW_ITEM, events.append)

    fake_client.threads.insert(0, {"ThreadId": "cccc-3", "LatestMessageId": 800, "Subject": "Sygdom i klassen",
                                   "IsUnread": True, "SenderName": "Kontoret"})
    await hass.services.async_call(DOMAIN, "opdater", {"kategori": "beskeder"}, blocking=True)
    await hass.async_block_till_done()

    msgs = [e.data for e in events if e.data["kategori"] == "beskeder"]
    assert len(msgs) == 1  # én gang, selv om tråden ses under begge børn
    assert msgs[0]["titel"] == "Sygdom i klassen" and msgs[0]["ændring"] == "ny"
    assert msgs[0]["url"].endswith("/messages/conversations")
    assert entry.runtime_data.status["1111_storebror:beskeder"].failures == 0


async def test_layout_error_keeps_data_and_marks_status(hass: HomeAssistant, fake_client, freezer):
    freezer.move_to("2026-10-27 09:00:00+01:00")
    await _setup(hass)
    fake_client.fail_with = LayoutError("Lektiebog: ny side")
    await hass.services.async_call(DOMAIN, "opdater", {}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.storebror_lektier_i_dag").state == "2"  # sidst kendte data bevares
    status = hass.states.get("sensor.skoleintra_status")
    assert status.state == "delvis fejl"
    assert status.attributes["kategorier"]["1111_storebror:lektiebog"]["fejltype"] == "layout"


async def test_auth_error_starts_reauth(hass: HomeAssistant, fake_client, freezer):
    freezer.move_to("2026-10-27 09:00:00+01:00")
    entry = await _setup(hass)
    fake_client.fail_with = AuthError("udløbet kode")
    await hass.services.async_call(DOMAIN, "opdater", {}, blocking=True)
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress()
    assert any(f["context"]["source"] == "reauth" and f["context"]["entry_id"] == entry.entry_id for f in flows)


async def test_yaml_import_with_secrets(hass: HomeAssistant, fake_client):
    assert await async_setup_component(hass, DOMAIN, {DOMAIN: [DATA]})
    await hass.async_block_till_done()
    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1 and entries[0].data["username"] == "forælder"


async def test_new_weekplan_is_one_notification(hass: HomeAssistant, fake_client, freezer):
    freezer.move_to("2026-10-27 09:00:00+01:00")
    await _setup(hass)
    events = []
    hass.bus.async_listen(EVENT_NEW_ITEM, events.append)
    fake_client.weeks.append("45-2026")  # næste uges plan lægges op
    await hass.services.async_call(DOMAIN, "opdater", {"kategori": "ugeplan"}, blocking=True)
    await hass.async_block_till_done()
    assert [e.data["titel"] for e in events] == ["Ny ugeplan: Ugeplan 4.s uge 45"]
    assert events[0].data["barn"] == "Lillesøster"


async def test_login_outage_backs_off(hass: HomeAssistant, fake_client, freezer):
    freezer.move_to("2026-10-27 09:00:00+01:00")
    entry = await _setup(hass)
    coordinator = entry.runtime_data
    fake_client.children = []  # session tabt
    fake_client.fail_with = TransientError("skolen er nede")
    await coordinator.async_refresh()
    await coordinator.async_refresh()  # inden for backoff-vinduet: intet nyt loginforsøg
    assert fake_client.login_calls == 2  # 1 ved opstart + 1 forsøg
    assert hass.states.get("sensor.skoleintra_status").state == "fejl"
    assert hass.states.get("sensor.storebror_lektier_i_dag").state == "2"  # gamle data vises stadig
    freezer.tick(timedelta(minutes=6))
    fake_client.fail_with = None
    await coordinator.async_refresh()
    assert fake_client.login_calls == 3
    assert hass.states.get("sensor.skoleintra_status").state == "ok"
