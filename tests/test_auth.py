"""Token rotation, concurrency, bounded retries and HA reauthorization."""

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

from aiohttp import ClientResponseError
from helpers import HATestCase, IntegrationTestCase
from homeassistant.exceptions import ConfigEntryAuthFailed

from custom_components.kumo_cloud.api import (
    KumoCloudAPI,
    KumoCloudAuthError,
    KumoCloudConnectionError,
)


class Response:
    def __init__(self, status=200, body=None):
        self.status = status
        self.body = {} if body is None else body
        self.content_type = "application/json"

    async def __aenter__(self):
        # Yield so concurrency cases exercise overlapping requests, not serial mocks.
        await asyncio.sleep(0)
        return self

    async def __aexit__(self, *_):
        pass

    def raise_for_status(self):
        if self.status >= 400:
            raise ClientResponseError(
                SimpleNamespace(real_url="https://example.invalid"),
                (),
                status=self.status,
            )

    async def json(self):
        return self.body


class APIAuthTests(HATestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.session = Mock()
        self.persist = Mock()
        with patch(
            "custom_components.kumo_cloud.api.async_get_clientsession",
            return_value=self.session,
        ):
            self.api = KumoCloudAPI(self.hass, self.persist)
        self.api.access_token = "old-access"
        self.api.refresh_token = "old-refresh"

    def rotation(self):
        return Response(body={"access": "new-access", "refresh": "new-refresh"})

    async def test_rejected_access_token_recovers_once_with_new_headers(self):
        for status in (401, 403):
            with self.subTest(status=status):
                self.session.get.side_effect = [
                    Response(status),
                    Response(body={"ok": True}),
                ]
                self.session.post.return_value = self.rotation()
                self.session.post.reset_mock()
                self.persist.reset_mock()
                self.assertEqual(await self.api.get_account_info(), {"ok": True})
                self.session.post.assert_called_once()
                self.persist.assert_called_once()
                self.assertEqual(
                    self.session.get.call_args.kwargs["headers"]["Authorization"],
                    "Bearer new-access",
                )
                self.assertEqual(self.api.refresh_token, "new-refresh")

    async def test_proactive_rotation_notifies_persistence(self):
        self.api.token_expires_at = datetime.now() - timedelta(seconds=1)
        self.session.post.return_value = self.rotation()
        self.session.get.return_value = Response()
        await self.api.get_sites()
        self.persist.assert_called_once()
        self.assertEqual(
            self.session.get.call_args.kwargs["headers"]["Authorization"],
            "Bearer new-access",
        )

    async def test_repeated_rejection_is_bounded_and_requires_reauth(self):
        self.session.get.return_value = Response(401)
        self.session.post.return_value = self.rotation()
        for _ in range(2):
            with self.assertRaises(KumoCloudAuthError):
                await self.api.get_sites()
        self.assertEqual(self.session.get.call_count, 2)
        self.assertEqual(self.session.post.call_count, 1)

    async def test_refresh_401_and_403_are_auth_errors_without_repeated_rotation(self):
        for status in (401, 403):
            with self.subTest(status=status):
                self.api._auth_failed = False
                self.session.get.return_value = Response(401)
                self.session.post.return_value = Response(status)
                self.session.post.reset_mock()
                for _ in range(2):
                    with self.assertRaises(KumoCloudAuthError):
                        await self.api.get_sites()
                self.session.post.assert_called_once()

    async def test_concurrent_rejected_requests_share_one_refresh(self):
        self.session.get.side_effect = [
            Response(401),
            Response(401),
            Response(),
            Response(),
        ]
        self.session.post.return_value = self.rotation()
        await asyncio.gather(self.api.get_sites(), self.api.get_account_info())
        self.session.post.assert_called_once()
        self.persist.assert_called_once()
        self.assertEqual(self.session.get.call_count, 4)

    async def test_concurrent_rejected_refreshes_do_not_start_a_refresh_storm(self):
        self.session.get.return_value = Response(401)
        self.session.post.return_value = Response(403)
        results = await asyncio.gather(
            self.api.get_sites(), self.api.get_account_info(), return_exceptions=True
        )
        self.assertTrue(
            all(isinstance(result, KumoCloudAuthError) for result in results)
        )
        self.session.post.assert_called_once()
        self.persist.assert_not_called()

    async def test_concurrent_proactive_requests_share_one_refresh(self):
        self.api.token_expires_at = datetime.now() - timedelta(seconds=1)
        self.session.post.return_value = self.rotation()
        self.session.get.return_value = Response()
        await asyncio.gather(self.api.get_sites(), self.api.get_account_info())
        self.session.post.assert_called_once()
        self.persist.assert_called_once()

    async def test_command_recovers_explicit_auth_rejection_and_persists_rotation(self):
        self.session.post.side_effect = [
            Response(401),
            self.rotation(),
            Response(body={"success": True}),
        ]
        self.assertEqual(
            await self.api.send_command("unit-a", {"spHeat": 20}), {"success": True}
        )
        self.assertEqual(self.session.post.call_count, 3)
        self.persist.assert_called_once()
        self.assertEqual(
            self.session.post.call_args.kwargs["headers"]["Authorization"],
            "Bearer new-access",
        )

    async def test_command_timeout_is_not_replayed(self):
        self.session.post.side_effect = TimeoutError
        with self.assertRaises(KumoCloudConnectionError):
            await self.api.send_command("unit-a", {"spHeat": 20})
        self.session.post.assert_called_once()
        self.persist.assert_not_called()

    async def test_http_success_with_rejected_command_body_raises(self):
        for body in (
            {"success": False},
            {"errors": ["invalidSetpoint"]},
            {"error": "invalidMode"},
        ):
            with self.subTest(body=body):
                self.session.post.return_value = Response(body=body)
                with self.assertRaises(KumoCloudConnectionError):
                    await self.api.send_command("unit-a", {"spHeat": 20})

    async def test_invalid_token_pair_does_not_partially_rotate_or_persist(self):
        self.session.post.return_value = Response(body={"access": "new-access"})
        with self.assertRaises(KumoCloudConnectionError):
            await self.api.refresh_access_token()
        self.assertEqual(
            (self.api.access_token, self.api.refresh_token),
            ("old-access", "old-refresh"),
        )
        self.persist.assert_not_called()

    async def test_successful_login_clears_auth_failure_and_persists(self):
        self.api._auth_failed = True
        self.session.post.return_value = Response(
            body={"token": {"access": "login-access", "refresh": "login-refresh"}}
        )
        await self.api.login("test@example.invalid", "test-password")
        self.assertFalse(self.api._auth_failed)
        self.assertEqual(self.api.refresh_token, "login-refresh")
        self.persist.assert_called_once()

    async def test_login_401_and_403_are_auth_errors(self):
        for status in (401, 403):
            with self.subTest(status=status):
                self.session.post.return_value = Response(status)
                with self.assertRaises(KumoCloudAuthError):
                    await self.api.login("test@example.invalid", "wrong")

    async def test_refresh_transport_failure_is_not_classified_as_invalid_credentials(
        self,
    ):
        self.session.post.side_effect = OSError("offline")
        with self.assertRaises(KumoCloudConnectionError):
            await self.api.refresh_access_token()
        self.assertFalse(self.api._auth_failed)


class CoordinatorAuthTests(IntegrationTestCase):
    async def test_account_auth_failure_is_not_recursively_retried(self):
        self.api.get_zones.side_effect = KumoCloudAuthError("expired")
        self.api.get_zones.reset_mock()
        with self.assertRaises(ConfigEntryAuthFailed):
            await self.coordinator._async_update_data()
        self.api.get_zones.assert_awaited_once()
        self.api.refresh_access_token.assert_not_awaited()

    async def test_parallel_endpoint_auth_errors_reach_ha(self):
        for endpoint in (
            "get_device_details",
            "get_device_profile",
            "get_device_status",
            "get_zone_notification_preferences",
        ):
            method = getattr(self.api, endpoint)
            previous = method.side_effect
            with self.subTest(endpoint=endpoint):
                method.side_effect = KumoCloudAuthError("expired")
                with self.assertRaises(ConfigEntryAuthFailed):
                    await self.coordinator._async_update_data()
                method.side_effect = previous
        self.api.refresh_access_token.assert_not_awaited()

    async def test_direct_readback_auth_failure_starts_reauth(self):
        self.coordinator.config_entry = Mock()
        self.api.get_device_details.side_effect = KumoCloudAuthError("expired")
        with self.assertRaises(ConfigEntryAuthFailed):
            await self.coordinator.async_refresh_device("unit-a")
        self.coordinator.config_entry.async_start_reauth.assert_called_once_with(
            self.hass
        )
        self.assertFalse(self.entity.available)

    async def test_scheduled_poll_uses_ha_reauthentication_and_marks_unavailable(self):
        self.coordinator.config_entry = Mock()
        self.api.get_device_details.side_effect = KumoCloudAuthError("expired")
        await self.coordinator.async_refresh()
        self.coordinator.config_entry.async_start_reauth_if_available.assert_called_once_with(
            self.hass
        )
        self.assertFalse(self.entity.available)
        self.assertIsInstance(self.coordinator.last_exception, ConfigEntryAuthFailed)
