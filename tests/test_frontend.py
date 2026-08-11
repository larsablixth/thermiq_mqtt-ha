"""Tests for serving the dashboard card from the integration.

These run against a real Home Assistant, which is what proves the frontend and
http APIs used by async_register_frontend exist and are called correctly - the
card is otherwise only exercised in a browser.

Deliberately self-contained: it defines its own fixtures rather than adding to
conftest.py, so it does not collide with the test suite proposed in #78.
"""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.thermiq_mqtt import (
    CARD_FILENAME,
    CARD_VERSION,
    FRONTEND_REGISTERED,
    FRONTEND_URL_BASE,
    async_register_frontend,
)

FRONTEND_DIR = Path("custom_components/thermiq_mqtt/frontend")


@pytest.fixture
async def http(hass: HomeAssistant):
    """hass.http only exists once the http component is set up."""
    assert await async_setup_component(hass, "http", {})
    return hass.http


def test_card_files_are_shipped_inside_the_integration():
    """HACS installs custom_components/ only, so the card must live there."""
    assert (FRONTEND_DIR / CARD_FILENAME).is_file()
    assert (FRONTEND_DIR / "heatpump_widget.j2").is_file()


def test_card_default_url_matches_the_served_path():
    """The card fetches its template from the path the integration registers."""
    card = (FRONTEND_DIR / CARD_FILENAME).read_text()
    assert f'const DEFAULT_URL = "{FRONTEND_URL_BASE}/heatpump_widget.j2"' in card


async def test_card_and_template_are_actually_served(
    hass: HomeAssistant, http, hass_client
):
    """End to end: register for real, then fetch both files over HTTP."""
    with patch("custom_components.thermiq_mqtt.add_extra_js_url"):
        await async_register_frontend(hass)

    client = await hass_client()

    resp = await client.get(f"{FRONTEND_URL_BASE}/{CARD_FILENAME}")
    assert resp.status == 200
    assert "thermiq-widget-card" in await resp.text()

    resp = await client.get(f"{FRONTEND_URL_BASE}/heatpump_widget.j2")
    assert resp.status == 200
    assert "hpwidget" in await resp.text()


async def test_register_frontend_adds_the_module_once(hass: HomeAssistant, http):
    with patch("custom_components.thermiq_mqtt.add_extra_js_url") as add_js:
        await async_register_frontend(hass)

    add_js.assert_called_once_with(
        hass, f"{FRONTEND_URL_BASE}/{CARD_FILENAME}?v={CARD_VERSION}"
    )
    assert hass.data[FRONTEND_REGISTERED] is True

    # registering the same static path twice raises, so a second call must do
    # nothing at all
    with (
        patch.object(hass.http, "async_register_static_paths", AsyncMock()) as again,
        patch("custom_components.thermiq_mqtt.add_extra_js_url") as add_js_again,
    ):
        await async_register_frontend(hass)

    again.assert_not_called()
    add_js_again.assert_not_called()


async def test_registration_failure_does_not_break_setup(hass: HomeAssistant, http):
    """A frontend problem must not stop the integration from loading."""
    with (
        patch.object(
            hass.http,
            "async_register_static_paths",
            AsyncMock(side_effect=RuntimeError("boom")),
        ),
        patch("custom_components.thermiq_mqtt.add_extra_js_url") as add_js,
    ):
        await async_register_frontend(hass)

    add_js.assert_not_called()
    assert FRONTEND_REGISTERED not in hass.data
