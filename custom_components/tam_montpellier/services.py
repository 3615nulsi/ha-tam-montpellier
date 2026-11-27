"""Actions of the TaM Montpellier integration."""

from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_DEVICE_ID
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.util import dt as dt_util

from .const import CONF_DIRECTION_ID, CONF_ROUTE_ID, CONF_STOP_ID, DOMAIN
from .coordinator import TamConfigEntry
from .gtfs_static import StopKey
from .sensor import departure_attributes

SERVICE_GET_DEPARTURES = "get_departures"
ATTR_COUNT = "count"
MAX_COUNT = 20

GET_DEPARTURES_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_DEVICE_ID): cv.string,
        vol.Inclusive(CONF_STOP_ID, "stop"): cv.string,
        vol.Inclusive(CONF_ROUTE_ID, "stop"): cv.string,
        vol.Inclusive(CONF_DIRECTION_ID, "stop"): vol.All(
            vol.Coerce(int), vol.In([0, 1])
        ),
        vol.Optional(ATTR_COUNT, default=6): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=MAX_COUNT)
        ),
    }
)


def async_setup_services(hass: HomeAssistant) -> None:
    """Register the actions of the integration."""
    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_DEPARTURES,
        _async_get_departures,
        schema=GET_DEPARTURES_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )


async def _async_get_departures(call: ServiceCall) -> ServiceResponse:
    """Return the next departures of a followed stop.

    The departures are listed as in the ``departures`` attribute of the
    minutes sensor, up to ``count`` of them.
    """
    hass = call.hass
    entries: list[TamConfigEntry] = [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.state is ConfigEntryState.LOADED
    ]
    if not entries:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="not_loaded"
        )
    coordinator = entries[0].runtime_data
    key = _stop_key(hass, call, coordinator.stop_keys)
    now = dt_util.now()
    departures = await hass.async_add_executor_job(
        coordinator.departures, key, now, call.data[ATTR_COUNT]
    )
    return {"departures": [departure_attributes(dep, now) for dep in departures]}


def _stop_key(
    hass: HomeAssistant, call: ServiceCall, followed: set[StopKey]
) -> StopKey:
    """Return the followed stop asked for, by its device or its ids."""
    if device_id := call.data.get(ATTR_DEVICE_ID):
        device = dr.async_get(hass).async_get(device_id)
        identifiers = (
            {key for domain, key in device.identifiers if domain == DOMAIN}
            if device
            else set()
        )
        for key in followed:
            if "_".join(map(str, key)) in identifiers:
                return key
    elif CONF_STOP_ID in call.data:
        key = (
            call.data[CONF_STOP_ID],
            call.data[CONF_ROUTE_ID],
            call.data[CONF_DIRECTION_ID],
        )
        if key in followed:
            return key
    else:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="stop_required"
        )
    raise ServiceValidationError(
        translation_domain=DOMAIN, translation_key="stop_not_followed"
    )
