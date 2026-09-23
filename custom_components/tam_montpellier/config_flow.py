"""Config flow for the TaM Montpellier integration."""

from __future__ import annotations

from typing import Any

from aiohttp import ClientError
from google.protobuf.message import DecodeError
import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    CONF_DIRECTION_ID,
    CONF_GTFS_URL,
    CONF_ROUTE_ID,
    CONF_STOP_ID,
    CONF_TRIP_UPDATES_URL,
    DEFAULT_GTFS_URL,
    DEFAULT_TRIP_UPDATES_URL,
    DOMAIN,
    SUBENTRY_TYPE_STOP,
)
from .coordinator import TamConfigEntry, async_fetch_trip_updates
from .departures import parse_trip_updates
from .gtfs_static import StaticData

_URL_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.URL))


class TamConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the setup of the TaM Montpellier integration."""

    VERSION = 1

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Return the subentries supported by this integration."""
        return {SUBENTRY_TYPE_STOP: StopSubentryFlowHandler}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm the data sources and check the real-time feed."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                payload = await async_fetch_trip_updates(
                    self.hass, user_input[CONF_TRIP_UPDATES_URL]
                )
                await self.hass.async_add_executor_job(parse_trip_updates, payload)
            except (ClientError, TimeoutError):
                errors["base"] = "cannot_connect"
            except DecodeError:
                errors["base"] = "invalid_feed"
            else:
                return self.async_create_entry(title="TaM Montpellier", data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(
                    {
                        vol.Required(CONF_TRIP_UPDATES_URL): _URL_SELECTOR,
                        vol.Required(CONF_GTFS_URL): _URL_SELECTOR,
                    }
                ),
                user_input
                or {
                    CONF_TRIP_UPDATES_URL: DEFAULT_TRIP_UPDATES_URL,
                    CONF_GTFS_URL: DEFAULT_GTFS_URL,
                },
            ),
            errors=errors,
        )


class StopSubentryFlowHandler(ConfigSubentryFlow):
    """Add a monitored stop: line, then direction, then stop."""

    _route_id: str
    _direction_id: int

    @property
    def _static(self) -> StaticData:
        entry: TamConfigEntry = self._get_entry()
        return entry.runtime_data.static

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Select the tram line."""
        if self._get_entry().state is not ConfigEntryState.LOADED:
            return self.async_abort(reason="entry_not_loaded")
        static = self._static
        if user_input is not None:
            self._route_id = user_input[CONF_ROUTE_ID]
            return await self.async_step_direction()

        routes = sorted(
            static.routes.values(),
            key=lambda route: (len(route.short_name), route.short_name),
        )
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ROUTE_ID): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(
                                    value=route.route_id,
                                    label=(
                                        f"Tram {route.short_name} · {route.long_name}"
                                    ),
                                )
                                for route in routes
                            ],
                            mode=SelectSelectorMode.LIST,
                        )
                    )
                }
            ),
        )

    async def async_step_direction(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Select the direction of travel."""
        static = self._static
        if user_input is not None and CONF_DIRECTION_ID in user_input:
            self._direction_id = int(user_input[CONF_DIRECTION_ID])
            return await self.async_step_stop()

        options: list[SelectOptionDict] = []
        for (route_id, direction_id), stops in sorted(static.line_stops.items()):
            if route_id != self._route_id or not stops:
                continue
            first = static.stop_names.get(stops[0], stops[0])
            label = static.direction_label(route_id, direction_id)
            options.append(
                SelectOptionDict(
                    value=str(direction_id), label=f"Vers {label} (depuis {first})"
                )
            )
        return self.async_show_form(
            step_id="direction",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_DIRECTION_ID): SelectSelector(
                        SelectSelectorConfig(
                            options=options, mode=SelectSelectorMode.LIST
                        )
                    )
                }
            ),
            description_placeholders={"line": self._line_name},
        )

    async def async_step_stop(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Select the stop."""
        static = self._static
        if user_input is not None and CONF_STOP_ID in user_input:
            stop_id = user_input[CONF_STOP_ID]
            unique_id = f"{stop_id}_{self._route_id}_{self._direction_id}"
            if any(
                subentry.unique_id == unique_id
                for subentry in self._get_entry().subentries.values()
            ):
                return self.async_abort(reason="already_configured")
            direction = static.direction_label(self._route_id, self._direction_id)
            return self.async_create_entry(
                title=(
                    f"{static.stop_names.get(stop_id, stop_id)} → {direction}"
                    f" ({self._line_name})"
                ),
                data={
                    CONF_ROUTE_ID: self._route_id,
                    CONF_DIRECTION_ID: self._direction_id,
                    CONF_STOP_ID: stop_id,
                },
                unique_id=unique_id,
            )

        stops = static.line_stops.get((self._route_id, self._direction_id), [])
        return self.async_show_form(
            step_id="stop",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_STOP_ID): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(
                                    value=stop_id,
                                    label=static.stop_names.get(stop_id, stop_id),
                                )
                                # The terminus is left out: trams do not depart
                                # from it in this direction.
                                for stop_id in stops[:-1]
                            ],
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
            description_placeholders={
                "line": self._line_name,
                "direction": static.direction_label(self._route_id, self._direction_id),
            },
        )

    @property
    def _line_name(self) -> str:
        route = self._static.routes.get(self._route_id)
        return f"Tram {route.short_name if route else self._route_id}"
