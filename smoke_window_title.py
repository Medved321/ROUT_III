"""smoke_window_title.py — the game window carries the ROUT III caption.

``Renderer`` is the single owner of the display surface; both entry points
(desktop ``run_client`` and Android ``main.py``) reach ``set_mode`` through it.
The caption is therefore set there, and this smoke reads it back from SDL with
``pygame.display.get_caption()`` after construction and after a resize.
"""
import os
import sys

os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
import pygame

pygame.init()

from client.renderer import Renderer

renderer = Renderer((1000, 700))
title, _icon = pygame.display.get_caption()
assert title == "ROUT III", f"caption after startup: {title!r}"
print("window caption after startup OK")

# A resize re-creates the display surface but must keep the caption, since the
# client only ever calls set_window_size after the first construction.
renderer.set_window_size((1280, 720))
title, _icon = pygame.display.get_caption()
assert title == "ROUT III", f"caption after resize: {title!r}"
print("window caption survives resize OK")

# Fullscreen (the Android path) goes through the same constructor.
full = Renderer((0, 0), fullscreen=True)
title, _icon = pygame.display.get_caption()
assert title == "ROUT III", f"caption in fullscreen: {title!r}"
print("window caption in fullscreen OK")

print("ALL OK")
