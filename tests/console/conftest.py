import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for p in (ROOT, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)


@pytest.fixture
def bus():
    """A fake USB bus with nothing plugged in; tests add FakeConsole objects to bus.consoles."""
    from console_fake import FakeBus
    from pico_tool.console import device
    fake = FakeBus()
    device.set_bus(fake)
    yield fake
    device.set_bus(None)
