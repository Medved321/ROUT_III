"""Entry point: python -m client"""
import asyncio
import logging
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

from client.numpy_bootstrap import require_numpy

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


def main():
    host, port, name = launch_config(sys.argv)
    client = GameClient(host, port, name)

    use_udp_safe_event_loop()
    try:
        asyncio.run(client.run())
    except KeyboardInterrupt:
        client.running = False
        client.renderer.cleanup()
        logging.info("Client stopped")


if __name__ == "__main__":
    main()
