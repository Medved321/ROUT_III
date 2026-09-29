#!/usr/bin/env python3
"""Run the game server."""
import asyncio
import logging
import sys

sys.path.insert(0, ".")

from server.server import GameServer
from shared.asyncio_util import use_udp_safe_event_loop

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(name)s %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)

if __name__ == "__main__":
    host = "0.0.0.0"
    port = 7777

    if "--port" in sys.argv:
        idx = sys.argv.index("--port")
        port = int(sys.argv[idx + 1])
    if "--host" in sys.argv:
        idx = sys.argv.index("--host")
        host = sys.argv[idx + 1]

    server = GameServer(host, port)

    use_udp_safe_event_loop()
    try:
        asyncio.run(server.start())
    except KeyboardInterrupt:
        server.stop()
        print("\nServer stopped.")
