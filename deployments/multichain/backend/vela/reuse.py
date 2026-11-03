"""Import only copied source. Never import service entrypoints or production paths."""
import importlib.util
import sys
import types
from .settings import ROOT


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    value = importlib.util.module_from_spec(spec)
    sys.modules[name] = value
    spec.loader.exec_module(value)
    return value


signals = module("vela_legacy_signals", "components/why-bybit-shadow/wy_signals.py")
loss_stop = module("vela_legacy_loss_stop", "components/why-yellow/execution/loss_stop.py")
package = types.ModuleType("vela_discovery")
package.__path__ = [str(ROOT / "components/why-discovery/backend/discovery")]
sys.modules.setdefault("vela_discovery", package)
rules = module("vela_discovery.rules", "components/why-discovery/backend/discovery/rules.py")
model_module = module("vela_discovery.model", "components/why-discovery/backend/discovery/model.py")
