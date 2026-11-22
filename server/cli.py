"""Single command-line parser for the game server.

Both entry points (``run_server.py`` and ``python -m server``) use this parser,
so ``--fps`` validation happens in one place and always before a UDP socket is
opened.
"""
from __future__ import annotations

import argparse
from typing import NamedTuple, Sequence

from shared.combat import (
    DEFAULT_TICK_RATE, MAX_TICK_RATE, MIN_TICK_RATE, validate_tick_rate,
)


class ServerArgs(NamedTuple):
    host: str
    port: int
    tick_rate: int


def _fps_type(value: str) -> int:
    try:
        return validate_tick_rate(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def build_server_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the ROUT III game server.")
    parser.add_argument(
        "--host", default="0.0.0.0", help="address to bind (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port", type=int, default=7777, help="UDP port to bind (default: 7777)",
    )
    parser.add_argument(
        "--fps", type=_fps_type, default=DEFAULT_TICK_RATE, metavar="N",
        help=(
            f"server tick rate, {MIN_TICK_RATE}..{MAX_TICK_RATE} "
            f"(default: {DEFAULT_TICK_RATE})"
        ),
    )
    return parser


def parse_server_args(argv: Sequence[str] | None = None) -> ServerArgs:
    """Parse server CLI arguments; invalid values raise ``SystemExit``."""
    args = build_server_parser().parse_args(argv)
    return ServerArgs(host=args.host, port=args.port, tick_rate=args.fps)
