"""Data update coordinator and per-device wrapper for Kumo Cloud."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from time import monotonic
from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import KumoCloudAPI, KumoCloudAuthError, KumoCloudConnectionError
from .const import (
    COMMAND_CACHE_TTL,
    DEFAULT_SCAN_INTERVAL,
    DEVICE_TELEMETRY_MAX_AGE,
    DOMAIN,
)

type KumoCloudConfigEntry = ConfigEntry["KumoCloudDataUpdateCoordinator"]

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class PendingCommand:
    """An accepted setting awaiting device confirmation, never raw telemetry."""

    value: Any
    expires_at: float


def _parse_updated_at(value: Any) -> datetime | None:
    """Read a timezone-aware device timestamp without trusting malformed data."""
    if not isinstance(value, str):
        return None
    try:
        updated = dt_util.parse_datetime(value)
    except (ValueError, OverflowError):
        return None
    if updated is None or updated.tzinfo is None:
        return None
    return updated


class KumoCloudDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching Kumo Cloud data."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: KumoCloudAPI,
        site_id: str,
        *,
        config_entry: KumoCloudConfigEntry | None = None,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
            config_entry=config_entry,
        )
        self.api = api
        self.site_id = site_id
        self.zones: list[dict[str, Any]] = []
        self.devices: dict[str, dict[str, Any]] = {}
        # Record actual detail-fetch success before pending commands can make
        # an empty/failed response look like populated device data.
        self.device_update_success: dict[str, bool] = {}
        self.device_profiles: dict[str, list[dict[str, Any]]] = {}
        self.wireless_sensors: dict[str, dict[str, Any]] = {}
        self.device_statuses: dict[str, dict[str, Any]] = {}
        self.zone_notifications: dict[str, dict[str, Any]] = {}

        self.cached_commands: dict[tuple[str, str], PendingCommand] = {}
        self.command_locks: dict[str, asyncio.Lock] = {}
        self.device_revisions: dict[str, int] = {}
        self._pending_expiry: asyncio.TimerHandle | None = None

    def next_device_revision(self, device_serial: str) -> int:
        """Order reads and accepted commands before they can update device state."""
        revision = self.device_revisions.get(device_serial, 0) + 1
        self.device_revisions[device_serial] = revision
        return revision

    def _process_pending_commands(
        self, device_serial: str, device_detail: dict[str, Any]
    ) -> None:
        """Acknowledge matching values without altering the cloud response."""
        self._expire_pending_commands()
        for key, pending in list(self.cached_commands.items()):
            serial, command = key
            if serial != device_serial:
                continue
            actual = device_detail.get(command)
            if command == "operationMode":
                if actual in ("autoHeat", "autoCool"):
                    actual = "auto"
                # Some adapters retain their mode while powered off.
                if device_detail.get("power") == 0:
                    actual = "off"
            if actual == pending.value:
                del self.cached_commands[key]
        self._schedule_pending_expiry()

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from Kumo Cloud."""
        try:
            # Get zones for the site
            zones = await self.api.get_zones(self.site_id)

            # Get device details for each zone
            devices = {}
            revisions = {}
            device_update_success = {}
            device_profiles = {}
            wireless_sensors = {}
            device_statuses = {}
            zone_notifications = {}

            for zone in zones:
                if zone.get("adapter"):
                    device_serial = zone["adapter"]["deviceSerial"]
                    revisions[device_serial] = self.next_device_revision(device_serial)
                    zone_id = zone["id"]
                    has_sensor = zone["adapter"].get("hasSensor", False)

                    # Build task list - fetch everything in parallel
                    task_keys = ["detail", "profile", "status", "notifications"]
                    tasks = [
                        self.api.get_device_details(device_serial),
                        self.api.get_device_profile(device_serial),
                        self.api.get_device_status(device_serial),
                        self.api.get_zone_notification_preferences(zone_id),
                    ]
                    # Also fetch wireless sensor data if the zone has one
                    if has_sensor:
                        task_keys.append("sensor")
                        tasks.append(self.api.get_wireless_sensor(device_serial))

                    results = await asyncio.gather(*tasks, return_exceptions=True)

                    # Process results by key
                    result_map = {}
                    for key, result in zip(task_keys, results):
                        if isinstance(result, KumoCloudAuthError):
                            raise result
                        if isinstance(result, asyncio.CancelledError):
                            raise result
                        if isinstance(result, Exception):
                            _LOGGER.debug(
                                "Failed to fetch %s for %s: %s",
                                key,
                                device_serial,
                                result,
                            )
                            result_map[key] = None
                        else:
                            result_map[key] = result

                    device_detail = result_map.get("detail") or {}
                    device_update_success[device_serial] = bool(device_detail)

                    devices[device_serial] = device_detail
                    # Capabilities are stable metadata. A failed/empty optional
                    # response must not disable controls on an otherwise online unit.
                    device_profiles[device_serial] = result_map.get(
                        "profile"
                    ) or self.device_profiles.get(device_serial, [])

                    if result_map.get("status"):
                        device_statuses[device_serial] = result_map["status"]

                    if result_map.get("notifications"):
                        zone_notifications[zone_id] = result_map["notifications"]

                    if has_sensor and result_map.get("sensor"):
                        wireless_sensors[device_serial] = result_map["sensor"]

            # A poll can overlap another read or an accepted command while fetching
            # other endpoints/units. Only the latest request may publish device state.
            for serial, detail in devices.items():
                if revisions[serial] != self.device_revisions.get(serial, 0):
                    devices[serial] = self.devices.get(serial, {})
                    device_update_success[serial] = self.device_update_success.get(
                        serial, False
                    )
                else:
                    self._process_pending_commands(serial, detail)

            # Store the data for access by entities
            self.zones = zones
            self.devices = devices
            self.device_update_success = device_update_success
            self.device_profiles = device_profiles
            self.wireless_sensors = wireless_sensors
            self.device_statuses = device_statuses
            self.zone_notifications = zone_notifications

            return {
                "zones": zones,
                "devices": devices,
                "device_profiles": device_profiles,
                "wireless_sensors": wireless_sensors,
                "device_statuses": device_statuses,
                "zone_notifications": zone_notifications,
            }

        except KumoCloudAuthError as err:
            # The API has already attempted bounded recovery. Let HA reauthorize.
            raise ConfigEntryAuthFailed("Cloud authentication failed") from err
        except KumoCloudConnectionError as err:
            raise UpdateFailed(f"Error communicating with API: {err}") from err
        except (TimeoutError, aiohttp.ClientError, OSError) as err:
            raise UpdateFailed(f"Connection error: {err}") from err

    async def async_refresh_device(self, device_serial: str) -> None:
        """Refresh a specific device's data immediately."""
        revision = self.next_device_revision(device_serial)
        try:
            # Get fresh device details
            device_detail = await self.api.get_device_details(device_serial)
            if revision != self.device_revisions.get(device_serial):
                return
            self.device_update_success[device_serial] = bool(device_detail)

            # Process pending commands for the device
            self._process_pending_commands(device_serial, device_detail)

            # Update the cached device data
            self.devices[device_serial] = device_detail

            # Also update the zone data if it contains the same info
            for zone in self.zones:
                if (
                    zone.get("adapter")
                    and zone["adapter"]["deviceSerial"] == device_serial
                ):
                    # Update adapter data with fresh device data
                    zone["adapter"].update(
                        {
                            "roomTemp": device_detail.get("roomTemp"),
                            "operationMode": device_detail.get("operationMode"),
                            "power": device_detail.get("power"),
                            "fanSpeed": device_detail.get("fanSpeed"),
                            "airDirection": device_detail.get("airDirection"),
                            "spCool": device_detail.get("spCool"),
                            "spHeat": device_detail.get("spHeat"),
                            "humidity": device_detail.get("humidity"),
                        }
                    )
                    break

            # Update the coordinator's data dict
            self.data = {
                "zones": self.zones,
                "devices": self.devices,
                "device_profiles": self.device_profiles,
                "wireless_sensors": self.wireless_sensors,
                "device_statuses": self.device_statuses,
                "zone_notifications": self.zone_notifications,
            }

            # Notify all listeners that data has been updated
            self.async_update_listeners()

            _LOGGER.debug("Refreshed device %s data", device_serial)

        except KumoCloudAuthError as err:
            # Authentication is account-wide, even when this read was superseded.
            self.device_update_success[device_serial] = False
            self.async_auth_failed()
            raise ConfigEntryAuthFailed("Cloud authentication failed") from err
        except (
            TimeoutError,
            KumoCloudConnectionError,
            aiohttp.ClientError,
            OSError,
        ) as err:
            if revision != self.device_revisions.get(device_serial):
                return
            self.device_update_success[device_serial] = False
            self.async_update_listeners()
            _LOGGER.warning("Failed to refresh device %s: %s", device_serial, err)

    def async_auth_failed(self) -> None:
        """Start reauthentication for failures outside scheduled coordinator polls."""
        self.last_update_success = False
        if self.config_entry:
            self.config_entry.async_start_reauth(self.hass)
        self.async_update_listeners()

    def cache_command(self, device_serial: str, command: str, value: Any) -> None:
        """Track an accepted setting for a bounded confirmation window."""
        self.cached_commands[(device_serial, command)] = PendingCommand(
            value, monotonic() + COMMAND_CACHE_TTL
        )
        self._schedule_pending_expiry()

    def pending_commands(self, device_serial: str) -> dict[str, Any]:
        """Return unexpired requested settings separately from measured state."""
        self._expire_pending_commands()
        return {
            command: pending.value
            for (serial, command), pending in self.cached_commands.items()
            if serial == device_serial
        }

    def _expire_pending_commands(self) -> None:
        now = monotonic()
        for key, pending in list(self.cached_commands.items()):
            if now >= pending.expires_at:
                del self.cached_commands[key]

    def _schedule_pending_expiry(self) -> None:
        if self._pending_expiry:
            self._pending_expiry.cancel()
            self._pending_expiry = None
        if self.cached_commands:
            delay = max(
                0,
                min(p.expires_at for p in self.cached_commands.values()) - monotonic(),
            )
            self._pending_expiry = self.hass.loop.call_later(
                delay, self._pending_expired
            )

    def _pending_expired(self) -> None:
        self._pending_expiry = None
        self._expire_pending_commands()
        self._schedule_pending_expiry()
        self.async_update_listeners()

    async def async_shutdown(self) -> None:
        """Cancel optimistic-state timers when the entry unloads."""
        if self._pending_expiry:
            self._pending_expiry.cancel()
            self._pending_expiry = None
        await super().async_shutdown()


class KumoCloudDevice:
    """Representation of a Kumo Cloud device."""

    def __init__(
        self,
        coordinator: KumoCloudDataUpdateCoordinator,
        zone_id: str,
        device_serial: str,
    ) -> None:
        """Initialize the device."""
        self.coordinator = coordinator
        self.zone_id = zone_id
        self.device_serial = device_serial
        self._zone_data: dict[str, Any] | None = None
        self._device_data: dict[str, Any] | None = None
        self._profile_data: list[dict[str, Any]] | None = None

    @property
    def zone_data(self) -> dict[str, Any]:
        """Get the zone data."""
        # Always get fresh data from coordinator
        for zone in self.coordinator.zones:
            if zone["id"] == self.zone_id:
                return zone
        return {}

    @property
    def device_data(self) -> dict[str, Any]:
        """Get the device data."""
        # Always get fresh data from coordinator
        return self.coordinator.devices.get(self.device_serial, {})

    @property
    def control_data(self) -> dict[str, Any]:
        """Combine latest readings and pending controls without changing telemetry."""
        pending = self.coordinator.pending_commands(self.device_serial)
        data = {**self.zone_data.get("adapter", {}), **self.device_data, **pending}
        if "operationMode" in pending:
            data["power"] = 0 if pending["operationMode"] == "off" else 1
        return data

    @property
    def profile_data(self) -> list[dict[str, Any]]:
        """Get the device profile data."""
        # Always get fresh data from coordinator
        return self.coordinator.device_profiles.get(self.device_serial, [])

    @property
    def has_wireless_sensor(self) -> bool:
        """Return True if this device has a wireless sensor attached."""
        zone = self.zone_data
        adapter = zone.get("adapter", {})
        return adapter.get("hasSensor", False)

    @property
    def wireless_sensor_data(self) -> dict[str, Any] | None:
        """Get the wireless sensor data (battery, temp, humidity, rssi)."""
        return self.coordinator.wireless_sensors.get(self.device_serial)

    @property
    def device_status_data(self) -> dict[str, Any] | None:
        """Get device status data (firmware, WiFi signal, router info)."""
        return self.coordinator.device_statuses.get(self.device_serial)

    @property
    def zone_notification_data(self) -> dict[str, Any] | None:
        """Get zone notification preferences (filter reminders, alert settings)."""
        return self.coordinator.zone_notifications.get(self.zone_id)

    @property
    def available(self) -> bool:
        """Require a successful refresh and evidence the device is online."""
        zone_data = self.zone_data
        device_data = self.device_data
        if (
            not self.coordinator.last_update_success
            or not self.coordinator.device_update_success.get(self.device_serial, False)
            or not zone_data
            or not device_data
        ):
            return False

        adapter = zone_data.get("adapter", {})
        adapter_connected = adapter.get("connected", False)
        device_connected = device_data.get("connected", adapter_connected)
        if device_connected is True:
            return True

        # The cloud can report connected=False for a working device. Only
        # override it when the device response itself has recent telemetry;
        # cached commands, zone metadata, and HTTP fetch time are not proof.
        updated = _parse_updated_at(device_data.get("updatedAt"))
        if updated is None:
            return False
        age = (dt_util.utcnow() - updated).total_seconds()
        return 0 <= age <= DEVICE_TELEMETRY_MAX_AGE

    @property
    def name(self) -> str:
        """Return the name of the device."""
        return self.zone_data.get("name", f"Zone {self.zone_id}")

    @property
    def unique_id(self) -> str:
        """Return a unique ID for the device."""
        return f"{self.device_serial}_{self.zone_id}"

    async def send_command(self, commands: dict[str, Any]) -> None:
        """Serialize writes and publish optimistic settings only after acceptance."""
        lock = self.coordinator.command_locks.setdefault(
            self.device_serial, asyncio.Lock()
        )
        async with lock:
            try:
                await self.coordinator.api.send_command(self.device_serial, commands)
            except KumoCloudAuthError as err:
                self.coordinator.async_auth_failed()
                raise ConfigEntryAuthFailed("Cloud authentication failed") from err

            self.coordinator.next_device_revision(self.device_serial)
            for command, value in commands.items():
                self.coordinator.cache_command(self.device_serial, command, value)
            self.coordinator.async_update_listeners()

            # Failed readback does not make an accepted command a rejected one.
            # Pending settings remain bounded by their original expiration time.
            await asyncio.sleep(1)
            await self.coordinator.async_refresh_device(self.device_serial)
