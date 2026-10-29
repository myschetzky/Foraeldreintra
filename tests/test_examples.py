"""Eksemplerne i examples/ valideres mod Home Assistant og integrationens rigtige entiteter."""

import re
from pathlib import Path

from homeassistant.components.automation.config import async_validate_config_item
from homeassistant.core import HomeAssistant
from homeassistant.helpers.template import Template
from homeassistant.util.yaml import load_yaml

from tests.test_integration import _setup, fake_client  # noqa: F401

EXAMPLES = Path(__file__).parent.parent / "examples"


async def test_automations_are_valid(hass: HomeAssistant):
    for auto in load_yaml(EXAMPLES / "automations.yaml"):
        assert await async_validate_config_item(hass, auto["id"], auto) is not None, auto["id"]


async def test_dashboard_and_digest_templates_render(hass: HomeAssistant, fake_client, freezer):  # noqa: F811
    freezer.move_to("2026-10-27 15:00:00+01:00")
    await _setup(hass)

    dashboard = load_yaml(EXAMPLES / "dashboard.yaml")
    assert dashboard["type"] == "vertical-stack"  # kan indsættes direkte som ét kort
    assert all("type" in card for card in dashboard["cards"])
    md_cards = dashboard["cards"][0]["cards"]
    storebror = Template(md_cards[0]["content"], hass).async_render(parse_result=False)
    assert "Engelsk" in storebror and "Til onsdag" in storebror
    lille = Template(md_cards[1]["content"], hass).async_render(parse_result=False)
    assert "Ugens opgaver" in lille and "Læs 20 min hver dag" in lille
    assert "hentet 27/10 kl. 15:00" in storebror

    for card in dashboard["cards"][1:]:
        for ent in card.get("entities", []):
            entity_id = ent if isinstance(ent, str) else ent["entity"]
            assert hass.states.get(entity_id) is not None, entity_id

    digest = next(a for a in load_yaml(EXAMPLES / "automations.yaml") if a["id"] == "skoleintra_lektier_til_i_morgen")
    text = Template(digest["actions"][0]["data"]["message"], hass).async_render(parse_result=False)
    assert re.search(r"Storebror: Engelsk, Tysk", text), text
    assert "Lillesøster: ingen" in text


async def test_dashboard_shows_hint_when_sensor_missing(hass: HomeAssistant):
    card = load_yaml(EXAMPLES / "dashboard.yaml")["cards"][0]["cards"][0]
    text = Template(card["content"], hass).async_render(parse_result=False)
    assert "Fandt ikke `sensor.storebror_lektier" in text


async def test_automations_send_expected_notifications(hass: HomeAssistant, fake_client, freezer):  # noqa: F811
    """Kør automatiseringerne rigtigt: event ind -> notify-kald ud."""
    from homeassistant.setup import async_setup_component
    from pytest_homeassistant_custom_component.common import async_mock_service

    freezer.move_to("2026-10-27 12:00:00+01:00")
    await _setup(hass)
    calls = async_mock_service(hass, "notify", "mobile_app_din_telefon")
    assert await async_setup_component(hass, "automation", {"automation": load_yaml(EXAMPLES / "automations.yaml")})
    await hass.async_block_till_done()

    base = {"entry_id": "x", "url": "https://skole/x"}
    hass.bus.async_fire("skoleintra_new_item", {**base, "barn": "Storebror", "kategori": "beskeder", "ændring": "ny",
                                                "nøgle": "t:1", "titel": "Tur", "afsender": "MH", "tekst": "Madpakke"})
    hass.bus.async_fire("skoleintra_new_item", {**base, "barn": "Storebror", "kategori": "kontaktbog", "ændring": "ny",
                                                "titel": "Kontaktbog: Lars", "afsender": "Lars", "tekst": "God bedring",
                                                "retning": "til_os"})
    hass.bus.async_fire("skoleintra_new_item", {**base, "barn": "Storebror", "kategori": "kontaktbog", "ændring": "ny",
                                                "titel": "Kontaktbog: Anna", "afsender": "Anna", "tekst": "Syg",
                                                "retning": "fra_os"})
    hass.bus.async_fire("skoleintra_new_item", {**base, "barn": "Lillesøster", "kategori": "ugeplan", "ændring": "ændret",
                                                "titel": "Dansk (28.10)", "tekst": "Læs"})
    await hass.async_block_till_done()

    got = [(c.data["title"], c.data["message"]) for c in calls]
    assert ("✉️ Tur", "MH: Madpakke") in got
    assert ("📒 Kontaktbog – Storebror", "Lars: God bedring") in got
    assert not any("Syg" in m for _, m in got)  # egne noter giver ingen notifikation
    assert ("📚 Lillesøster: ændret ugeplan", "Dansk (28.10) – Læs") in got
    assert next(c for c in calls if c.data["title"] == "✉️ Tur").data["data"]["tag"] == "skoleintra-t:1"
