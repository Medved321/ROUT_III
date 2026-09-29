import pygame, math
from client.engine.constants import TEX_W
from client.engine.game_state import textures


def gen_textures():
    for name, maker in [
        ('wall', lambda x, y: (
            140 + ((x // 16) * 17 + (y // 8) * 13) % 30,
            (140 + ((x // 16) * 17 + (y // 8) * 13) % 30) // 3,
            (140 + ((x // 16) * 17 + (y // 8) * 13) % 30) // 4
        ) if not (y % 8 == 7 or (x % 16 == 15 and (y // 8) % 2 == 0) or (x % 16 == 0 and (y // 8) % 2 == 1))
        else (80, 80, 80)),
        ('stone', lambda x, y: (
            120 + ((x * 3 + y * 7) % 60) + (20 if ((x // 12) + (y // 12)) % 2 == 0 else 0),
            120 + ((x * 3 + y * 7) % 60) + (20 if ((x // 12) + (y // 12)) % 2 == 0 else 0),
            120 + ((x * 3 + y * 7) % 60) + (20 if ((x // 12) + (y // 12)) % 2 == 0 else 0)
        )),
        ('wood', lambda x, y: (
            120 + (x * 5) % 40,
            60 + (x * 5) % 20,
            30 + (x * 5) % 10
        )),
        ('exit', lambda x, y: (
            int(255 * (1 - abs(x - TEX_W // 2) / (TEX_W // 2))),
            int(191 * (1 - abs(x - TEX_W // 2) / (TEX_W // 2))),
            0
        )),
        ('elevator', lambda x, y: (
            int(85 * (0.3 + 0.7 * (math.sin(x * 0.3) * 0.5 + 0.5) * (math.cos(y * 0.3) * 0.5 + 0.5))),
            int(255 * (0.3 + 0.7 * (math.sin(x * 0.3) * 0.5 + 0.5) * (math.cos(y * 0.3) * 0.5 + 0.5))),
            int(255 * (0.3 + 0.7 * (math.sin(x * 0.3) * 0.5 + 0.5) * (math.cos(y * 0.3) * 0.5 + 0.5)))
        )),
        ('spikes', lambda x, y: (
            180 if (x % 16 in (6, 7, 8, 9) and y % 16 in (6, 7, 8, 9)) else 60,
            180 if (x % 16 in (6, 7, 8, 9) and y % 16 in (6, 7, 8, 9)) else 55,
            50 if (x % 16 in (6, 7, 8, 9) and y % 16 in (6, 7, 8, 9)) else 50
        )),
    ]:
        surf = pygame.Surface((TEX_W, TEX_W))
        for y in range(TEX_W):
            for x in range(TEX_W):
                surf.set_at((x, y), maker(x, y))
        textures[name] = surf

    # Floor texture — dark tiles with subtle variation
    floor_surf = pygame.Surface((TEX_W, TEX_W))
    for y in range(TEX_W):
        for x in range(TEX_W):
            v = 35 + ((x * 7 + y * 13) % 25)
            is_line = (x % 16 < 2) or (y % 16 < 2)
            c = (v, v, v) if is_line else (v + 10, v + 8, v + 5)
            floor_surf.set_at((x, y), c)
    textures['floor'] = floor_surf

    # Ceiling texture — dark with faint pattern
    ceil_surf = pygame.Surface((TEX_W, TEX_W))
    for y in range(TEX_W):
        for x in range(TEX_W):
            v = 15 + ((x * 11 + y * 5) % 15)
            if ((x // 8) + (y // 8)) % 2 == 0:
                v += 5
            ceil_surf.set_at((x, y), (v, v, v + 3))
    textures['ceiling'] = ceil_surf

    for ch, base in [('R', (180, 40, 40)), ('Y', (180, 180, 40)), ('B', (40, 40, 180)), ('D', (120, 80, 40))]:
        surf = pygame.Surface((TEX_W, TEX_W))
        for y in range(TEX_W):
            for x in range(TEX_W):
                border = x < 4 or x >= TEX_W - 4 or y < 4 or y >= TEX_W - 4
                panel = (TEX_W // 4 <= x <= TEX_W * 3 // 4) and (TEX_W // 4 <= y <= TEX_W * 3 // 4)
                if border:
                    surf.set_at((x, y), tuple(max(0, c - 60) for c in base))
                elif panel:
                    surf.set_at((x, y), tuple(min(255, c + 30) for c in base))
                else:
                    surf.set_at((x, y), base)
        textures[f'door_{ch}'] = surf

    # Meat wall — dark red flesh with vein-like streaks
    meat_surf = pygame.Surface((TEX_W, TEX_W))
    for y in range(TEX_W):
        for x in range(TEX_W):
            base_r = 100 + ((x * 7 + y * 13) % 50)
            base_g = 20 + ((x * 11 + y * 5) % 20)
            base_b = 20 + ((x * 3 + y * 17) % 15)
            vein = 1 if (x * 3 + y * 7) % 13 < 2 else 0
            if vein:
                base_r = max(0, base_r - 40)
                base_g = max(0, base_g - 10)
                base_b = max(0, base_b - 10)
            meat_surf.set_at((x, y), (base_r, base_g, base_b))
    textures['meat'] = meat_surf

    # Infected stone — gray with green corruption patches
    rst_surf = pygame.Surface((TEX_W, TEX_W))
    for y in range(TEX_W):
        for x in range(TEX_W):
            base = 110 + ((x * 3 + y * 7) % 50)
            patch = (x * 17 + y * 13) % 37
            if patch < 8:
                g = 80 + ((x * 5 + y * 11) % 40)
                rst_surf.set_at((x, y), (base - 20, g, base - 30))
            else:
                rst_surf.set_at((x, y), (base, base, base))
    textures['infected_stone'] = rst_surf

    # Martyr wall — meat with a distorted face pattern
    mar_surf = pygame.Surface((TEX_W, TEX_W))
    for y in range(TEX_W):
        for x in range(TEX_W):
            br = 100 + ((x * 7 + y * 13) % 40)
            bg = 25 + ((x * 11 + y * 5) % 15)
            bb = 20 + ((x * 3 + y * 17) % 10)
            cx, cy = TEX_W // 2, TEX_W // 2
            dx, dy = x - cx, y - cy
            dist = math.sqrt(dx * dx + dy * dy)
            # Eyes
            if abs(dx - 12) < 4 and abs(dy + 4) < 4 and dist < 20:
                br, bg, bb = 200, 180, 160
            elif abs(dx + 12) < 4 and abs(dy + 4) < 4 and dist < 20:
                br, bg, bb = 200, 180, 160
            # Pupils
            if abs(dx - 12) < 2 and abs(dy + 4) < 2:
                br, bg, bb = 30, 30, 30
            elif abs(dx + 12) < 2 and abs(dy + 4) < 2:
                br, bg, bb = 30, 30, 30
            # Mouth
            if abs(dy - 8) < 3 and abs(dx) < 18:
                br, bg, bb = 40, 10, 10
            if abs(dy - 8) < 1 and abs(dx) < 18:
                br, bg, bb = 180, 140, 120
            mar_surf.set_at((x, y), (br, bg, bb))
    textures['martyr'] = mar_surf

    # Barrel texture — red cylinder with metallic bands
    bar_surf = pygame.Surface((TEX_W, TEX_W))
    for y in range(TEX_W):
        for x in range(TEX_W):
            cx = TEX_W // 2
            dx = abs(x - cx)
            band = abs(y - TEX_W // 2) < 4 or y < 6 or y > TEX_W - 7
            if band:
                col = (80 + (x * 3) % 20, 70 + (x * 3) % 20, 60 + (x * 3) % 20)
            elif dx < 22:
                shade = 1.0 - dx * 0.02
                r = int(160 * shade + (x * 7 + y * 13) % 30)
                g = int(30 * shade + (x * 5 + y * 11) % 15)
                b = int(15 * shade)
                col = (min(255, r), min(255, g), min(255, b))
            else:
                col = (50, 40, 35)
            bar_surf.set_at((x, y), col)
    textures['barrel'] = bar_surf

    # Destructible wall — cracked brick
    dh_surf = pygame.Surface((TEX_W, TEX_W))
    for y in range(TEX_W):
        for x in range(TEX_W):
            base = 100 + ((x * 7 + y * 13) % 40)
            crack = 1 if (x * 17 + y * 31) % 19 < 2 else 0
            if crack:
                base = max(0, base - 50)
            dh_surf.set_at((x, y), (base, base - 20, base - 30))
    textures['hwall'] = dh_surf

    # Lava floor — orange/red glow with dark cracks
    lava_surf = pygame.Surface((TEX_W, TEX_W))
    for y in range(TEX_W):
        for x in range(TEX_W):
            r = 180 + (x * 13 + y * 7) % 60
            g = 50 + (x * 7 + y * 17) % 40
            b = 5 + (x * 5 + y * 11) % 15
            dark = 1 if (x % 12 < 2 and y % 12 < 2) else 0
            if dark:
                r, g, b = 60, 10, 0
            lava_surf.set_at((x, y), (min(255, r), min(255, g), min(255, b)))
    textures['lava'] = lava_surf

    # Switch panel — green glowing panel with button
    sw_surf = pygame.Surface((TEX_W, TEX_W))
    for y in range(TEX_W):
        for x in range(TEX_W):
            gl = int(50 + 40 * (math.sin(x * 0.3) * 0.5 + 0.5) + 40 * (math.cos(y * 0.3) * 0.5 + 0.5))
            r = 20 + gl // 4
            g = 100 + gl
            b = 20 + gl // 6
            border = x < 3 or x >= TEX_W - 3 or y < 3 or y >= TEX_W - 3
            if border:
                r, g, b = 60, 60, 60
            center = abs(x - TEX_W // 2) < 8 and abs(y - TEX_W // 2) < 8
            if center:
                r, g, b = 100 + gl // 2, 200 + gl, 100 + gl // 2
            sw_surf.set_at((x, y), (min(255, r), min(255, g), min(255, b)))
    textures['switch'] = sw_surf

    # Bridge — wooden planks
    br_surf = pygame.Surface((TEX_W, TEX_W))
    for y in range(TEX_W):
        for x in range(TEX_W):
            plank = y // 8
            shade = 100 + (plank * 20) % 40 + (x * 5) % 20
            r = min(255, shade + 30)
            g = min(255, 60 + shade // 3)
            b = min(255, 30 + shade // 5)
            gap = 1 if y % 8 == 7 else 0
            if gap:
                r, g, b = 20, 15, 10
            br_surf.set_at((x, y), (r, g, b))
    textures['bridge'] = br_surf

    # Platform — metallic floor with directional arrows
    pl_surf = pygame.Surface((TEX_W, TEX_W))
    for y in range(TEX_W):
        for x in range(TEX_W):
            v = 80 + ((x * 5 + y * 7) % 40)
            is_rail = x < 4 or x >= TEX_W - 4 or y < 4 or y >= TEX_W - 4
            if is_rail:
                v = 120 + ((x * 3 + y * 5) % 20)
            pl_surf.set_at((x, y), (v, v + 10, v + 20))
    textures['platform'] = pl_surf

    # Pentagram — dark floor with glowing purple star
    pen_surf = pygame.Surface((TEX_W, TEX_W))
    cx, cy = TEX_W // 2, TEX_W // 2
    for y in range(TEX_W):
        for x in range(TEX_W):
            dx, dy = x - cx, y - cy
            dist = math.sqrt(dx * dx + dy * dy)
            r = 40 + (x * 7 + y * 13) % 30
            g = 5 + (x * 11 + y * 5) % 15
            b = 60 + (x * 3 + y * 17) % 40
            gl = max(0, 1 - dist / 20)
            if gl > 0:
                r += int(100 * gl)
                b += int(200 * gl)
            line = 0
            for angle in [math.pi / 2, math.pi / 2 + 2 * math.pi / 5, math.pi / 2 + 4 * math.pi / 5,
                          math.pi / 2 + 6 * math.pi / 5, math.pi / 2 + 8 * math.pi / 5]:
                ex = cx + int(math.cos(angle) * 20)
                ey = cy + int(math.sin(angle) * 20)
                ldx, ldy = ex - cx, ey - cy
                t = ((x - cx) * ldx + (y - cy) * ldy) / (ldx * ldx + ldy * ldy + 0.01)
                if 0 <= t <= 1:
                    px, py = cx + ldx * t, cy + ldy * t
                    if abs(x - px) < 2 and abs(y - py) < 2:
                        line = 1
            if line:
                r, g, b = 180, 50, 255
            pen_surf.set_at((x, y), (min(255, r), min(255, g), min(255, b)))
    textures['pentagram'] = pen_surf
