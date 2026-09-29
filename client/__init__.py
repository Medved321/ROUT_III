"""Client package. Submodule imports are lazy so that importing the package
(or the numpy bootstrap, entry points) never imports pygame in advance."""
from __future__ import annotations

import importlib

_LAZY = {
    "GameClient": "client.client",
    "Raycaster": "client.raycaster",
    "Renderer": "client.renderer",
    "InputHandler": "client.input_handler",
    "NetworkClient": "client.network_client",
    "AssetManager": "client.assets",
    "LobbyScreen": "client.lobby_screen",
}


def __getattr__(name: str):
    module_name = _LAZY.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(importlib.import_module(module_name), name)


def __dir__():
    return sorted(list(globals()) + list(_LAZY))