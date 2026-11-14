"""Accepted commands are bounded overlays, not replacement telemetry."""

import asyncio
from unittest.mock import AsyncMock, Mock, patch

from helpers import IntegrationTestCase, detail
from homeassistant.components.climate import HVACMode
from homeassistant.exceptions import ConfigEntryAuthFailed

from custom_components.kumo_cloud.api import (
    KumoCloudAuthError,
    KumoCloudConnectionError,
)
from custom_components.kumo_cloud.const import COMMAND_CACHE_TTL
from custom_components.kumo_cloud.coordinator import KumoCloudDevice


class CommandCacheTests(IntegrationTestCase):
    async def send(self, commands):
        with patch(
            "custom_components.kumo_cloud.coordinator.asyncio.sleep", new=AsyncMock()
        ):
            await self.device.send_command(commands)

    async def test_failed_command_never_becomes_reported_state(self):
        self.api.send_command.side_effect = KumoCloudConnectionError("rejected")
        with self.assertRaises(KumoCloudConnectionError):
            await self.send({"fanSpeed": "quiet"})
        self.assertEqual(self.coordinator.cached_commands, {})
        self.assertEqual(self.entity.fan_mode, "auto")
        await self.coordinator.async_refresh()
        self.assertEqual(self.entity.fan_mode, "auto")

    async def test_failed_replacement_preserves_previous_accepted_setting(self):
        self.coordinator.cache_command("unit-a", "spCool", 23)
        original = self.coordinator.cached_commands[("unit-a", "spCool")]
        self.api.send_command.side_effect = KumoCloudConnectionError("rejected")
        with self.assertRaises(KumoCloudConnectionError):
            await self.send({"spCool": 24})
        self.assertIs(self.coordinator.cached_commands[("unit-a", "spCool")], original)

    async def test_no_optimistic_update_before_cloud_accepts(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def command(*_):
            entered.set()
            await release.wait()
            raise KumoCloudConnectionError("rejected")

        self.api.send_command.side_effect = command
        task = asyncio.create_task(self.device.send_command({"spCool": 23}))
        await entered.wait()
        self.assertEqual(self.coordinator.cached_commands, {})
        self.assertEqual(self.entity.native_target_temperature, 77)
        release.set()
        with self.assertRaises(KumoCloudConnectionError):
            await task

    async def test_accepted_setting_is_visible_but_raw_telemetry_is_unchanged(self):
        await self.send({"spCool": 22.5, "fanSpeed": "quiet"})
        self.assertEqual(self.entity.native_target_temperature, 72)
        self.assertEqual(self.entity.fan_mode, "low")
        self.assertEqual(self.coordinator.devices["unit-a"]["spCool"], 25)
        self.assertEqual(self.coordinator.devices["unit-a"]["fanSpeed"], "auto")

    async def test_pending_mode_overrides_old_off_power_only_in_control_state(self):
        self.coordinator.devices["unit-a"].update(power=0, operationMode="off")
        self.coordinator.cache_command("unit-a", "operationMode", "heat")
        self.assertEqual(self.entity.hvac_mode, HVACMode.HEAT)
        self.assertEqual(self.device.device_data["power"], 0)
        self.assertEqual(self.device.device_data["operationMode"], "off")

    async def test_new_timestamp_without_matching_value_does_not_acknowledge(self):
        await self.send({"spCool": 22.5})
        await self.coordinator.async_refresh()
        self.assertEqual(self.coordinator.pending_commands("unit-a"), {"spCool": 22.5})
        self.api.get_device_details.side_effect = lambda _: detail(spCool=22.5)
        await self.coordinator.async_refresh()
        self.assertEqual(self.coordinator.cached_commands, {})
        self.assertEqual(self.entity.native_target_temperature, 72)

    async def test_pending_state_expires_without_valid_timestamp_and_notifies(self):
        with patch(
            "custom_components.kumo_cloud.coordinator.monotonic", return_value=100
        ) as clock:
            self.coordinator.cache_command("unit-a", "spCool", 22.5)
            for timestamp in (None, "bad-date", "2026-11-14T00:00:00"):
                self.api.get_device_details.side_effect = (
                    lambda _, timestamp=timestamp: detail(updatedAt=timestamp)
                )
                await self.coordinator.async_refresh()
                self.assertEqual(self.entity.native_target_temperature, 72)
            clock.return_value = 100 + COMMAND_CACHE_TTL
            with patch.object(self.coordinator, "async_update_listeners") as notify:
                self.coordinator._pending_expired()
            notify.assert_called_once()
            self.assertEqual(self.coordinator.cached_commands, {})
            self.assertEqual(self.entity.native_target_temperature, 77)

    async def test_matching_auto_stage_confirms_auto_and_off_power_confirms_off(self):
        for mode, raw in (
            ("auto", detail(operationMode="autoCool")),
            ("off", detail(power=0, operationMode="heat")),
        ):
            with self.subTest(mode=mode):
                self.coordinator.cache_command("unit-a", "operationMode", mode)
                self.coordinator._process_pending_commands("unit-a", raw)
                self.assertEqual(self.coordinator.cached_commands, {})

    async def test_readback_connection_failure_retains_only_bounded_pending_state(self):
        self.api.get_device_details.side_effect = KumoCloudConnectionError("offline")
        await self.send({"spCool": 22.5})
        self.assertFalse(self.entity.available)
        self.assertEqual(self.coordinator.pending_commands("unit-a"), {"spCool": 22.5})
        self.assertEqual(self.device.device_data["spCool"], 25)

    async def test_device_wrappers_share_command_serialization(self):
        second_device = KumoCloudDevice(self.coordinator, "zone-a", "unit-a")
        entered, release = asyncio.Event(), asyncio.Event()

        async def submit(_, command):
            if command["spCool"] == 22.5:
                entered.set()
                await release.wait()

        self.api.send_command.side_effect = submit
        with patch(
            "custom_components.kumo_cloud.coordinator.asyncio.sleep", new=AsyncMock()
        ):
            first = asyncio.create_task(self.device.send_command({"spCool": 22.5}))
            await entered.wait()
            second = asyncio.create_task(second_device.send_command({"spCool": 23}))
            release.set()
            await asyncio.gather(first, second)
        self.assertEqual(
            [c.args[1]["spCool"] for c in self.api.send_command.call_args_list],
            [22.5, 23],
        )
        self.assertEqual(self.coordinator.pending_commands("unit-a"), {"spCool": 23})

    async def test_authentication_failure_starts_reauth_without_pending_command(self):
        self.coordinator.config_entry = Mock()
        self.api.send_command.side_effect = KumoCloudAuthError("expired")
        with self.assertRaises(ConfigEntryAuthFailed):
            await self.send({"spCool": 22.5})
        self.coordinator.config_entry.async_start_reauth.assert_called_once_with(
            self.hass
        )
        self.assertFalse(self.entity.available)
        self.assertEqual(self.coordinator.cached_commands, {})

    async def test_unload_cancels_expiry_callback(self):
        self.coordinator.cache_command("unit-a", "spCool", 22.5)
        timer = self.coordinator._pending_expiry
        await self.coordinator.async_shutdown()
        self.assertTrue(timer.cancelled())

    async def test_cancellation_before_acceptance_keeps_cache_empty_and_releases_lock(
        self,
    ):
        entered, release = asyncio.Event(), asyncio.Event()

        async def command(*_):
            entered.set()
            await release.wait()

        self.api.send_command.side_effect = command
        task = asyncio.create_task(self.device.send_command({"spCool": 22.5}))
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.coordinator.cached_commands, {})
        self.assertFalse(self.coordinator.command_locks["unit-a"].locked())

    async def test_poll_started_before_command_cannot_replace_newer_confirmed_readback(
        self,
    ):
        entered, release = asyncio.Event(), asyncio.Event()
        original_profile = self.coordinator.device_profiles["unit-a"]

        async def slow_profile(_):
            entered.set()
            await release.wait()
            return original_profile

        self.api.get_device_profile.side_effect = slow_profile
        poll = asyncio.create_task(self.coordinator.async_refresh())
        await entered.wait()
        self.api.get_device_details.side_effect = lambda _: detail(spCool=22.5)
        await self.send({"spCool": 22.5})
        self.assertEqual(self.coordinator.cached_commands, {})
        self.assertEqual(self.device.device_data["spCool"], 22.5)
        release.set()
        await poll
        self.assertEqual(self.device.device_data["spCool"], 22.5)
        self.assertEqual(self.entity.native_target_temperature, 72)
