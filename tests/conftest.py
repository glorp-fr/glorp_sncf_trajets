"""Common fixtures."""

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations in all tests."""
    yield


@pytest.fixture(autouse=True)
async def paris_timezone(hass):
    """Run every test with Europe/Paris as HA time zone."""
    await hass.config.async_set_time_zone("Europe/Paris")
    yield
