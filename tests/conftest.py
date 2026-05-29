"""
Shared test configuration.

Adds the charger package to sys.path and stubs out AppDaemon so that
charger_app.py can be imported without a running Home Assistant instance.

We use real types.ModuleType objects (not MagicMock) for the appdaemon
stubs so that Python's import machinery resolves 'import ... as hass'
to a deterministic module whose .Hass attribute is our plain FakeHass class.
"""

import sys
import types
from pathlib import Path

# Make 'charger' importable as a package (relative imports need the parent on path)
_APPS_DIR = Path(__file__).parent.parent / "appdaemon" / "apps"
if str(_APPS_DIR) not in sys.path:
    sys.path.insert(0, str(_APPS_DIR))


class FakeHass:
    """Minimal stand-in for appdaemon.plugins.hass.hassapi.Hass."""
    pass


# Build real module objects so 'import appdaemon.plugins.hass.hassapi as hass'
# resolves to a module whose .Hass attribute is our FakeHass class.
_hassapi = types.ModuleType("appdaemon.plugins.hass.hassapi")
_hassapi.Hass = FakeHass

_hass = types.ModuleType("appdaemon.plugins.hass")
_hass.hassapi = _hassapi

_plugins = types.ModuleType("appdaemon.plugins")
_plugins.hass = _hass

_appdaemon = types.ModuleType("appdaemon")
_appdaemon.plugins = _plugins

sys.modules["appdaemon"] = _appdaemon
sys.modules["appdaemon.plugins"] = _plugins
sys.modules["appdaemon.plugins.hass"] = _hass
sys.modules["appdaemon.plugins.hass.hassapi"] = _hassapi
