"""Constants for the TaM Montpellier integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "tam_montpellier"

CONF_GTFS_URL: Final = "gtfs_url"
CONF_TRIP_UPDATES_URL: Final = "trip_updates_url"
CONF_ALERTS_URL: Final = "alerts_url"
CONF_VEHICLE_POSITIONS_URL: Final = "vehicle_positions_url"

CONF_ROUTE_ID: Final = "route_id"
CONF_DIRECTION_ID: Final = "direction_id"
CONF_STOP_ID: Final = "stop_id"
# Option: ids of the bus lines to offer next to the tram lines.
CONF_EXTRA_ROUTES: Final = "extra_routes"

SUBENTRY_TYPE_STOP: Final = "stop"

DEFAULT_GTFS_URL: Final = "https://gtfsproxy.e-tam.fr/COMMON/GTFS.zip"
DEFAULT_TRIP_UPDATES_URL: Final = "https://gtfsproxy.e-tam.fr/COMMON/TripUpdate.pb"
DEFAULT_ALERTS_URL: Final = "https://gtfsproxy.e-tam.fr/COMMON/Alert.pb"
DEFAULT_VEHICLE_POSITIONS_URL: Final = (
    "https://gtfsproxy.e-tam.fr/COMMON/VehiclePosition.pb"
)

# GTFS route_type 0 = tram / light rail.
TRAM_ROUTE_TYPE: Final = "0"
# GTFS route_type 3 = bus.
BUS_ROUTE_TYPE: Final = "3"

UPDATE_INTERVAL: Final = timedelta(seconds=30)
REQUEST_TIMEOUT: Final = 20
GTFS_DOWNLOAD_TIMEOUT: Final = 180
# The GTFS archive is regenerated daily by the operator.
GTFS_MAX_AGE: Final = timedelta(hours=20)
GTFS_REFRESH_HOUR: Final = 3
GTFS_REFRESH_MINUTE: Final = 30

# Number of upcoming departures exposed in the sensor attributes.
MAX_DEPARTURES: Final = 6

# A tram stays counted in service this long after it was last seen in the
# real-time feeds: it may vanish from them while waiting at a terminus.
FLEET_MEMORY: Final = timedelta(minutes=20)

ATTRIBUTION: Final = "Données TaM / Montpellier Méditerranée Métropole (ODbL)"
