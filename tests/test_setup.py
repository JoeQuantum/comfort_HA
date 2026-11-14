"""Entry setup persists rotated credentials and preserves account configuration."""

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import test_auth as http
from helpers import HATestCase
from homeassistant.config_entries import ConfigEntries
from homeassistant.exceptions import ConfigEntryAuthFailed

from custom_components.kumo_cloud import async_setup_entry


class SetupTests(HATestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.hass.config_entries = ConfigEntries(self.hass, {})
        self.session = Mock()
        self.entry = SimpleNamespace(
            data={
                "username": "test@example.invalid",
                "password": "password-secret",
                "site_id": "site-a",
                "access_token": "old-access",
                "refresh_token": "old-refresh",
            }
        )
        self.coordinator = SimpleNamespace(async_config_entry_first_refresh=AsyncMock())

    async def setup_entry(self):
        def persist(entry, *, data):
            entry.data = data

        def coordinator(hass, api, site_id, *, config_entry):
            self.assertIs(config_entry, self.entry)
            self.coordinator.api = api
            return self.coordinator

        with (
            patch(
                "custom_components.kumo_cloud.api.async_get_clientsession",
                return_value=self.session,
            ),
            patch(
                "custom_components.kumo_cloud.KumoCloudDataUpdateCoordinator",
                side_effect=coordinator,
            ),
            patch.object(
                self.hass.config_entries, "async_update_entry", side_effect=persist
            ) as update,
            patch.object(
                self.hass.config_entries, "async_forward_entry_setups", new=AsyncMock()
            ),
        ):
            await async_setup_entry(self.hass, self.entry)
            return update

    async def test_setup_refresh_persists_both_tokens_and_preserves_site_and_password(
        self,
    ):
        self.session.get.side_effect = [http.Response(401), http.Response()]
        self.session.post.return_value = http.Response(
            body={"access": "new-access", "refresh": "new-refresh"}
        )
        update = await self.setup_entry()
        update.assert_called_once()
        self.assertEqual(
            self.entry.data,
            {
                "username": "test@example.invalid",
                "password": "password-secret",
                "site_id": "site-a",
                "access_token": "new-access",
                "refresh_token": "new-refresh",
            },
        )
        self.assertIs(self.entry.runtime_data, self.coordinator)
        self.coordinator.async_config_entry_first_refresh.assert_awaited_once()

    async def test_fallback_login_after_revoked_refresh_persists_replacement(self):
        self.session.get.return_value = http.Response(401)
        self.session.post.side_effect = [
            http.Response(403),
            http.Response(
                body={"token": {"access": "login-access", "refresh": "login-refresh"}}
            ),
        ]
        await self.setup_entry()
        self.assertEqual(self.entry.data["refresh_token"], "login-refresh")
        self.assertEqual(self.session.post.call_count, 2)

    async def test_exhausted_setup_auth_raises_config_entry_auth_failed(self):
        self.session.get.return_value = http.Response(401)
        self.session.post.return_value = http.Response(403)
        with self.assertRaises(ConfigEntryAuthFailed):
            await self.setup_entry()
        self.assertEqual(self.entry.data["refresh_token"], "old-refresh")
        self.coordinator.async_config_entry_first_refresh.assert_not_awaited()

    async def test_runtime_rotations_use_entry_persistence_callback(self):
        self.session.get.return_value = http.Response()
        await self.setup_entry()
        api = self.entry.runtime_data.api
        for proactive in (True, False):
            with self.subTest(proactive=proactive):
                api.token_expires_at = datetime.now() + timedelta(
                    seconds=-1 if proactive else 1200
                )
                self.session.get.side_effect = (
                    [http.Response()]
                    if proactive
                    else [http.Response(401), http.Response()]
                )
                pair = {
                    "access": f"access-{proactive}",
                    "refresh": f"refresh-{proactive}",
                }
                self.session.post.return_value = http.Response(body=pair)

                def persist(entry, *, data):
                    entry.data = data

                with patch.object(
                    self.hass.config_entries, "async_update_entry", side_effect=persist
                ):
                    await api.get_sites()
                self.assertEqual(self.entry.data["access_token"], pair["access"])
                self.assertEqual(self.entry.data["refresh_token"], pair["refresh"])
