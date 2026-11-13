"""Availability regression tests using real HA entities and mocked cloud I/O.

Run with: python -m unittest discover -s tests -v
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers import frame

from custom_components.kumo_cloud.api import KumoCloudConnectionError
from custom_components.kumo_cloud.climate import KumoCloudClimate
from custom_components.kumo_cloud.const import DEVICE_TELEMETRY_MAX_AGE
from custom_components.kumo_cloud.coordinator import (
    KumoCloudDataUpdateCoordinator,
    KumoCloudDevice,
)

NOW = datetime(2026, 11, 13, 18, 0, tzinfo=timezone.utc)


def detail(serial: str = "unit-a", **changes) -> dict:
    data = {
        "deviceSerial": serial,
        "connected": False,
        "updatedAt": NOW.isoformat(),
        "roomTemp": 21,
        "spHeat": 20,
        "spCool": 24,
        "operationMode": "heat",
        "power": 1,
    }
    data.update(changes)
    return data


def zone(serial: str = "unit-a") -> dict:
    return {"id": f"zone-{serial}", "name": serial, "adapter": detail(serial)}


def climate(coordinator, serial: str = "unit-a") -> KumoCloudClimate:
    return KumoCloudClimate(KumoCloudDevice(coordinator, f"zone-{serial}", serial))


class AvailabilityTests(TestCase):
    def setUp(self) -> None:
        self.clock = patch(
            "custom_components.kumo_cloud.coordinator.dt_util.utcnow", return_value=NOW
        ).start()
        self.addCleanup(patch.stopall)
        self.coordinator = SimpleNamespace(
            zones=[zone()],
            devices={"unit-a": detail()},
            device_profiles={},
            device_update_success={"unit-a": True},
            data={"loaded": True},
            last_update_success=True,
        )
        self.entity = climate(self.coordinator)

    def test_false_connection_flag_with_fresh_telemetry_is_available(self):
        self.assertIs(self.entity.available, True)

    def test_missing_connection_flag_with_fresh_telemetry_is_available(self):
        del self.coordinator.devices["unit-a"]["connected"]
        del self.coordinator.zones[0]["adapter"]["connected"]
        self.assertIs(self.entity.available, True)

    def test_repeated_old_snapshot_expires(self):
        self.clock.return_value = NOW + timedelta(seconds=DEVICE_TELEMETRY_MAX_AGE)
        self.assertIs(self.entity.available, True)
        self.clock.return_value += timedelta(seconds=1)
        self.assertIs(self.entity.available, False)

    def test_freshness_boundaries_and_future_timestamp(self):
        for age, expected in [
            (0, True),
            (299, True),
            (300, True),
            (301, False),
            (-1, False),
        ]:
            with self.subTest(age=age):
                self.coordinator.devices["unit-a"]["updatedAt"] = (
                    NOW - timedelta(seconds=age)
                ).isoformat()
                self.assertIs(self.entity.available, expected)

    def test_invalid_or_missing_timestamp_does_not_override_offline(self):
        for value in [
            None,
            123,
            {},
            "",
            "invalid",
            "2026-13-13T18:00:00Z",
            "2026-11-13T18:00:00",
        ]:
            with self.subTest(value=value):
                self.coordinator.devices["unit-a"]["updatedAt"] = value
                self.assertIs(self.entity.available, False)
        del self.coordinator.devices["unit-a"]["updatedAt"]
        self.assertIs(self.entity.available, False)

    def test_timezone_offsets_and_z_timestamps_are_supported(self):
        for value in ["2026-11-13T18:00:00Z", "2026-11-13T10:00:00-08:00"]:
            with self.subTest(value=value):
                self.coordinator.devices["unit-a"]["updatedAt"] = value
                self.assertIs(self.entity.available, True)

    def test_positive_connection_flag_retains_existing_behavior(self):
        self.coordinator.devices["unit-a"]["connected"] = True
        del self.coordinator.devices["unit-a"]["updatedAt"]
        self.assertIs(self.entity.available, True)

    def test_device_offline_flag_takes_precedence_over_adapter_online(self):
        self.coordinator.zones[0]["adapter"]["connected"] = True
        self.coordinator.devices["unit-a"]["updatedAt"] = "2020-01-01T00:00:00Z"
        self.assertIs(self.entity.available, False)

    def test_adapter_connection_is_used_when_device_flag_is_absent(self):
        self.coordinator.zones[0]["adapter"]["connected"] = True
        del self.coordinator.devices["unit-a"]["connected"]
        del self.coordinator.devices["unit-a"]["updatedAt"]
        self.assertIs(self.entity.available, True)

    def test_site_poll_failure_overrides_fresh_or_connected_data(self):
        self.coordinator.last_update_success = False
        for connected in [False, True]:
            with self.subTest(connected=connected):
                self.coordinator.devices["unit-a"]["connected"] = connected
                self.assertIs(self.entity.available, False)

    def test_device_refresh_failure_overrides_fresh_or_connected_data(self):
        self.coordinator.device_update_success["unit-a"] = False
        for connected in [False, True]:
            with self.subTest(connected=connected):
                self.coordinator.devices["unit-a"]["connected"] = connected
                self.assertIs(self.entity.available, False)

    def test_missing_refresh_status_is_unavailable(self):
        self.coordinator.device_update_success.clear()
        self.assertIs(self.entity.available, False)

    def test_missing_device_data_is_unavailable(self):
        self.coordinator.devices.clear()
        self.assertIs(self.entity.available, False)

    def test_missing_zone_is_unavailable(self):
        self.coordinator.zones.clear()
        self.assertIs(self.entity.available, False)

    def test_missing_coordinator_data_is_unavailable(self):
        self.coordinator.data = None
        self.assertIs(self.entity.available, False)

    def test_zone_timestamp_is_not_device_telemetry(self):
        del self.coordinator.devices["unit-a"]["updatedAt"]
        self.assertIs(self.entity.available, False)


class CoordinatorAvailabilityTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.hass = HomeAssistant(self.directory.name)
        frame.async_setup(self.hass)
        self.clock = patch(
            "custom_components.kumo_cloud.coordinator.dt_util.utcnow", return_value=NOW
        ).start()
        self.addCleanup(patch.stopall)
        self.api = SimpleNamespace(
            get_zones=AsyncMock(side_effect=lambda _: [zone("unit-a"), zone("unit-b")]),
            get_device_details=AsyncMock(side_effect=lambda serial: detail(serial)),
            get_device_profile=AsyncMock(return_value=[]),
            get_device_status=AsyncMock(return_value={}),
            get_zone_notification_preferences=AsyncMock(return_value={}),
        )
        self.coordinator = KumoCloudDataUpdateCoordinator(self.hass, self.api, "site")
        self.entity_a = climate(self.coordinator, "unit-a")
        self.entity_b = climate(self.coordinator, "unit-b")

    async def asyncTearDown(self) -> None:
        await self.coordinator.async_shutdown()
        await self.hass.async_stop()

    async def test_initial_state_is_unavailable_then_recovers_on_poll(self):
        self.assertIs(self.entity_a.available, False)
        await self.coordinator.async_refresh()
        self.assertIs(self.entity_a.available, True)
        self.assertIs(self.entity_b.available, True)

    async def test_failed_site_poll_keeps_cache_but_marks_offline_and_recovers(self):
        await self.coordinator.async_refresh()
        retained = deepcopy(self.coordinator.devices)
        self.api.get_zones.side_effect = KumoCloudConnectionError("offline")
        await self.coordinator.async_refresh()
        self.assertEqual(self.coordinator.devices, retained)
        self.assertIs(self.entity_a.available, False)
        self.assertIs(self.entity_b.available, False)
        self.api.get_zones.side_effect = lambda _: [zone("unit-a"), zone("unit-b")]
        await self.coordinator.async_refresh()
        self.assertIs(self.entity_a.available, True)

    async def test_partial_device_failure_does_not_hide_behind_cached_commands(self):
        await self.coordinator.async_refresh()
        self.coordinator.cached_commands[("unit-a", "spHeat")] = (NOW.isoformat(), 22)

        def respond(serial):
            if serial == "unit-a":
                raise KumoCloudConnectionError("device detail failed")
            return detail(serial)

        self.api.get_device_details.side_effect = respond
        await self.coordinator.async_refresh()
        self.assertTrue(self.coordinator.last_update_success)
        self.assertEqual(self.coordinator.devices["unit-a"], {"spHeat": 22})
        self.assertIs(self.entity_a.available, False)
        self.assertIs(self.entity_b.available, True)
        self.api.get_device_details.side_effect = lambda serial: detail(serial)
        await self.coordinator.async_refresh()
        self.assertIs(self.entity_a.available, True)

    async def test_empty_device_response_is_not_restored_by_cached_commands(self):
        self.coordinator.cached_commands[("unit-a", "spHeat")] = (NOW.isoformat(), 22)
        self.api.get_device_details.side_effect = (
            lambda serial: {} if serial == "unit-a" else detail(serial)
        )
        await self.coordinator.async_refresh()
        self.assertEqual(self.coordinator.devices["unit-a"], {"spHeat": 22})
        self.assertIs(self.entity_a.available, False)
        self.assertIs(self.entity_b.available, True)

    async def test_optional_endpoint_failure_does_not_disable_climate(self):
        self.api.get_device_status.side_effect = KumoCloudConnectionError(
            "status failed"
        )
        self.api.get_device_profile.side_effect = KumoCloudConnectionError(
            "profile failed"
        )
        await self.coordinator.async_refresh()
        self.assertIs(self.entity_a.available, True)

    async def test_successful_repeated_fetch_does_not_refresh_stale_telemetry(self):
        await self.coordinator.async_refresh()
        self.assertIs(self.entity_a.available, True)
        self.clock.return_value += timedelta(seconds=DEVICE_TELEMETRY_MAX_AGE + 1)
        await self.coordinator.async_refresh()
        self.assertTrue(self.coordinator.last_update_success)
        self.assertTrue(self.coordinator.device_update_success["unit-a"])
        self.assertIs(self.entity_a.available, False)
        self.api.get_device_details.side_effect = lambda serial: detail(
            serial, updatedAt=self.clock.return_value.isoformat()
        )
        await self.coordinator.async_refresh()
        self.assertIs(self.entity_a.available, True)

    async def test_direct_refresh_failure_notifies_and_recovery_restores_availability(
        self,
    ):
        await self.coordinator.async_refresh()
        retained = deepcopy(self.coordinator.devices["unit-a"])
        self.api.get_device_details.side_effect = KumoCloudConnectionError("offline")
        with patch.object(self.coordinator, "async_update_listeners") as notify:
            await self.coordinator.async_refresh_device("unit-a")
            notify.assert_called_once()
        self.assertEqual(self.coordinator.devices["unit-a"], retained)
        self.assertIs(self.entity_a.available, False)
        self.assertIs(self.entity_b.available, True)
        self.api.get_device_details.side_effect = lambda serial: detail(serial)
        await self.coordinator.async_refresh_device("unit-a")
        self.assertIs(self.entity_a.available, True)

    async def test_empty_direct_refresh_is_not_restored_by_cached_commands(self):
        await self.coordinator.async_refresh()
        self.coordinator.cached_commands[("unit-a", "spHeat")] = (NOW.isoformat(), 22)
        self.api.get_device_details.side_effect = None
        self.api.get_device_details.return_value = {}
        await self.coordinator.async_refresh_device("unit-a")
        self.assertIs(self.entity_a.available, False)

    async def test_removed_zone_does_not_keep_old_success_status(self):
        await self.coordinator.async_refresh()
        self.api.get_zones.side_effect = lambda _: [zone("unit-b")]
        await self.coordinator.async_refresh()
        self.assertNotIn("unit-a", self.coordinator.device_update_success)
        self.assertIs(self.entity_a.available, False)
        self.assertIs(self.entity_b.available, True)

    async def test_invalid_timestamps_do_not_break_pending_command_processing(self):
        await self.coordinator.async_refresh()
        self.coordinator.cached_commands[("unit-a", "spHeat")] = (NOW.isoformat(), 22)
        for updated_at in [
            None,
            "bad-date",
            "2026-13-13T18:00:00Z",
            "2026-11-13T18:00:00",
        ]:
            with self.subTest(updated_at=updated_at):
                self.api.get_device_details.side_effect = lambda serial: detail(
                    serial, updatedAt=updated_at
                )
                await self.coordinator.async_refresh()
                self.assertTrue(self.coordinator.last_update_success)
                self.assertIs(self.entity_a.available, False)
                self.assertIn(("unit-a", "spHeat"), self.coordinator.cached_commands)

    async def test_fresh_confirmed_telemetry_still_culls_cached_commands(self):
        await self.coordinator.async_refresh()
        self.coordinator.cached_commands[("unit-a", "spHeat")] = (NOW.isoformat(), 22)
        await self.coordinator.async_refresh()
        self.assertNotIn(("unit-a", "spHeat"), self.coordinator.cached_commands)
        self.assertIs(self.entity_a.available, True)
