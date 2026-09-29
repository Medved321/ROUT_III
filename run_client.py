#!/usr/bin/env python3
"""Run the game client."""
import asyncio
import logging
import os
import sys
from pathlib import Path

# Assets (skins/) are loaded from relative paths by the engine; make the
# project root the working directory regardless of how this script is run.
os.chdir(Path(__file__).resolve().parent)

sys.path.insert(0, ".")

from client.numpy_bootstrap import require_numpy  # noqa: E402

if not require_numpy():
    raise SystemExit(1)

from client.client import GameClient
from client.settings import launch_config
from shared.asyncio_util import use_udp_safe_event_loop

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(name)s %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)

if __name__ == "__main__":
    host, port, name = launch_config(sys.argv)
    client = GameClient(host, port, name)

    use_udp_safe_event_loop()
    try:
        asyncio.run(client.run())
    except KeyboardInterrupt:
        client.running = False
        client.renderer.cleanup()
        print("\nClient stopped.")
