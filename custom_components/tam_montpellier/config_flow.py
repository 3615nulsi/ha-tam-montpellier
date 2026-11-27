"""Config flow for the TaM Montpellier integration."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from aiohttp import ClientError, ClientTimeout
from google.protobuf.message import DecodeError
import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
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
    CONF_EXTRA_ROUTES,
    CONF_GTFS_URL,
    CONF_INCLUDE_ADDED_TRIPS,
    CONF_LEAVE_WINDOW,
    CONF_ROUTE_ID,
    CONF_STOP_ID,
    CONF_TRIP_UPDATES_URL,
    CONF_WALK_MINUTES,
    DEFAULT_LEAVE_WINDOW,
    DEFAULT_URLS,
    DOMAIN,
    REQUEST_TIMEOUT,
    SUBENTRY_TYPE_STOP,
)
from .coordinator import TamConfigEntry, async_fetch_feed, stop_key_from_data
from .departures import parse_trip_updates
from .gtfs_static import StaticData
from .labels import Labels, async_get_labels, line_kind, rotation

_URL_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.URL))

# The data source addresses, folded in a section: the defaults are right
# unless the operator moves its feeds.
SECTION_ADVANCED = "advanced"
# First bytes of a zip archive.
_ZIP_SIGNATURE = b"PK\x03\x04"


def _urls_schema(urls: Mapping[str, Any], collapsed: bool) -> vol.Schema:
    """Return the form of the data source addresses, filled with ``urls``."""
    return vol.Schema(
        {
            vol.Required(SECTION_ADVANCED): section(
                vol.Schema(
                    {
                        vol.Required(key, default=urls.get(key, default)): (
                            _URL_SELECTOR
                        )
                        for key, default in DEFAULT_URLS.items()
                    }
                ),
                {"collapsed": collapsed},
            )
        }
    )


async def _async_check_trip_updates(hass: HomeAssistant, url: str) -> str | None:
    """Return the error of the real-time feed, None when it is valid."""
    try:
        payload = await async_fetch_feed(hass, url)
        await hass.async_add_executor_job(parse_trip_updates, payload)
    except (ClientError, TimeoutError):
        return "cannot_connect"
    except DecodeError:
        return "invalid_feed"
    return None


async def _async_check_gtfs(hass: HomeAssistant, url: str) -> str | None:
    """Return the error of the timetable archive, None when it looks valid.

    Only the first bytes are read: the archive weighs several megabytes.
    """
    session = async_get_clientsession(hass)
    try:
        async with session.get(
            url, timeout=ClientTimeout(total=REQUEST_TIMEOUT)
        ) as resp:
            resp.raise_for_status()
            start = await resp.content.readexactly(len(_ZIP_SIGNATURE))
    except asyncio.IncompleteReadError:
        return "invalid_gtfs"
    except (ClientError, TimeoutError):
        return "gtfs_cannot_connect"
    return None if start == _ZIP_SIGNATURE else "invalid_gtfs"


# Walk to the stop and "time to leave" window, in minutes.
_LEAVE_FIELDS: dict[vol.Marker, NumberSelector] = {
    vol.Optional(CONF_WALK_MINUTES): NumberSelector(
        NumberSelectorConfig(
            min=0,
            max=60,
            step=1,
            mode=NumberSelectorMode.BOX,
            unit_of_measurement="min",
        )
    ),
    vol.Optional(CONF_LEAVE_WINDOW): NumberSelector(
        NumberSelectorConfig(
            min=1,
            max=15,
            step=1,
            mode=NumberSelectorMode.BOX,
            unit_of_measurement="min",
        )
    ),
}


def _leave_data(user_input: Mapping[str, Any]) -> dict[str, int]:
    """Return the walk and window given in a form, as whole minutes."""
    return {
        key: int(user_input[key])
        for key in (CONF_WALK_MINUTES, CONF_LEAVE_WINDOW)
        if user_input.get(key) is not None
    }


async def _async_check_urls(hass: HomeAssistant, urls: Mapping[str, str]) -> str | None:
    """Return the error of the data sources, None when they can be used.

    The service alerts and vehicle positions are optional: the departures
    are shown without them.
    """
    if error := await _async_check_trip_updates(hass, urls[CONF_TRIP_UPDATES_URL]):
        return error
    return await _async_check_gtfs(hass, urls[CONF_GTFS_URL])


def _direction_option(
    labels: Labels, static: StaticData, route_id: str, direction_id: int
) -> str:
    """Label a direction: its destination, or its way round for a circular line.

    Both directions of a circular line start and end at the same stop, so
    they are told apart by their way round and the stops they pass through:
    "4a · sens horaire (Rondelet → Albert 1er → Place de l'Europe)".
    """
    stops = static.line_stops[(route_id, direction_id)]
    names = [static.stop_names.get(stop_id, stop_id) for stop_id in stops]
    if (way := rotation(labels, static, route_id, direction_id)) is None:
        return labels["towards"].format(
            direction=static.direction_label(route_id, direction_id), origin=names[0]
        )
    # Three stops spread along the loop, the same ones in both directions,
    # without their second name.
    last = len(names) - 1
    via = " → ".join(
        names[index].split(" - ")[0]
        for index in (last // 4, last // 2, last - last // 4)
    )
    return f"{static.line_name(route_id, direction_id)} · {way} ({via})"


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

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow, which picks the bus lines to offer."""
        return TamOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm the data sources and check the real-time feed and timetables."""
        errors: dict[str, str] = {}
        urls: Mapping[str, Any] = DEFAULT_URLS
        if user_input is not None:
            urls = user_input[SECTION_ADVANCED]
            if error := await _async_check_urls(self.hass, urls):
                errors["base"] = error
            else:
                return self.async_create_entry(title="TaM Montpellier", data=dict(urls))

        return self.async_show_form(
            step_id="user",
            # Unfolded when an address is wrong, for the user to see it.
            data_schema=_urls_schema(urls, collapsed=not errors),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the data source addresses, keeping the followed stops."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        urls: Mapping[str, Any] = {**DEFAULT_URLS, **entry.data}
        if user_input is not None:
            urls = user_input[SECTION_ADVANCED]
            if error := await _async_check_urls(self.hass, urls):
                errors["base"] = error
            else:
                return self.async_update_reload_and_abort(
                    entry, data_updates=dict(urls)
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_urls_schema(urls, collapsed=False),
            errors=errors,
        )


class TamOptionsFlow(OptionsFlow):
    """Choose the bus lines offered next to the tram lines, and the added runs."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select the bus lines."""
        entry: TamConfigEntry = self.config_entry
        if entry.state is not ConfigEntryState.LOADED:
            return self.async_abort(reason="entry_not_loaded")
        static = entry.runtime_data.static
        chosen = set(entry.options.get(CONF_EXTRA_ROUTES, []))
        include_added = entry.options.get(CONF_INCLUDE_ADDED_TRIPS, False)
        # A line with a followed stop cannot be removed.
        in_use = {
            subentry.data[CONF_ROUTE_ID] for subentry in entry.subentries.values()
        }
        errors: dict[str, str] = {}
        if user_input is not None:
            selected = set(user_input[CONF_EXTRA_ROUTES])
            if missing := (in_use & chosen) - selected:
                errors["base"] = "route_in_use"
                # A line removed from the GTFS is named after its id.
                names = ", ".join(
                    sorted(
                        static.routes[route_id].short_name
                        if route_id in static.routes
                        else route_id
                        for route_id in missing
                    )
                )
                placeholders = {"lines": names}
            else:
                return self.async_create_entry(
                    data={
                        CONF_EXTRA_ROUTES: sorted(selected),
                        CONF_INCLUDE_ADDED_TRIPS: user_input.get(
                            CONF_INCLUDE_ADDED_TRIPS, False
                        ),
                    }
                )
        else:
            placeholders = {"lines": ""}

        buses = [
            route
            for route in (*static.routes.values(), *static.other_routes.values())
            if not route.is_tram
        ]
        buses.sort(key=lambda route: (len(route.short_name), route.short_name))
        # Lines removed from the GTFS can no longer be selected.
        chosen &= {route.route_id for route in buses}
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(
                    {
                        # Optional: no line at all brings back the tram alone.
                        vol.Optional(CONF_EXTRA_ROUTES, default=[]): SelectSelector(
                            SelectSelectorConfig(
                                options=[
                                    SelectOptionDict(
                                        value=route.route_id,
                                        label=(
                                            f"Bus {route.short_name}"
                                            f" · {route.long_name}"
                                        ),
                                    )
                                    for route in buses
                                ],
                                multiple=True,
                                mode=SelectSelectorMode.DROPDOWN,
                            )
                        ),
                        vol.Optional(
                            CONF_INCLUDE_ADDED_TRIPS, default=False
                        ): BooleanSelector(),
                    }
                ),
                {
                    CONF_EXTRA_ROUTES: sorted(user_input[CONF_EXTRA_ROUTES]),
                    CONF_INCLUDE_ADDED_TRIPS: user_input.get(
                        CONF_INCLUDE_ADDED_TRIPS, False
                    ),
                }
                if user_input
                else {
                    CONF_EXTRA_ROUTES: sorted(chosen),
                    CONF_INCLUDE_ADDED_TRIPS: include_added,
                },
            ),
            errors=errors,
            description_placeholders=placeholders,
        )


class StopSubentryFlowHandler(ConfigSubentryFlow):
    """Add a monitored stop: line, then direction, then stop."""

    _route_id: str
    _direction_id: int
    _labels: Labels

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
        self._labels = await async_get_labels(self.hass)
        if user_input is not None:
            self._route_id = user_input[CONF_ROUTE_ID]
            return await self.async_step_direction()

        labels = self._labels
        routes = sorted(
            static.routes.values(),
            key=lambda route: (
                not route.is_tram,
                len(route.short_name),
                route.short_name,
            ),
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
                                        f"{line_kind(labels, static, route.route_id)}"
                                        f" {route.short_name} · {route.long_name}"
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
            options.append(
                SelectOptionDict(
                    value=str(direction_id),
                    label=_direction_option(
                        self._labels, static, route_id, direction_id
                    ),
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
            stop_name = static.stop_names.get(stop_id, stop_id)
            # A stop is followed on all its platforms: another one of the
            # same name is the same stop.
            if any(
                stop_key_from_data(subentry.data)[1:]
                == (self._route_id, self._direction_id)
                and static.stop_names.get(
                    subentry.data[CONF_STOP_ID], subentry.data[CONF_STOP_ID]
                )
                == stop_name
                for subentry in self._get_entry().subentries.values()
            ):
                return self.async_abort(reason="already_configured")
            kind = line_kind(self._labels, static, self._route_id)
            name = static.line_name(self._route_id, self._direction_id)
            line = f"{kind} {name}"
            if (self._route_id, self._direction_id) in static.clockwise:
                # Circular line: its name tells the direction, "Tram 4a".
                title = f"{stop_name} ({line})"
            else:
                direction = static.direction_label(self._route_id, self._direction_id)
                title = f"{stop_name} → {direction} ({line})"
            return self.async_create_entry(
                title=title,
                data={
                    CONF_ROUTE_ID: self._route_id,
                    CONF_DIRECTION_ID: self._direction_id,
                    CONF_STOP_ID: stop_id,
                    **_leave_data(user_input),
                },
                unique_id=f"{stop_id}_{self._route_id}_{self._direction_id}",
            )

        offered = static.offered_stops(self._route_id, self._direction_id)
        fields: dict[vol.Marker, Any] = {
            vol.Required(CONF_STOP_ID): SelectSelector(
                SelectSelectorConfig(
                    options=[
                        SelectOptionDict(value=stop_id, label=name)
                        for name, stop_id in offered.items()
                    ],
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
            **_LEAVE_FIELDS,
        }
        return self.async_show_form(
            step_id="stop",
            data_schema=vol.Schema(fields),
            description_placeholders={
                "line": (
                    f"{line_kind(self._labels, static, self._route_id)} "
                    f"{static.line_name(self._route_id, self._direction_id)}"
                ),
                "direction": self._direction_name,
            },
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Change the walk to the stop and the "time to leave" window."""
        subentry = self._get_reconfigure_subentry()
        if user_input is not None:
            data = {
                key: value
                for key, value in subentry.data.items()
                if key not in (CONF_WALK_MINUTES, CONF_LEAVE_WINDOW)
            }
            return self.async_update_and_abort(
                self._get_entry(), subentry, data={**data, **_leave_data(user_input)}
            )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(_LEAVE_FIELDS),
                {
                    CONF_LEAVE_WINDOW: DEFAULT_LEAVE_WINDOW,
                    **{
                        key: subentry.data[key]
                        for key in (CONF_WALK_MINUTES, CONF_LEAVE_WINDOW)
                        if key in subentry.data
                    },
                },
            ),
            description_placeholders={"stop": subentry.title},
        )

    @property
    def _direction_name(self) -> str:
        """Destination of the direction, with its way round on a circular line."""
        static = self._static
        label = static.direction_label(self._route_id, self._direction_id)
        if way := rotation(self._labels, static, self._route_id, self._direction_id):
            return f"{label} ({way})"
        return label

    @property
    def _line_name(self) -> str:
        route = self._static.routes.get(self._route_id)
        kind = line_kind(self._labels, self._static, self._route_id)
        return f"{kind} {route.short_name if route else self._route_id}"
