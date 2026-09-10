"""Unit and integration tests for screen capture."""

from pathlib import Path
from PIL import Image
import pytest
from perception.screenshot import ScreenCapture, get_screen_capture


def test_get_screen_size():
    capture = get_screen_capture()
    width, height = capture.get_screen_size()
    assert isinstance(width, int)
    assert isinstance(height, int)
    assert width > 0
    assert height > 0


def test_get_all_monitors():
    capture = get_screen_capture()
    monitors = capture.get_all_monitors()
    assert isinstance(monitors, list)
    assert len(monitors) >= 1
    assert "width" in monitors[0]
    assert "height" in monitors[0]


def test_capture_full_screen(tmp_path):
    capture = ScreenCapture(output_dir=tmp_path)
    save_file = tmp_path / "test_full.png"
    img = capture.capture_full_screen(save_path=save_file)

    assert isinstance(img, Image.Image)
    assert img.width > 0
    assert img.height > 0
    assert save_file.exists()
    assert save_file.stat().st_size > 0


def test_capture_region(tmp_path):
    capture = ScreenCapture(output_dir=tmp_path)
    save_file = tmp_path / "test_region.png"
    img = capture.capture_region(x=50, y=50, width=100, height=80, save_path=save_file)

    assert isinstance(img, Image.Image)
    assert img.width == 100
    assert img.height == 80
    assert save_file.exists()


def test_capture_invalid_region():
    capture = get_screen_capture()
    with pytest.raises(ValueError):
        capture.capture_region(x=0, y=0, width=0, height=100)
    with pytest.raises(ValueError):
        capture.capture_region(x=0, y=0, width=100, height=-10)
