from __future__ import annotations

import asyncio
import sys


def use_udp_safe_event_loop() -> None:
    """На Windows ProactorEventLoop ломает sendto() для UDP
    (WinError 10022, падения в DatagramTransport). Переключаемся на
    SelectorEventLoop ДО создания цикла событий."""
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def get_running_loop_or_create() -> asyncio.AbstractEventLoop:
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop