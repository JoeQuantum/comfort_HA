"""API client for Kumo Cloud."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

import aiohttp
from aiohttp import ClientResponseError
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    API_APP_VERSION,
    API_BASE_URL,
    API_VERSION,
    TOKEN_EXPIRY_MARGIN,
    TOKEN_REFRESH_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)


class KumoCloudError(HomeAssistantError):
    """Base exception for Kumo Cloud."""


class KumoCloudAuthError(KumoCloudError):
    """Authentication error."""


class KumoCloudConnectionError(KumoCloudError):
    """Connection error."""


class KumoCloudAPI:
    """Kumo Cloud API client."""

    def __init__(
        self, hass: HomeAssistant, on_token_update: Callable[[], None] | None = None
    ) -> None:
        """Initialize the API client."""
        self.hass = hass
        self.session = async_get_clientsession(hass)
        self.base_url = API_BASE_URL
        self.username: str | None = None
        self.access_token: str | None = None
        self.refresh_token: str | None = None
        self.token_expires_at: datetime | None = None
        self._on_token_update = on_token_update
        self._refresh_lock = asyncio.Lock()
        self._token_generation = 0
        self._auth_failed = False

    def _store_tokens(self, tokens: dict[str, Any]) -> None:
        """Publish and persist a complete token pair before another request runs."""
        if not isinstance(tokens, dict):
            raise KumoCloudConnectionError("Cloud returned an invalid token pair")
        access, refresh = tokens.get("access"), tokens.get("refresh")
        if (
            not isinstance(access, str)
            or not access
            or not isinstance(refresh, str)
            or not refresh
        ):
            raise KumoCloudConnectionError("Cloud returned an invalid token pair")
        self.access_token, self.refresh_token = access, refresh
        self.token_expires_at = datetime.now() + timedelta(
            seconds=TOKEN_REFRESH_INTERVAL
        )
        self._token_generation += 1
        self._auth_failed = False
        if self._on_token_update:
            self._on_token_update()

    async def login(self, username: str, password: str) -> dict[str, Any]:
        """Login to Kumo Cloud and return user data."""
        url = f"{self.base_url}/{API_VERSION}/login"
        headers = {
            "x-app-version": API_APP_VERSION,
            "Content-Type": "application/json",
        }
        data = {
            "username": username,
            "password": password,
            "appVersion": API_APP_VERSION,
        }

        try:
            async with asyncio.timeout(30):
                async with self.session.post(
                    url, headers=headers, json=data
                ) as response:
                    if response.status in (401, 403):
                        raise KumoCloudAuthError("Invalid username or password")
                    response.raise_for_status()
                    result = await response.json()

                    self.username = username
                    self._store_tokens(result["token"])

                    return result

        except TimeoutError as err:
            raise KumoCloudConnectionError("Connection timeout") from err
        except ClientResponseError as err:
            if err.status in (401, 403):
                raise KumoCloudAuthError("Invalid credentials") from err
            raise KumoCloudConnectionError(f"HTTP error: {err.status}") from err
        except KumoCloudError:
            raise
        except (ValueError, KeyError, TypeError) as err:
            raise KumoCloudConnectionError("Invalid login response") from err
        except Exception as err:
            raise KumoCloudConnectionError(f"Unexpected error: {err}") from err

    async def refresh_access_token(self, *, generation: int | None = None) -> None:
        """Refresh once for concurrent callers using the same token generation."""
        if generation is None:
            generation = self._token_generation
        async with self._refresh_lock:
            if self._auth_failed:
                raise KumoCloudAuthError("Authentication requires reauthorization")
            if generation != self._token_generation:
                return
            try:
                await self._refresh_access_token()
            except KumoCloudAuthError:
                self._auth_failed = True
                raise

    async def _refresh_access_token(self) -> None:
        """Rotate tokens while holding the account refresh lock."""
        if not self.refresh_token:
            raise KumoCloudAuthError("No refresh token available")

        url = f"{self.base_url}/{API_VERSION}/refresh"
        headers = {
            "x-app-version": API_APP_VERSION,
            "Content-Type": "application/json",
        }
        data = {"refresh": self.refresh_token}

        try:
            async with asyncio.timeout(30):
                async with self.session.post(
                    url, headers=headers, json=data
                ) as response:
                    if response.status in (401, 403):
                        raise KumoCloudAuthError("Refresh token expired")
                    response.raise_for_status()
                    result = await response.json()

                    self._store_tokens(result)

        except TimeoutError as err:
            raise KumoCloudConnectionError("Connection timeout during refresh") from err
        except ClientResponseError as err:
            if err.status in (401, 403):
                raise KumoCloudAuthError("Refresh token expired") from err
            raise KumoCloudConnectionError(
                f"HTTP error during refresh: {err.status}"
            ) from err
        except (aiohttp.ClientError, OSError) as err:
            raise KumoCloudConnectionError(
                f"Connection error during refresh: {err}"
            ) from err
        except (ValueError, KeyError, TypeError) as err:
            raise KumoCloudConnectionError("Invalid token refresh response") from err

    async def _ensure_token_valid(self) -> None:
        """Ensure access token is valid, refresh if needed."""
        if self._auth_failed:
            raise KumoCloudAuthError("Authentication requires reauthorization")
        if not self.access_token:
            raise KumoCloudAuthError("No access token available")

        if (
            self.token_expires_at
            and datetime.now() + timedelta(seconds=TOKEN_EXPIRY_MARGIN)
            >= self.token_expires_at
        ):
            await self.refresh_access_token()

    async def _request(
        self, method: str, endpoint: str, data: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Recover a rejected token once, for both polls and commands."""
        await self._ensure_token_valid()
        for attempt in range(2):
            generation = self._token_generation
            try:
                return await self._request_with_retries(method, endpoint, data)
            except KumoCloudAuthError:
                if attempt:
                    # Do not invalidate a newer token installed by another request.
                    if generation == self._token_generation:
                        self._auth_failed = True
                    raise
                await self.refresh_access_token(generation=generation)
        raise KumoCloudAuthError("Authentication failed")

    async def _request_with_retries(
        self, method: str, endpoint: str, data: dict[str, Any] | None
    ) -> dict[str, Any]:
        """Retry reads and explicit throttling, never an ambiguous command timeout."""
        url = f"{self.base_url}/{API_VERSION}{endpoint}"
        headers = {
            "x-app-version": API_APP_VERSION,
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

        max_retries = 3
        base_delay = 60

        for attempt in range(max_retries + 1):
            try:
                async with asyncio.timeout(30):
                    if method.upper() == "GET":
                        async with self.session.get(url, headers=headers) as response:
                            response.raise_for_status()
                            return await response.json()
                    elif method.upper() == "POST":
                        async with self.session.post(
                            url, headers=headers, json=data
                        ) as response:
                            response.raise_for_status()
                            if response.content_type == "application/json":
                                return await response.json()
                            return {}

            except TimeoutError as err:
                if method.upper() == "GET" and attempt < max_retries:
                    delay = base_delay * (2**attempt)
                    _LOGGER.warning(
                        "Request timeout (attempt %d/%d), retrying in %d seconds",
                        attempt + 1,
                        max_retries + 1,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                raise KumoCloudConnectionError("Request timeout") from err
            except ClientResponseError as err:
                if err.status in (401, 403):
                    raise KumoCloudAuthError("Authentication failed") from err
                if err.status == 429 and attempt < max_retries:
                    delay = base_delay * (2**attempt)
                    _LOGGER.warning(
                        "Rate limited (429), retrying in %d seconds (attempt %d/%d)",
                        delay,
                        attempt + 1,
                        max_retries + 1,
                    )
                    await asyncio.sleep(delay)
                    continue
                raise KumoCloudConnectionError(f"HTTP error: {err.status}") from err
            except (aiohttp.ClientError, OSError) as err:
                # Catches DNS failures (ClientConnectorDNSError), connection refused,
                # and other low-level socket errors that aren't ClientResponseError.
                raise KumoCloudConnectionError(f"Connection error: {err}") from err

    async def get_account_info(self) -> dict[str, Any]:
        """Get account information."""
        return await self._request("GET", "/accounts/me")

    async def get_sites(self) -> list[dict[str, Any]]:
        """Get list of sites."""
        return await self._request("GET", "/sites/")

    async def get_zones(self, site_id: str) -> list[dict[str, Any]]:
        """Get list of zones for a site."""
        return await self._request("GET", f"/sites/{site_id}/zones")

    async def get_device_details(self, device_serial: str) -> dict[str, Any]:
        """Get device details."""
        return await self._request("GET", f"/devices/{device_serial}")

    async def get_device_profile(self, device_serial: str) -> list[dict[str, Any]]:
        """Get device profile information."""
        return await self._request("GET", f"/devices/{device_serial}/profile")

    async def get_wireless_sensor(self, device_serial: str) -> dict[str, Any] | None:
        """Get wireless sensor data (battery, temperature, humidity, rssi).

        Returns None if the device has no wireless sensor attached.
        Endpoint: GET /v3/devices/{deviceSerial}/sensor
        """
        try:
            return await self._request("GET", f"/devices/{device_serial}/sensor")
        except KumoCloudConnectionError as err:
            if "404" in str(err):
                return None
            raise

    async def get_device_status(self, device_serial: str) -> dict[str, Any] | None:
        """Get device status (firmware version, WiFi signal, router info).

        Endpoint: GET /v3/devices/{deviceSerial}/status
        Returns: firmwareVersion, routerSsid, routerRssi, autoModeDisable,
                 roomTempDisplayOffset, modeHeat, modeDry, cryptoSerial, etc.
        """
        try:
            return await self._request("GET", f"/devices/{device_serial}/status")
        except KumoCloudConnectionError as err:
            if "404" in str(err):
                return None
            raise

    async def get_zone_notification_preferences(
        self, zone_id: str
    ) -> dict[str, Any] | None:
        """Get zone notification preferences (filter reminders, alert settings).

        Endpoint: GET /v3/zones/{zoneId}/notification-preferences
        Returns: filterDirtyReminderInterval, filterDirtyReminderLastSent,
                 sensorLowBattery, sensorSignalLost, lowTemp, highTemp, etc.
        """
        try:
            return await self._request(
                "GET", f"/zones/{zone_id}/notification-preferences"
            )
        except KumoCloudConnectionError as err:
            if "404" in str(err):
                return None
            raise

    async def send_command(
        self, device_serial: str, commands: dict[str, Any]
    ) -> dict[str, Any]:
        """Send command to device."""
        data = {"deviceSerial": device_serial, "commands": commands}
        response = await self._request("POST", "/devices/send-command", data)
        if isinstance(response, dict) and (
            response.get("success") is False
            or response.get("error")
            or response.get("errors")
        ):
            raise KumoCloudConnectionError("Cloud rejected the command")
        return response
