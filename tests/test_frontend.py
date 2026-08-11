"""Tests for serving the dashboard card from the integration.

These run against a real Home Assistant, so they are what proves the frontend
and http APIs used in async_register_frontend actually exist and are called
correctly - the card is otherwise only exercised in a browser.
"""

from pathlib import Path
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant

from custom_components.thermiq_mqtt import (
    CARD_FILENAME,
    CARD_VERSION,
    FRONTEND_REGISTERED,
    FRONTEND_URL_BASE,
    async_register_frontend,
)


def test_card_files_are_shipped_inside_the_integration():
    """HACS installs custom_components/ only, so the card must live there."""
    frontend = Path("custom_components/thermiq_mqtt/frontend")
    assert (frontend / CARD_FILENAME).is_file()
    assert (frontend / "heatpump_widget.j2").is_file()


def test_card_default_url_matches_the_served_path():
    """The card fetches its template from the path the integration registers."""
    card = (Path("custom_components/thermiq_mqtt/frontend") / CARD_FILENAME).read_text()
    assert f'const DEFAULT_URL = "{FRONTEND_URL_BASE}/heatpump_widget.j2"' in card


async def test_register_frontend_serves_directory_and_adds_module(hass: HomeAssistant):
    with (
        patch.object(
            hass.http, "async_register_static_paths", AsyncMock()
        ) as register_paths,
        patch("custom_components.thermiq_mqtt.add_extra_js_url") as add_js,
    ):
        await async_register_frontend(hass)

    configs = register_paths.call_args.args[0]
    assert len(configs) == 1
    assert configs[0].url_path == FRONTEND_URL_BASE
    assert configs[0].path.endswith("custom_components/thermiq_mqtt/frontend")
    # the template is edited in place, so it must not be cached indefinitely
    assert configs[0].cache_headers is False

    add_js.assert_called_once_with(
        hass, f"{FRONTEND_URL_BASE}/{CARD_FILENAME}?v={CARD_VERSION}"
    )
    assert hass.data[FRONTEND_REGISTERED] is True


async def test_register_frontend_is_idempotent(hass: HomeAssistant):
    """Registering the same static path twice raises, so it must run once."""
    hass.data[FRONTEND_REGISTERED] = True

    with (
        patch.object(
            hass.http, "async_register_static_paths", AsyncMock()
        ) as register_paths,
        patch("custom_components.thermiq_mqtt.add_extra_js_url") as add_js,
    ):
        await async_register_frontend(hass)

    register_paths.assert_not_called()
    add_js.assert_not_called()


async def test_registration_failure_does_not_break_setup(hass: HomeAssistant):
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
