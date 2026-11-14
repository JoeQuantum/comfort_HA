"""Diagnostic exports redact identities in both field values and map keys."""

import json
from copy import deepcopy
from types import SimpleNamespace

from helpers import IntegrationTestCase

from custom_components.kumo_cloud.diagnostics import (
    async_get_config_entry_diagnostics,
    async_get_device_diagnostics,
)


class DiagnosticsTests(IntegrationTestCase):
    async def test_account_and_device_map_identifiers_are_redacted_without_mutation(
        self,
    ):
        self.coordinator.devices["unit-a"].update(
            serialNumber="serial-secret",
            cryptoSerial="crypto-secret",
            macAddress="mac-secret",
            ssid="ssid-secret",
            ipAddress="ip-secret",
            nested={"unit-a": {"email": "nested@example.invalid"}},
        )
        self.coordinator.device_statuses["unit-a"] = {
            "firmwareVersion": "1.2.3",
            "routerSsid": "router-secret",
        }
        self.coordinator.wireless_sensors["unit-a"] = {"temperature": 22, "battery": 75}
        self.coordinator.zone_notifications["zone-a"] = {
            "filterDirtyReminderInterval": 90
        }
        data = {
            "username": "test@example.invalid",
            "password": "password-secret",
            "access_token": "access-secret",
            "refresh_token": "refresh-secret",
            "site_id": "site-secret",
        }
        entry_dict = {
            "unique_id": "test@example.invalid",
            "title": "My private property",
            "data": data,
            "version": 1,
        }
        entry = SimpleNamespace(
            runtime_data=self.coordinator, as_dict=lambda: entry_dict
        )
        original_devices = deepcopy(self.coordinator.devices)
        result = await async_get_config_entry_diagnostics(self.hass, entry)
        encoded = json.dumps(result)
        for secret in [
            "unit-a",
            "zone-a",
            "Bedroom",
            "serial-secret",
            "crypto-secret",
            "mac-secret",
            "ssid-secret",
            "ip-secret",
            "router-secret",
            "nested@example.invalid",
            "My private property",
            *data.values(),
        ]:
            with self.subTest(secret=secret):
                self.assertNotIn(secret, encoded)
        self.assertEqual(result["devices"]["device_1"]["roomTemp"], 22)
        self.assertEqual(
            result["device_statuses"]["device_1"]["firmwareVersion"], "1.2.3"
        )
        self.assertEqual(result["wireless_sensors"]["device_1"]["battery"], 75)
        self.assertIn("zone_1", result["zone_notifications"])
        self.assertEqual(self.coordinator.devices, original_devices)
        self.assertEqual(entry_dict["unique_id"], "test@example.invalid")

    async def test_device_diagnostics_redact_name_and_nested_identifiers(self):
        entry = SimpleNamespace(runtime_data=self.coordinator)
        device = SimpleNamespace(
            identifiers={("kumo_cloud", "unit-a")},
            name="Private bedroom",
            model="Model X",
            sw_version="1.2.3",
            manufacturer="Mitsubishi",
        )
        result = await async_get_device_diagnostics(self.hass, entry, device)
        encoded = json.dumps(result)
        self.assertNotIn("unit-a", encoded)
        self.assertNotIn("Private bedroom", encoded)
        self.assertEqual(result["device_info"]["model"], "Model X")
        self.assertEqual(result["device_data"]["roomTemp"], 22)

    async def test_real_config_entry_and_nested_readonly_mappings_are_redacted(self):
        from types import MappingProxyType

        from homeassistant.config_entries import ConfigEntry

        entry = ConfigEntry(
            data={
                "username": "real@example.invalid",
                "password": "password-secret",
                "nested": MappingProxyType({"refresh_token": "refresh-secret"}),
            },
            discovery_keys=MappingProxyType({}),
            domain="kumo_cloud",
            minor_version=1,
            options={},
            source="user",
            subentries_data=[],
            title="Private home",
            unique_id="real@example.invalid",
            version=1,
        )
        entry.runtime_data = self.coordinator
        encoded = json.dumps(await async_get_config_entry_diagnostics(self.hass, entry))
        for secret in (
            "real@example.invalid",
            "password-secret",
            "refresh-secret",
            "Private home",
        ):
            self.assertNotIn(secret, encoded)
        self.assertEqual(entry.data["password"], "password-secret")
