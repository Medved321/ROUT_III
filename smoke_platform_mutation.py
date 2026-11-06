"""Mutation check: break the floating-platform contract, one rule at a time.

A smoke test that passes on the old behaviour proves nothing. Each mutation
below reverts one rule to what it was before floating sectors worked, and the
owning smoke test is re-run: every mutation has to FAIL. A survivor means the
rule is not actually checked.

Run: py -3.12 smoke_platform_mutation.py
"""

import io
import os
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")

# (name, path, old, new, smoke test)
MUTATIONS = [
    (
        "the host loop carves out the slab footprint (floor lost)",
        "shared/sector_map.py",
        "        return (not self.sectors[s].floating\n"
        "                and 0 <= other < len(self.sectors)\n"
        "                and self.sectors[other].floating)",
        "        return (False and not self.sectors[s].floating\n"
        "                and 0 <= other < len(self.sectors)\n"
        "                and self.sectors[other].floating)  # MUTATED",
        "smoke_sector_model.py",
    ),
    (
        "the slab border counts as a blocking wall",
        "shared/sector_map.py",
        "        if self.sectors[fs].floating or self.sectors[bs].floating:\n"
        "            return False",
        "        if False:  # MUTATED\n"
        "            return False",
        "smoke_sector_model.py",
    ),
    (
        "an ordinary sector may keep floor above ceiling",
        "shared/sector_map.py",
        "            elif s.floor_h > s.ceil_h:",
        "            elif False:  # MUTATED",
        "smoke_sector_model.py",
    ),
    (
        "a thing standing on a slab is accepted",
        "shared/sector_map.py",
        "            if self.sectors[inner].floating:\n"
        "                errs.append(\n"
        "                    f\"thing {i}: stands on floating sector {inner}\")\n"
        "                continue",
        "            if False:  # MUTATED\n"
        "                continue",
        "smoke_sector_model.py",
    ),
    (
        "a map with slabs never writes the SFLAGS slice",
        "shared/mt1.py",
        "        if any(floating):\n"
        "            lumps[L_SFLAGS] = encode_sflags(floating)",
        "        if False:  # MUTATED\n"
        "            lumps[L_SFLAGS] = encode_sflags(floating)",
        "smoke_mt1_format.py",
    ),
    (
        "a map without slabs still writes an SFLAGS slice",
        "shared/mt1.py",
        "        if any(floating):\n"
        "            lumps[L_SFLAGS] = encode_sflags(floating)",
        "        if True:  # MUTATED\n"
        "            lumps[L_SFLAGS] = encode_sflags(floating)",
        "smoke_mt1_format.py",
    ),
    (
        "the text parser accepts a thinner-than-minimum slab",
        "shared/text_map.py",
        "        if room.floor_h - room.ceil_h < MIN_PLATFORM_THICKNESS:",
        "        if False:  # MUTATED",
        "smoke_text_map.py::test_floating_room",
    ),
    (
        "render drops the floating flag of a slab",
        "shared/text_map.py",
        "        if sector.floating:\n"
        "            out.append(\"парит да\")",
        "        if False:  # MUTATED\n"
        "            out.append(\"парит да\")",
        "smoke_text_map.py::test_floating_room",
    ),
    (
        "movement ignores the support query (walks through a slab)",
        "server/game_world.py",
        "        return support_at(x, y, pos.z)",
        "        return 0.0  # MUTATED",
        "smoke_sector_server.py",
    ),
    (
        "a step never updates the accepted support height",
        "server/game_world.py",
        "                self._set_axis(pos, cur + step, axis)\n"
        "                pos.z = z",
        "                self._set_axis(pos, cur + step, axis)  # MUTATED",
        "smoke_sector_server.py",
    ),
    (
        "respawn keeps the height the player died at",
        "server/game_world.py",
        "        pos.z = self._ground_z(start[0], start[1])",
        "        pass  # MUTATED",
        "smoke_respawn.py",
    ),
    (
        "enemies may take the slab top as support",
        "server/game_world.py",
        "        return support_at(nx, ny, en.get('z', 0.0), allow_platforms=False)",
        "        return support_at(nx, ny, en.get('z', 0.0), allow_platforms=True)"
        "  # MUTATED",
        "smoke_sector_server.py",
    ),
    # ---- render contract (change ``floating-sector-platforms``, section 4) --
    (
        "the slab border draws a wall band in the base pass",
        "engine/sector_render.py",
        "    if scene.floating_line[li]:\n"
        "        # Only allow doors between floating sectors (both sides floating)\n"
        "        # Doors between floating and host must be skipped to preserve host floor spanning\n"
        "        if is_door_check:\n"
        "            fs_sec = scene.front_sec[li]\n"
        "            bs_sec = scene.back_sec[li]\n"
        "            both_floating = (0 <= fs_sec < len(scene.floating) and scene.floating[fs_sec] and\n"
        "                            0 <= bs_sec < len(scene.floating) and scene.floating[bs_sec])\n"
        "            if not both_floating:\n"
        "                return\n"
        "        else:\n"
        "            return",
        "    if False:  # MUTATED\n"
        "        pass",
        "smoke_platform_render.py",
    ),
    (
        "the plate overlay is painted before the walls",
        "engine/sector_render.py",
        "    # ---- Phase C: walls (bucket strips, sliced into the frame) ----\n"
        "    _draw_wall_bands(frame, bands, scene, view, iw, ih, iw_f, ih_f,"
        " fwidths)\n"
        "    # ---- Phase D: floating slabs (change"
        " ``floating-sector-platforms``) ----\n"
        "    # After the walls so a plate covers the far wall behind it, before"
        " the one\n"
        "    # upscale so it costs no second scaling.  A map without plates —"
        " the\n"
        "    # overwhelming majority — pays a single boolean here and never"
        " imports the\n"
        "    # module.\n"
        "    if gs.platforms_enabled and scene.has_floating:\n"
        "        from engine import platform_render\n"
        "\n"
        "        gs.platform_occluders = platform_render.draw_platforms(\n"
        "            frame, scene, px, py, pa, eye, F, hc, iw, ih, iw_f, ih_f,\n"
        "            fwidths, clip_top, clip_bot, view_buf, view, vh, vy)",
        "    # MUTATED: the plate goes down first, so the wall behind it wins\n"
        "    if gs.platforms_enabled and scene.has_floating:\n"
        "        from engine import platform_render\n"
        "\n"
        "        gs.platform_occluders = platform_render.draw_platforms(\n"
        "            frame, scene, px, py, pa, eye, F, hc, iw, ih, iw_f, ih_f,\n"
        "            fwidths, clip_top, clip_bot, view_buf, view, vh, vy)\n"
        "    _draw_wall_bands(frame, bands, scene, view, iw, ih, iw_f, ih_f,"
        " fwidths)",
        "smoke_platform_render.py",
    ),
    (
        "the overlay ignores the wall depth buffer",
        "engine/platform_render.py",
        "            ivs = _ray_intervals(loops, px, py, ca - k * sa,"
        " sa + k * ca, vb)",
        "            ivs = _ray_intervals(loops, px, py, ca - k * sa,"
        " sa + k * ca, view)  # MUTATED",
        "smoke_platform_render.py",
    ),
    (
        "floor_at returns the slab top instead of the host floor",
        "engine/sector_render.py",
        "        if self.has_floating:\n            f = self.sm.floor_under(x, y)",
        "        if self.has_floating:  # MUTATED\n"
        "            f = self.sm.sectors[self.sm.find_sector_at(x, y)].floor_h",
        "smoke_platform_render.py",
    ),
    (
        "a sprite fully covered by a plate is still drawn",
        "engine/platform_render.py",
        "    if not occluders:\n        return False",
        "    if True:  # MUTATED\n        return False",
        "smoke_platform_render.py",
    ),
]

TESTS = sorted({m[4] for m in MUTATIONS})


def run_test(test: str) -> subprocess.CompletedProcess:
    """Run a whole smoke script or a single ``file.py::function`` case.

    The function form keeps a mutation honest when the script also carries a
    known, unrelated red case (stale map fixture): the owning test is still
    the one that has to catch the mutation.
    """
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    path, _, func = test.partition("::")
    module = os.path.splitext(path)[0]
    cmd = ([sys.executable, path] if not func else
           [sys.executable, "-c", f"import {module}; {module}.{func}()"])
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", env=env,
    )


def run_tests():
    return {test: run_test(test) for test in TESTS}


def baseline_ok(failures) -> bool:
    ok = True
    for test, res in failures.items():
        if res.returncode != 0:
            print(f"BASELINE FAILS: {test}")
            print(res.stdout[-3000:])
            print(res.stderr[-2000:])
            ok = False
    return ok


print("baseline (unmutated)")
if not baseline_ok(run_tests()):
    print("fix the tests before trusting any mutation result")
    sys.exit(1)
print(f"  ok   baseline passes ({', '.join(TESTS)})\n")

survivors = []
for name, path, old, new, test in MUTATIONS:
    original = io.open(path, encoding="utf-8").read()
    if old not in original:
        print(f"  SKIP {name}: anchor not found in {path}")
        survivors.append(f"{name} (anchor missing)")
        continue
    io.open(path, "w", encoding="utf-8", newline="").write(
        original.replace(old, new, 1))
    try:
        res = run_tests()[test]
        caught = res.returncode != 0
        if caught:
            print(f"  ok   caught: {name} ({test})")
        else:
            print(f"  FAIL survived: {name} ({test})")
            survivors.append(name)
    finally:
        io.open(path, "w", encoding="utf-8", newline="").write(original)

print()
if not baseline_ok(run_tests()):
    print("WARNING: files not restored cleanly, baseline still fails")
    sys.exit(1)
print("baseline restored and passing")

if survivors:
    print(f"\nSURVIVED ({len(survivors)}): {survivors}")
    sys.exit(1)
print(f"\nall {len(MUTATIONS)} mutations caught")
