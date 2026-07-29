"""Config flow for the Freebox Ultra integration.

Pairing is a three-legged dance:

1. `POST /login/authorize/` returns an `app_token` and a `track_id`;
2. the user physically validates the request on the box's screen while we poll
   `GET /login/authorize/{track_id}`;
3. the `app_token` is exchanged for a session token, which proves it works.

Step 2 can take a minute, so it runs as a background task behind
`async_show_progress` rather than blocking the flow handler.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
import logging
import socket
from typing import Any

from homeassistant.config_entries import (
    SOURCE_REAUTH,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
import voluptuous as vol

from .api import BoxDescriptor, FreeboxClient, async_discover, async_probe
from .const import (
    CATEGORY_META,
    CONF_APP_TOKEN,
    DEFAULT_CATEGORIES,
    DEFAULT_CONSIDER_HOME,
    DEFAULT_HOST,
    DEFAULT_PORT,
    DOMAIN,
    OPT_CATEGORIES,
    OPT_CONSIDER_HOME,
    OPT_SCAN_INTERVALS,
    OPT_TRACK_NEW_DEVICES,
    Category,
)
from .exceptions import (
    FreeboxAuthDenied,
    FreeboxConnectionError,
    FreeboxError,
    FreeboxInvalidToken,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST, default=DEFAULT_HOST): str,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
    }
)


class FreeboxUltraConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle pairing, reauth and reconfiguration."""

    VERSION = 1
    MINOR_VERSION = 1

    def __init__(self) -> None:
        """Initialize the mutable state carried across steps."""
        self._descriptor: BoxDescriptor | None = None
        self._host: str | None = None
        self._port: int | None = None
        self._app_token: str | None = None
        self._permissions: dict[str, bool] = {}
        self._auth_task: asyncio.Task[None] | None = None
        self._auth_error: str | None = None


    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manual setup, pre-filled with whatever autodiscovery finds."""
        if user_input is None:
            suggested: dict[str, Any] = {
                CONF_HOST: DEFAULT_HOST,
                CONF_PORT: DEFAULT_PORT,
            }
            try:
                descriptor = await async_discover(self.hass)
            except FreeboxConnectionError as err:
                _LOGGER.debug("Autodiscovery failed, manual entry needed: %s", err)
            else:
                suggested = {
                    CONF_HOST: descriptor.api_domain,
                    CONF_PORT: descriptor.https_port,
                }
            return self.async_show_form(
                step_id="user",
                data_schema=self.add_suggested_values_to_schema(
                    STEP_USER_SCHEMA, suggested
                ),
            )

        host = user_input[CONF_HOST]
        port = int(user_input[CONF_PORT])
        try:
            descriptor = await async_probe(self.hass, host, port)
        except FreeboxConnectionError:
            return self.async_show_form(
                step_id="user",
                data_schema=self.add_suggested_values_to_schema(
                    STEP_USER_SCHEMA, user_input
                ),
                errors={"base": "cannot_connect"},
            )

        if not descriptor.is_supported:
            return self.async_abort(reason="unsupported_api_version")

        await self.async_set_unique_id(descriptor.uid)
        self._abort_if_unique_id_configured(
            updates={CONF_HOST: host, CONF_PORT: port}
        )
        self._descriptor = descriptor
        self._host = host
        self._port = port
        return await self.async_step_link()

    async def async_step_zeroconf(
        self, discovery_info: ZeroconfServiceInfo
    ) -> ConfigFlowResult:
        """Handle discovery of `_fbx-api._tcp.local.`.

        The gateway certificate is issued for `<uid>.fbxos.fr`, so we keep the
        advertised `api_domain` and ignore the discovered IP address.
        """
        try:
            descriptor = BoxDescriptor.from_zeroconf(discovery_info.properties)
        except (KeyError, TypeError, ValueError) as err:
            _LOGGER.debug("Unusable mDNS record %s: %s", discovery_info, err)
            return self.async_abort(reason="invalid_discovery_info")

        if not descriptor.https_available:
            return self.async_abort(reason="https_not_available")
        if not descriptor.is_supported:
            return self.async_abort(reason="unsupported_api_version")

        await self.async_set_unique_id(descriptor.uid)
        self._abort_if_unique_id_configured(
            updates={
                CONF_HOST: descriptor.api_domain,
                CONF_PORT: descriptor.https_port,
            }
        )

        self._descriptor = descriptor
        self._host = descriptor.api_domain
        self._port = descriptor.https_port
        self.context["title_placeholders"] = {"name": self._title()}
        return await self.async_step_link()

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Restart pairing after the stored app_token was revoked."""
        entry = self._get_reauth_entry()
        self._host = entry.data[CONF_HOST]
        self._port = entry.data[CONF_PORT]
        self._descriptor = BoxDescriptor.from_api(entry.data)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask the user to get ready, then restart the pairing dance."""
        if user_input is None:
            return self.async_show_form(
                step_id="reauth_confirm",
                data_schema=vol.Schema({}),
                errors={"base": self._auth_error} if self._auth_error else None,
                description_placeholders={"name": self._title()},
            )
        self._auth_error = None
        return await self.async_step_authorize()

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the host/port of an existing entry without re-pairing."""
        entry = self._get_reconfigure_entry()
        if user_input is None:
            return self.async_show_form(
                step_id="reconfigure",
                data_schema=self.add_suggested_values_to_schema(
                    STEP_USER_SCHEMA,
                    {
                        CONF_HOST: entry.data[CONF_HOST],
                        CONF_PORT: entry.data[CONF_PORT],
                    },
                ),
            )

        host = user_input[CONF_HOST]
        port = int(user_input[CONF_PORT])
        try:
            descriptor = await async_probe(self.hass, host, port)
        except FreeboxConnectionError:
            return self.async_show_form(
                step_id="reconfigure",
                data_schema=self.add_suggested_values_to_schema(
                    STEP_USER_SCHEMA, user_input
                ),
                errors={"base": "cannot_connect"},
            )

        await self.async_set_unique_id(descriptor.uid)
        self._abort_if_unique_id_mismatch(reason="wrong_box")
        return self.async_update_reload_and_abort(
            entry,
            data_updates={
                CONF_HOST: host,
                CONF_PORT: port,
                **descriptor.as_entry_data(),
            },
        )


    async def async_step_link(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Tell the user to be in front of the box, then start pairing."""
        if user_input is None:
            return self.async_show_form(
                step_id="link",
                data_schema=vol.Schema({}),
                errors={"base": self._auth_error} if self._auth_error else None,
                description_placeholders={"name": self._title()},
            )
        self._auth_error = None
        return await self.async_step_authorize()

    async def async_step_authorize(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show a spinner while the user validates the request on the box."""
        if self._auth_task is None:
            self._auth_task = self.hass.async_create_task(
                self._async_authorize(), eager_start=False
            )

        if not self._auth_task.done():
            return self.async_show_progress(
                step_id="authorize",
                progress_action="wait_for_validation",
                progress_task=self._auth_task,
                description_placeholders={"name": self._title()},
            )
        return self.async_show_progress_done(next_step_id="finish")

    async def _async_authorize(self) -> None:
        """Run the full pairing dance. Never raises: sets `_auth_error`."""
        assert self._descriptor is not None
        client = await FreeboxClient.async_create(
            self.hass, self._descriptor, host=self._host, port=self._port
        )
        try:
            app_token, track_id = await client.async_request_app_token(
                self.hass.config.location_name or socket.gethostname()
            )
            await client.async_wait_for_authorization(track_id)
            client.app_token = app_token
            await client.async_open_session()
        except FreeboxAuthDenied:
            self._auth_error = "authorization_denied"
        except FreeboxInvalidToken:
            self._auth_error = "invalid_auth"
        except FreeboxConnectionError:
            self._auth_error = "cannot_connect"
        except FreeboxError:
            _LOGGER.exception("Unexpected error while pairing")
            self._auth_error = "unknown"
        else:
            self._app_token = app_token
            self._permissions = client.permissions

    async def async_step_finish(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Persist the token, or send the user back to retry."""
        if self._auth_error or not self._app_token:
            self._auth_task = None
            if self.source == SOURCE_REAUTH:
                return await self.async_step_reauth_confirm()
            return await self.async_step_link()

        if self.source == SOURCE_REAUTH:
            return self.async_update_reload_and_abort(
                self._get_reauth_entry(),
                data_updates={CONF_APP_TOKEN: self._app_token},
            )

        assert self._descriptor is not None
        return self.async_create_entry(
            title=self._title(),
            data={
                CONF_HOST: self._host,
                CONF_PORT: self._port,
                CONF_APP_TOKEN: self._app_token,
                **self._descriptor.as_entry_data(),
            },
            options={
                OPT_CATEGORIES: [category.value for category in DEFAULT_CATEGORIES],
                OPT_SCAN_INTERVALS: {},
                OPT_TRACK_NEW_DEVICES: True,
                OPT_CONSIDER_HOME: DEFAULT_CONSIDER_HOME,
            },
        )

    def _title(self) -> str:
        """Human-readable name for the box being paired."""
        if self._descriptor is None:
            return "Freebox"
        return (
            self._descriptor.box_model_name
            or self._descriptor.device_name
            or "Freebox Ultra"
        )


    @staticmethod
    @callback
    def async_get_options_flow(config_entry: Any) -> OptionsFlowWithReload:
        """Return the options flow handler."""
        return FreeboxUltraOptionsFlow()


class FreeboxUltraOptionsFlow(OptionsFlowWithReload):
    """Let the user pick which data categories to poll, and how often."""

    def __init__(self) -> None:
        """Initialize the pending options."""
        self._options: dict[str, Any] = {}

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose the enabled categories."""
        current = self.config_entry.options
        if user_input is not None:
            self._options = {**current, **user_input}
            return await self.async_step_intervals()

        schema = vol.Schema(
            {
                vol.Required(OPT_CATEGORIES): SelectSelector(
                    SelectSelectorConfig(
                        options=[category.value for category in Category],
                        multiple=True,
                        mode=SelectSelectorMode.LIST,
                        translation_key="category",
                    )
                ),
                vol.Required(OPT_TRACK_NEW_DEVICES): bool,
                vol.Required(OPT_CONSIDER_HOME): NumberSelector(
                    NumberSelectorConfig(
                        min=0, max=3600, step=10, unit_of_measurement="s",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                schema,
                {
                    OPT_CATEGORIES: current.get(
                        OPT_CATEGORIES,
                        [category.value for category in DEFAULT_CATEGORIES],
                    ),
                    OPT_TRACK_NEW_DEVICES: current.get(OPT_TRACK_NEW_DEVICES, True),
                    OPT_CONSIDER_HOME: current.get(
                        OPT_CONSIDER_HOME, DEFAULT_CONSIDER_HOME
                    ),
                },
            ),
        )

    async def async_step_intervals(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Override the refresh interval of each enabled category."""
        enabled = [
            category
            for category in Category
            if category.value in self._options.get(OPT_CATEGORIES, [])
        ]
        if user_input is not None:
            self._options[OPT_SCAN_INTERVALS] = {
                key: int(value) for key, value in user_input.items()
            }
            return self.async_create_entry(data=self._options)

        stored = self.config_entry.options.get(OPT_SCAN_INTERVALS, {})
        schema = vol.Schema(
            {
                vol.Required(
                    category.value,
                    default=stored.get(
                        category.value,
                        int(CATEGORY_META[category].interval.total_seconds()),
                    ),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=5, max=3600, step=5, unit_of_measurement="s",
                        mode=NumberSelectorMode.BOX,
                    )
                )
                for category in enabled
            }
        )
        return self.async_show_form(step_id="intervals", data_schema=schema)
