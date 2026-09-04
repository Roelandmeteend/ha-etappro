"""Laad `device.py` los van Home Assistant.

De module hangt alleen van `modbus-connection` af, maar het package eromheen
importeert Home Assistant. Daarom laden we het bestand rechtstreeks.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_PATH = Path(__file__).parent.parent / "custom_components" / "etappro" / "device.py"
_SPEC = importlib.util.spec_from_file_location("etappro_device", _PATH)
assert _SPEC is not None and _SPEC.loader is not None
device = importlib.util.module_from_spec(_SPEC)
sys.modules["etappro_device"] = device
_SPEC.loader.exec_module(device)


@pytest.fixture(name="device_module")
def device_module_fixture():
    """De module onder test."""
    return device
