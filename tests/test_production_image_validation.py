"""Filename validation for browsed production images (#83).

The SDK's only content gate before an irreversible write is
`is_production_asset()`, which just checks for the substring "production"
(omotion/firmware_update.py). It does not check console-vs-sensor. The GitHub
path cannot flash the wrong device's image -- it fetches production_asset(kind)
by exact name -- but the browse dialog added in #83 can, so the check lives here.
"""
import pytest

from motion_connector import _validate_production_image


@pytest.fixture
def image(tmp_path):
    """Make a named file on disk and return its path as a string."""
    def _make(name):
        p = tmp_path / name
        p.write_bytes(b"\x00" * 16)
        return str(p)
    return _make


@pytest.mark.parametrize("name", [
    "motion-console-production.bin",
    "motion-console-production-1.8.3.bin",
    "MOTION-CONSOLE-PRODUCTION.BIN",
])
def test_accepts_console_production_images(image, name):
    assert _validate_production_image("console", image(name)) == ""


@pytest.mark.parametrize("target", ["left", "right"])
def test_accepts_sensor_production_image_for_either_slot(image, target):
    assert _validate_production_image(target, image("motion-sensor-production.bin")) == ""


def test_rejects_sensor_image_for_console_the_brick_case(image):
    err = _validate_production_image("console", image("motion-sensor-production.bin"))
    assert err != ""
    assert "console" in err.lower()


def test_rejects_console_image_for_a_sensor(image):
    err = _validate_production_image("left", image("motion-console-production.bin"))
    assert err != ""
    assert "sensor" in err.lower()


def test_rejects_a_non_production_image(image):
    err = _validate_production_image("console", image("motion-console-fw-signed.bin"))
    assert err != ""
    assert "production" in err.lower()


def test_rejects_a_missing_file(tmp_path):
    err = _validate_production_image("console", str(tmp_path / "nope.bin"))
    assert err != ""


def test_rejects_an_unknown_target(image):
    err = _validate_production_image("middle", image("motion-console-production.bin"))
    assert err != ""
