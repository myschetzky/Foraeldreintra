"""Opsætning via brugerfladen, re-autentificering og indstillinger pr. barn."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

import aiohttp
import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import SkoleIntraClient, normalize_school_url
from .const import (
    CONF_LOGIN_TYPE,
    CONF_PASSWORD,
    CONF_SCHOOL_URL,
    CONF_USERNAME,
    DEFAULT_CALENDAR_HOURS,
    DEFAULT_FAST_MINUTES,
    DEFAULT_QUIET_END,
    DEFAULT_QUIET_START,
    DEFAULT_SLOW_MINUTES,
    DOMAIN,
    LOGIN_TYPE_ALM,
    LOGIN_TYPE_UNI,
    OPT_CALENDAR_HOURS,
    OPT_CHILD_SOURCES,
    OPT_FAST_MINUTES,
    OPT_OWN_NAMES,
    OPT_QUIET_END,
    OPT_QUIET_START,
    OPT_SLOW_MINUTES,
    SOURCE_BOTH,
    SOURCE_DIARY,
    SOURCE_WEEKPLAN,
)
from .errors import AuthError, ManualActionRequired, SkoleIntraError, TransientError

_LOGGER = logging.getLogger(__name__)


async def _try_login(hass, data: Mapping[str, Any]) -> list[str]:
    session = async_create_clientsession(hass, cookie_jar=aiohttp.CookieJar())
    client = SkoleIntraClient(session, data[CONF_SCHOOL_URL], data[CONF_USERNAME], data[CONF_PASSWORD],
                              data.get(CONF_LOGIN_TYPE, LOGIN_TYPE_ALM))
    try:
        return [c.name for c in await client.login()]
    finally:
        await session.close()


def _error_key(err: Exception) -> str:
    if isinstance(err, AuthError):
        return "invalid_auth"
    if isinstance(err, ManualActionRequired):
        return "manual_action"
    if isinstance(err, TransientError):
        return "cannot_connect"
    return "unknown_layout"


class SkoleIntraConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input[CONF_SCHOOL_URL] = normalize_school_url(user_input[CONF_SCHOOL_URL])
            host = urlsplit(user_input[CONF_SCHOOL_URL]).netloc
            await self.async_set_unique_id(f"{host}:{user_input[CONF_USERNAME].lower()}")
            self._abort_if_unique_id_configured()
            try:
                children = await _try_login(self.hass, user_input)
            except SkoleIntraError as err:
                _LOGGER.warning("Login-test fejlede: %s", err)
                errors["base"] = _error_key(err)
            else:
                return self.async_create_entry(
                    title=f"{host.split('.')[0]} ({', '.join(children)})", data=user_input
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_SCHOOL_URL, default=(user_input or {}).get(
                    CONF_SCHOOL_URL, "https://vinderoedprivatskole.m.skoleintra.dk")): str,
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
                vol.Required(CONF_LOGIN_TYPE, default=LOGIN_TYPE_ALM): SelectSelector(
                    SelectSelectorConfig(options=[LOGIN_TYPE_ALM, LOGIN_TYPE_UNI], translation_key="login_type")
                ),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_import(self, import_data: dict[str, Any]) -> ConfigFlowResult:
        """Fra configuration.yaml (med !secret). Opdaterer en eksisterende post ved ny adgangskode."""
        import_data[CONF_SCHOOL_URL] = normalize_school_url(import_data[CONF_SCHOOL_URL])
        host = urlsplit(import_data[CONF_SCHOOL_URL]).netloc
        await self.async_set_unique_id(f"{host}:{import_data[CONF_USERNAME].lower()}")
        self._abort_if_unique_id_configured(updates=import_data)
        return self.async_create_entry(title=f"{host.split('.')[0]} (YAML)", data=import_data)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {**entry.data, CONF_PASSWORD: user_input[CONF_PASSWORD]}
            try:
                await _try_login(self.hass, data)
            except SkoleIntraError as err:
                errors["base"] = _error_key(err)
            else:
                return self.async_update_reload_and_abort(entry, data=data)
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {vol.Required(CONF_PASSWORD): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))}
            ),
            description_placeholders={"username": entry.data[CONF_USERNAME]},
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return SkoleIntraOptionsFlow()


class SkoleIntraOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        coordinator = getattr(self.config_entry, "runtime_data", None)
        children = [c.name for c in coordinator.client.children] if coordinator else []
        opts = self.config_entry.options
        sources = opts.get(OPT_CHILD_SOURCES, {})

        if user_input is not None:
            new_sources = {name: user_input.pop(f"kilde_{name}") for name in children}
            return self.async_create_entry(data={**user_input, OPT_CHILD_SOURCES: new_sources})

        source_selector = SelectSelector(
            SelectSelectorConfig(options=[SOURCE_DIARY, SOURCE_WEEKPLAN, SOURCE_BOTH], translation_key="source")
        )

        def num(lo: int, hi: int) -> NumberSelector:
            return NumberSelector(NumberSelectorConfig(min=lo, max=hi, step=1, mode="box"))

        fields: dict[Any, Any] = {
            vol.Required(f"kilde_{name}", default=sources.get(name, SOURCE_BOTH)): source_selector
            for name in children
        }
        fields.update(
            {
                vol.Optional(OPT_OWN_NAMES, default=opts.get(OPT_OWN_NAMES, "")): str,
                vol.Required(OPT_FAST_MINUTES, default=opts.get(OPT_FAST_MINUTES, DEFAULT_FAST_MINUTES)): num(10, 240),
                vol.Required(OPT_SLOW_MINUTES, default=opts.get(OPT_SLOW_MINUTES, DEFAULT_SLOW_MINUTES)): num(30, 720),
                vol.Required(OPT_CALENDAR_HOURS, default=opts.get(OPT_CALENDAR_HOURS, DEFAULT_CALENDAR_HOURS)): num(1, 48),
                vol.Required(OPT_QUIET_START, default=opts.get(OPT_QUIET_START, DEFAULT_QUIET_START)): num(0, 23),
                vol.Required(OPT_QUIET_END, default=opts.get(OPT_QUIET_END, DEFAULT_QUIET_END)): num(0, 23),
            }
        )
        return self.async_show_form(step_id="init", data_schema=vol.Schema(fields),
                                    description_placeholders={"children": ", ".join(children) or "-"})
