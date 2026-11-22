"""Entry point: python -m server"""
import asyncio
import logging
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

from server.cli import parse_server_args
from server.server import GameServer
from shared.asyncio_util import use_udp_safe_event_loop

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(name)s %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)


def main():
    args = parse_server_args(sys.argv[1:])
    server = GameServer(args.host, args.port, tick_rate=args.tick_rate)

    use_udp_safe_event_loop()
    try:
        asyncio.run(server.start())
    except KeyboardInterrupt:
        server.stop()
        logging.info("Server stopped")


if __name__ == "__main__":
    main()
