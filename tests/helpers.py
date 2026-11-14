"""Offline HA fixtures shared by the regression suite."""

from copy import deepcopy
from datetime import UTC, datetime
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock

from homeassistant.core import HomeAssistant
from homeassistant.helpers import frame
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM

from custom_components.kumo_cloud.climate import KumoCloudClimate
from custom_components.kumo_cloud.coordinator import (
    KumoCloudDataUpdateCoordinator,
    KumoCloudDevice,
)

PROFILE = {
    "hasModeHeat": True,
    "hasModeCool": True,
    "hasModeAuto": True,
    "numberOfFanSpeeds": 3,
    "hasVaneSwing": True,
    "minimumSetPoints": {"heat": 10, "cool": 16},
    "maximumSetPoints": {"heat": 28, "cool": 30, "auto": 28},
}


def detail(**changes):
    return {
        "deviceSerial": "unit-a",
        "connected": True,
        "updatedAt": datetime.now(UTC).isoformat(),
        "roomTemp": 22,
        "spHeat": 20,
        "spCool": 25,
        "operationMode": "cool",
        "power": 1,
        "fanSpeed": "auto",
        **changes,
    }


class HATestCase(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.hass = HomeAssistant(self.directory.name)
        self.hass.config.units = US_CUSTOMARY_SYSTEM
        frame.async_setup(self.hass)

    async def asyncTearDown(self):
        await self.hass.async_stop()


class IntegrationTestCase(HATestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.api = SimpleNamespace(
            get_zones=AsyncMock(
                side_effect=lambda _: [
                    {"id": "zone-a", "name": "Bedroom", "adapter": detail()}
                ]
            ),
            get_device_details=AsyncMock(side_effect=lambda _: detail()),
            get_device_profile=AsyncMock(side_effect=lambda _: [deepcopy(PROFILE)]),
            get_device_status=AsyncMock(return_value={}),
            get_zone_notification_preferences=AsyncMock(return_value={}),
            refresh_access_token=AsyncMock(),
            send_command=AsyncMock(return_value={}),
        )
        self.coordinator = KumoCloudDataUpdateCoordinator(self.hass, self.api, "site-a")
        await self.coordinator.async_refresh()
        self.device = KumoCloudDevice(self.coordinator, "zone-a", "unit-a")
        self.entity = self.make_entity()

    def make_entity(self):
        entity = KumoCloudClimate(self.device)
        entity.hass = self.hass
        entity.entity_id = "climate.test_unit"
        entity.async_write_ha_state = Mock()
        return entity

    async def asyncTearDown(self):
        await self.coordinator.async_shutdown()
        await super().asyncTearDown()
