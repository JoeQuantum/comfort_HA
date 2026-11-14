"""Diagnostics support for Kumo Cloud."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntry

from .const import DOMAIN
from .coordinator import KumoCloudConfigEntry, KumoCloudDataUpdateCoordinator

# Keys redacted from any nested dict before the diagnostics blob is exposed.
TO_REDACT = {
    "username",
    "password",
    "access",
    "access_token",
    "refresh",
    "refresh_token",
    "token",
    "deviceSerial",
    "serialNumber",
    "cryptoSerial",
    "mac",
    "macAddress",
    "ssid",
    "routerSsid",
    "ipAddress",
    "email",
    "unique_id",
    "title",
    "name",
    "id",
    "site_id",
    "siteId",
    "zone_id",
    "zoneId",
    "serial",
    "address",
    # HA DiscoveryKey objects can contain MACs/hostnames outside normal dict fields.
    "discovery_keys",
}


def _redact(data: Any, identifiers: dict[str, str]) -> Any:
    """Redact identities in values and dictionary keys without changing source data."""
    if isinstance(data, Mapping):
        return {
            identifiers.get(key, key): (
                "**REDACTED**" if key in TO_REDACT else _redact(value, identifiers)
            )
            for key, value in data.items()
        }
    if isinstance(data, (list, tuple)):
        return [_redact(value, identifiers) for value in data]
    if isinstance(data, str) and data in identifiers:
        return "**REDACTED**"
    return data


def _identifiers(coordinator: KumoCloudDataUpdateCoordinator) -> dict[str, str]:
    """Use consistent anonymous labels across the per-device diagnostic maps."""
    serials = set().union(
        coordinator.devices,
        coordinator.device_profiles,
        coordinator.device_statuses,
        coordinator.wireless_sensors,
    )
    identifiers = {serial: f"device_{i}" for i, serial in enumerate(sorted(serials), 1)}
    zones = set(coordinator.zone_notifications) | {
        zone["id"] for zone in coordinator.zones if "id" in zone
    }
    identifiers.update({zone: f"zone_{i}" for i, zone in enumerate(sorted(zones), 1)})
    return identifiers


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: KumoCloudConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    return _redact(
        {
            "config_entry": entry.as_dict(),
            "zones": coordinator.zones,
            "devices": coordinator.devices,
            "device_profiles": coordinator.device_profiles,
            "device_statuses": coordinator.device_statuses,
            "wireless_sensors": coordinator.wireless_sensors,
            "zone_notifications": coordinator.zone_notifications,
        },
        _identifiers(coordinator),
    )


async def async_get_device_diagnostics(
    hass: HomeAssistant,
    entry: KumoCloudConfigEntry,
    device: DeviceEntry,
) -> dict[str, Any]:
    """Return diagnostics for a single Mitsubishi indoor unit."""
    coordinator = entry.runtime_data

    serial = next(
        (identifier for domain, identifier in device.identifiers if domain == DOMAIN),
        None,
    )
    if serial is None:
        return {"error": "device serial not found in identifiers"}

    return _redact(
        {
            "device_info": {
                "name": device.name,
                "model": device.model,
                "sw_version": device.sw_version,
                "manufacturer": device.manufacturer,
            },
            "device_data": coordinator.devices.get(serial, {}),
            "device_status": coordinator.device_statuses.get(serial, {}),
            "device_profile": coordinator.device_profiles.get(serial, []),
            "wireless_sensor": coordinator.wireless_sensors.get(serial, {}),
        },
        _identifiers(coordinator),
    )
