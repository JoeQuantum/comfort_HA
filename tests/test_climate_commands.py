"""Mode/temperature service behavior in Celsius and Fahrenheit."""

from copy import deepcopy
from unittest.mock import AsyncMock

from homeassistant.components.climate import HVACMode

try:
    from homeassistant.components.climate.services import _async_service_temperature_set
except ImportError:  # HA 2026.10, before climate services moved into their own module.
    from homeassistant.components.climate import _async_service_temperature_set
from helpers import IntegrationTestCase, detail
from homeassistant.const import UnitOfTemperature
from homeassistant.core import ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util.unit_system import METRIC_SYSTEM

from custom_components.kumo_cloud.api import KumoCloudConnectionError
from custom_components.kumo_cloud.temperature import F_TO_C


class ClimateCommandTests(IntegrationTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.entity._send_command_and_refresh = AsyncMock()

    async def service(self, **data):
        await _async_service_temperature_set(
            self.entity, ServiceCall(self.hass, "climate", "set_temperature", data)
        )
        return self.entity._send_command_and_refresh.call_args.args[0]

    async def test_combined_heat_updates_heat_not_current_cooling_setpoint(self):
        command = await self.service(temperature=72, hvac_mode=HVACMode.HEAT)
        self.assertEqual(
            command, {"operationMode": "heat", "spHeat": 22.5, "spCool": 25}
        )
        self.entity._send_command_and_refresh.assert_awaited_once()

    async def test_combined_cool_from_heat_preserves_heat_target(self):
        self.coordinator.devices["unit-a"]["operationMode"] = "heat"
        command = await self.service(temperature=72, hvac_mode=HVACMode.COOL)
        self.assertEqual(
            command, {"operationMode": "cool", "spHeat": 20, "spCool": 22.5}
        )

    async def test_combined_temperature_turns_on_an_off_unit(self):
        self.coordinator.devices["unit-a"].update(power=0, operationMode="off")
        command = await self.service(temperature=72, hvac_mode=HVACMode.HEAT)
        self.assertEqual(command["operationMode"], "heat")
        self.assertEqual(command["spHeat"], 22.5)

    async def test_combined_auto_range_is_one_atomic_command(self):
        command = await self.service(
            target_temp_low=68, target_temp_high=77, hvac_mode=HVACMode.HEAT_COOL
        )
        self.assertEqual(command, {"operationMode": "auto", "spHeat": 20, "spCool": 25})
        self.entity._send_command_and_refresh.assert_awaited_once()

    async def test_partial_auto_range_retains_other_bound(self):
        self.coordinator.devices["unit-a"]["operationMode"] = "autoHeat"
        await self.entity.async_set_temperature(target_temp_low=69)
        self.assertEqual(
            self.entity._send_command_and_refresh.call_args.args[0],
            {"spHeat": 21, "spCool": 25},
        )

    async def test_invalid_request_never_changes_mode_or_sends_command(self):
        cases = [
            {"temperature": 72, "hvac_mode": "unsupported"},
            {"temperature": 72, "hvac_mode": HVACMode.OFF},
            {
                "target_temp_low": 77,
                "target_temp_high": 68,
                "hvac_mode": HVACMode.HEAT_COOL,
            },
            {
                "target_temp_low": 72,
                "target_temp_high": 72,
                "hvac_mode": HVACMode.HEAT_COOL,
            },
            {"temperature": float("nan"), "hvac_mode": HVACMode.HEAT},
            {"temperature": 100, "hvac_mode": HVACMode.HEAT},
        ]
        for data in cases:
            with self.subTest(data=data), self.assertRaises(ServiceValidationError):
                await self.entity.async_set_temperature(**data)
        self.entity._send_command_and_refresh.assert_not_awaited()
        self.assertEqual(self.entity.hvac_mode, HVACMode.COOL)

    async def test_fahrenheit_app_setpoints_are_preserved(self):
        for requested, expected in F_TO_C.items():
            with self.subTest(fahrenheit=requested):
                command = await self.service(temperature=requested)
                self.assertEqual(command["spCool"], expected)

    async def test_fractional_fahrenheit_keeps_existing_app_selection_policy(self):
        command = await self.service(temperature=68.9)
        self.assertEqual(command["spCool"], 21)

    async def test_metric_service_preserves_celsius_including_half_degrees(self):
        self.hass.config.units = METRIC_SYSTEM
        self.entity = self.make_entity()
        self.entity._send_command_and_refresh = AsyncMock()
        self.assertEqual(self.entity.native_temperature_unit, UnitOfTemperature.CELSIUS)
        self.assertEqual(self.entity.target_temperature_step, 0.5)
        for requested in (18, 18.5, 19, 20.5, 22.5):
            with self.subTest(celsius=requested):
                command = await self.service(temperature=requested)
                self.assertEqual(command["spCool"], requested)
        command = await self.service(
            target_temp_low=18, target_temp_high=22.5, hvac_mode=HVACMode.HEAT_COOL
        )
        self.assertEqual(
            command, {"operationMode": "auto", "spHeat": 18, "spCool": 22.5}
        )

    async def test_metric_readings_and_limits_do_not_round_trip_through_fahrenheit(
        self,
    ):
        self.hass.config.units = METRIC_SYSTEM
        self.entity = self.make_entity()
        self.coordinator.devices["unit-a"].update(roomTemp=18, spCool=18.5)
        self.assertEqual(self.entity.native_current_temperature, 18)
        self.assertEqual(self.entity.native_target_temperature, 18.5)
        self.assertEqual(self.entity.state_attributes["current_temperature"], 18)
        self.assertEqual((self.entity.min_temp, self.entity.max_temp), (10, 30))

    async def test_latest_device_target_wins_over_older_zone_target(self):
        self.coordinator.devices["unit-a"].update(spCool=22.5, roomTemp=18)
        self.assertEqual(self.entity.native_target_temperature, 72)
        self.assertEqual(self.entity.native_current_temperature, 65)
        self.assertEqual(self.coordinator.zones[0]["adapter"]["spCool"], 25)

    async def test_live_unit_change_keeps_metric_commands_and_readings_exact(self):
        self.assertEqual(
            self.entity.native_temperature_unit, UnitOfTemperature.FAHRENHEIT
        )
        self.hass.config.units = METRIC_SYSTEM
        self.assertEqual(self.entity.native_temperature_unit, UnitOfTemperature.CELSIUS)
        command = await self.service(temperature=18)
        self.assertEqual(command["spCool"], 18)
        self.coordinator.devices["unit-a"]["roomTemp"] = 18
        self.assertEqual(self.entity.native_current_temperature, 18)

    async def test_profile_failure_preserves_auto_range_commands_and_capabilities(self):
        original_profile = self.device.profile_data
        self.api.get_device_details.side_effect = lambda _: detail(
            operationMode="autoHeat"
        )
        self.api.get_device_profile.side_effect = KumoCloudConnectionError(
            "Profile timeout"
        )
        for _ in range(3):
            await self.coordinator.async_refresh()
            self.assertTrue(self.entity.available)
            self.assertEqual(self.device.profile_data, original_profile)
            self.assertIn(HVACMode.HEAT_COOL, self.entity.hvac_modes)
            command = await self.service(target_temp_low=68, target_temp_high=77)
            self.assertEqual(command, {"spHeat": 20, "spCool": 25})

    async def test_empty_profile_response_preserves_last_valid_profile(self):
        original_profile = self.device.profile_data
        self.api.get_device_profile.side_effect = None
        for response in (None, [], {}):
            with self.subTest(response=response):
                self.api.get_device_profile.return_value = response
                await self.coordinator.async_refresh()
                self.assertEqual(self.device.profile_data, original_profile)
                self.assertIn(HVACMode.HEAT_COOL, self.entity.hvac_modes)

    async def test_profile_recovery_updates_capabilities_and_temperature_validation(
        self,
    ):
        profile = deepcopy(self.device.profile_data)
        self.api.get_device_profile.side_effect = KumoCloudConnectionError(
            "Profile timeout"
        )
        await self.coordinator.async_refresh()
        profile[0]["minimumSetPoints"]["heat"] = 21
        self.api.get_device_profile.side_effect = None
        self.api.get_device_profile.return_value = profile
        await self.coordinator.async_refresh()
        self.assertEqual(self.device.profile_data, profile)
        with self.assertRaises(ServiceValidationError):
            await self.service(temperature=68, hvac_mode=HVACMode.HEAT)
        self.entity._send_command_and_refresh.assert_not_awaited()
        command = await self.service(temperature=72, hvac_mode=HVACMode.HEAT)
        self.assertEqual(command["spHeat"], 22.5)

    async def test_removed_devices_do_not_retain_profile_cache(self):
        self.api.get_zones.side_effect = None
        self.api.get_zones.return_value = []
        await self.coordinator.async_refresh()
        self.assertEqual(self.coordinator.device_profiles, {})
