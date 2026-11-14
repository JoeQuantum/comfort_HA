"""Overlapping polls and command readbacks must preserve the newer result."""

import asyncio
from unittest.mock import AsyncMock, patch

from helpers import IntegrationTestCase, detail

from custom_components.kumo_cloud.api import KumoCloudConnectionError


class RefreshOrderTests(IntegrationTestCase):
    async def assert_late_readback_ignored(self, *, older_fails, newer_fails=False):
        entered, release = asyncio.Event(), asyncio.Event()
        calls = 0

        async def fetch(_):
            nonlocal calls
            calls += 1
            if calls == 1:
                older_detail = detail(spCool=25, roomTemp=24)
                entered.set()
                await release.wait()
                if older_fails:
                    raise KumoCloudConnectionError("Older request timed out")
                return older_detail
            if newer_fails:
                raise KumoCloudConnectionError("Newer request failed")
            return detail(spCool=22.5, roomTemp=21)

        self.api.get_device_details.side_effect = fetch
        with patch(
            "custom_components.kumo_cloud.coordinator.asyncio.sleep", new=AsyncMock()
        ):
            command = asyncio.create_task(self.device.send_command({"spCool": 22.5}))
            try:
                await asyncio.wait_for(entered.wait(), timeout=2)
                await self.coordinator.async_refresh()
                expected_device = self.device.device_data.copy()
                expected_adapter = self.device.zone_data["adapter"].copy()
                expected_pending = self.coordinator.cached_commands.copy()
                self.assertEqual(self.entity.available, not newer_fails)
                release.set()
                await asyncio.wait_for(command, timeout=2)
                self.assertEqual(self.device.device_data, expected_device)
                self.assertEqual(self.device.zone_data["adapter"], expected_adapter)
                self.assertEqual(self.coordinator.cached_commands, expected_pending)
                self.assertEqual(self.entity.available, not newer_fails)
                if not newer_fails:
                    self.assertEqual(self.entity.native_target_temperature, 72)
                    self.assertEqual(self.device.device_data["roomTemp"], 21)
            finally:
                release.set()
                await asyncio.gather(command, return_exceptions=True)

    async def test_late_successful_readback_cannot_overwrite_newer_poll(self):
        await self.assert_late_readback_ignored(older_fails=False)

    async def test_late_failed_readback_cannot_make_newer_success_unavailable(self):
        await self.assert_late_readback_ignored(older_fails=True)

    async def test_late_successful_readback_cannot_hide_newer_poll_failure(self):
        await self.assert_late_readback_ignored(older_fails=False, newer_fails=True)

    async def test_older_poll_cannot_hide_newer_failed_command_readback(self):
        entered, release = asyncio.Event(), asyncio.Event()
        original_profile = self.coordinator.device_profiles["unit-a"]

        async def slow_profile(_):
            entered.set()
            await release.wait()
            return original_profile

        self.api.get_device_profile.side_effect = slow_profile
        poll = asyncio.create_task(self.coordinator.async_refresh())
        try:
            await asyncio.wait_for(entered.wait(), timeout=2)
            self.api.get_device_details.side_effect = KumoCloudConnectionError(
                "New readback failed"
            )
            with patch(
                "custom_components.kumo_cloud.coordinator.asyncio.sleep",
                new=AsyncMock(),
            ):
                await self.device.send_command({"spCool": 22.5})
            self.assertFalse(self.entity.available)
            release.set()
            await asyncio.wait_for(poll, timeout=2)
            self.assertFalse(self.entity.available)
            self.assertEqual(
                self.coordinator.pending_commands("unit-a"), {"spCool": 22.5}
            )
        finally:
            release.set()
            await asyncio.gather(poll, return_exceptions=True)
