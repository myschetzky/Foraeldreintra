"""SkoleIntra / ForældreIntra for Home Assistant."""

from __future__ import annotations

import logging

import aiohttp
import voluptuous as vol
from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import SkoleIntraClient
from .const import (
    CATEGORIES,
    CONF_LOGIN_TYPE,
    CONF_PASSWORD,
    CONF_SCHOOL_URL,
    CONF_USERNAME,
    DOMAIN,
    LOGIN_TYPE_ALM,
    LOGIN_TYPE_UNI,
)
from .coordinator import SkoleIntraCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR, Platform.CALENDAR]

# Valgfri YAML-opsætning, så loginoplysninger kan ligge i secrets.yaml:
#   skoleintra:
#     - school_url: https://vinderoedprivatskole.m.skoleintra.dk
#       username: !secret skoleintra_username
#       password: !secret skoleintra_password
CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.All(
            cv.ensure_list,
            [
                vol.Schema(
                    {
                        vol.Required(CONF_SCHOOL_URL): cv.string,
                        vol.Required(CONF_USERNAME): cv.string,
                        vol.Required(CONF_PASSWORD): cv.string,
                        vol.Optional(CONF_LOGIN_TYPE, default=LOGIN_TYPE_ALM): vol.In(
                            [LOGIN_TYPE_ALM, LOGIN_TYPE_UNI]
                        ),
                    }
                )
            ],
        )
    },
    extra=vol.ALLOW_EXTRA,
)

type SkoleIntraConfigEntry = ConfigEntry[SkoleIntraCoordinator]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    for conf in config.get(DOMAIN, []):
        hass.async_create_task(
            hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_IMPORT}, data=dict(conf))
        )

    async def _refresh(call: ServiceCall) -> None:
        category = call.data.get("kategori")
        for entry in hass.config_entries.async_loaded_entries(DOMAIN):
            entry.runtime_data.request_refresh(category)
            await entry.runtime_data.async_request_refresh()

    hass.services.async_register(
        DOMAIN, "opdater", _refresh, schema=vol.Schema({vol.Optional("kategori"): vol.In(CATEGORIES)})
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: SkoleIntraConfigEntry) -> bool:
    # Egen cookie-jar: sessionen deles ikke med andre integrationer.
    session = async_create_clientsession(hass, cookie_jar=aiohttp.CookieJar())
    client = SkoleIntraClient(
        session,
        entry.data[CONF_SCHOOL_URL],
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        entry.data.get(CONF_LOGIN_TYPE, LOGIN_TYPE_ALM),
    )
    coordinator = SkoleIntraCoordinator(hass, entry, client)
    await coordinator.async_load()
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_options_updated))
    return True


async def _options_updated(hass: HomeAssistant, entry: SkoleIntraConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: SkoleIntraConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
