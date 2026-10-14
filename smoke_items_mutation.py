"""Mutation check: break the item system on purpose, one rule at a time.

A smoke test that passes on the old behaviour proves nothing. Each mutation
below reverts one contract to what it was before items worked, and the test is
re-run: every one of them has to FAIL. If a mutation survives, the test does not
actually check that rule.

Run: py -3.12 smoke_items_mutation.py
"""

import io
import os
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")

MUTATIONS = [
    (
        "items never spawn from the map",
        "server/game_world.py",
        "        self.spawn_items_from_map()",
        "        pass  # MUTATED: items never spawn",
    ),
    (
        "pickup ignores the radius (takes anything on the map)",
        "server/game_world.py",
        "                if math.hypot(pos.x - it['x'], pos.y - it['y']) > ITEM_PICKUP_DIST:\n                    continue",
        "                if False:\n                    continue  # MUTATED",
    ),
    (
        "a dead player loots",
        "server/game_world.py",
        "                if pos is None or hp is None or hp.value <= 0:\n                    continue",
        "                if pos is None or hp is None:\n                    continue  # MUTATED",
    ),
    (
        "a medkit heals nothing",
        "server/game_world.py",
        "            if hp is not None:\n                hp.value = min(hp.max_value, hp.value + spec['hp'])",
        "            if hp is not None:\n                hp.value = min(hp.max_value, hp.value + 0)  # MUTATED: heal dropped",
    ),
    (
        "a pickup at full health overheals past the cap",
        "server/game_world.py",
        "            if hp is not None:\n                hp.value = min(hp.max_value, hp.value + spec['hp'])",
        "            if hp is not None:\n                hp.value = hp.value + spec['hp']  # MUTATED: no cap",
    ),
    (
        "armor does not absorb (HP takes the whole hit)",
        "server/game_world.py",
        "        arm = self.entities.get_component(eid, Armor)\n        if arm is not None and arm.value > 0 and dmg > 0:\n            absorbed = min(arm.value, dmg)\n            arm.value -= absorbed\n            dmg -= absorbed",
        "        arm = self.entities.get_component(eid, Armor)\n        if arm is not None and False and dmg > 0:\n            absorbed = min(arm.value, dmg)  # MUTATED\n            arm.value -= absorbed\n            dmg -= absorbed",
    ),
    (
        "invulnerability no longer blocks damage",
        "server/game_world.py",
        "        if pu is not None and pu.kind == 'INVULN':\n            return False",
        "        if pu is not None and pu.kind == 'NEVER_MATCHES':\n            return False  # MUTATED",
    ),
    (
        "a re-picked buff stacks instead of refreshing",
        "server/game_world.py",
        "                pu.kind = spec['pu']\n                pu.ticks = spec['ticks']",
        "                pu.ticks = pu.ticks + spec['ticks'] if pu.kind == spec['pu'] else spec['ticks']  # MUTATED\n                pu.kind = spec['pu']",
    ),
    (
        "powerups never expire",
        "server/game_world.py",
        "            pu.ticks -= 1\n            if pu.ticks <= 0:\n                pu.kind = \"\"\n                pu.ticks = 0",
        "            pu.ticks = max(1, pu.ticks)  # MUTATED",
    ),
    (
        "respawn keeps the armor and the buff",
        "server/game_world.py",
        "        arm = self.entities.get_component(eid, Armor)\n        if arm is not None:\n            arm.value = 0\n        pu = self.entities.get_component(eid, Powerup)\n        if pu is not None:\n            pu.kind = \"\"\n            pu.ticks = 0",
        "        pass  # MUTATED: respawn keeps armor and powerups",
    ),
    (
        "a taken item is not returned on a player's death",
        "server/game_world.py",
        "        for it in self.items:\n            it['a'] = True",
        "        for it in self.items:\n            pass  # MUTATED: taken items stay gone",
    ),
    (
        "a taken item returns on its own (timer back)",
        "server/game_world.py",
        "        for it in self.items:\n"
        "            if not it['a']:\n"
        "                continue",
        "        for it in self.items:\n"
        "            if not it['a']:\n"
        "                it['a'] = True  # MUTATED: self-respawn\n"
        "                continue",
    ),
    (
        "a player's death does not reset the items",
        "server/game_world.py",
        "        self._reset_doors()\n        self._reset_items()",
        "        self._reset_doors()  # MUTATED: items never come back",
    ),
    (
        "berserk does not double damage",
        "server/weapons.py",
        "    dmg = wdef['d'] * dmg_mult",
        "    dmg = wdef['d']  # MUTATED",
    ),
    (
        "berserk still spends ammo",
        "server/weapons.py",
        "        if not berserk:\n            wp.ammo[ammo_key] = cur - cost",
        "        wp.ammo[ammo_key] = cur - cost  # MUTATED",
    ),
    (
        "berserk skips the empty-magazine refusal (dry-fires forever)",
        "server/weapons.py",
        "        if cur < cost:",
        "        if False:  # MUTATED",
    ),
    (
        "haste also multiplies the dash",
        "server/server.py",
        "            if dashing:\n                spd *= DASH_SPEED_MULT\n            else:\n                pu = world.entities.get_component(entity_id, Powerup)\n                if pu is not None and pu.kind == 'HASTE':\n                    spd *= HASTE_SPEED_MULT",
        "            if dashing:\n                spd *= DASH_SPEED_MULT\n            pu = world.entities.get_component(entity_id, Powerup)\n            if pu is not None and pu.kind == 'HASTE':\n                spd *= HASTE_SPEED_MULT  # MUTATED",
    ),
    (
        "the snapshot drops the items section",
        "shared/protocol.py",
        "    items = items or []\n    body += struct.pack(\"!H\", len(items))\n    for it in items:\n        itype = it.get(\"type\", it.get(\"t\", \"\"))\n        if isinstance(itype, str):\n            itype = ITEM_TYPES.index(itype) if itype in ITEM_TYPES else 0\n        body += struct.pack(\"!HffB\",\n                            it.get(\"id\", 0) & 0xFFFF,\n                            it[\"x\"], it[\"y\"], itype & 0xFF)",
        "    items = []  # MUTATED: items never encoded",
    ),
    (
        "the snapshot reports zero armor instead of none",
        "shared/protocol.py",
        "            \"armor\": ({\"type\": ARMOR_TYPES[armor_idx], \"value\": armor_value}\n                      if 0 <= armor_idx < len(ARMOR_TYPES) and armor_value > 0\n                      else None),",
        "            \"armor\": {\"type\": ARMOR_TYPES[0], \"value\": 0},  # MUTATED",
    ),
    (
        "pickups draw at a flat height instead of per type",
        "engine/rendering.py",
        "                    world_h = ITEM_GAME_HEIGHTS.get(s_obj['t'], 48)",
        "                    world_h = 64  # MUTATED",
    ),
    (
        "pickups lie on the floor instead of floating",
        "engine/rendering.py",
        "            if 't' in s_obj and 'id' in s_obj:\n                spr_flr -= int(sh * ITEM_FLOAT_HEIGHT)",
        "            if False:\n                spr_flr -= int(sh * ITEM_FLOAT_HEIGHT)  # MUTATED",
    ),
]


def run_test():
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    return subprocess.run(
        [sys.executable, "smoke_items.py"],
        capture_output=True, text=True, encoding="utf-8", env=env,
    )


print("baseline (unmutated)")
res = run_test()
if res.returncode != 0:
    print(res.stdout[-3000:])
    print(res.stderr[-2000:])
    print("BASELINE FAILS - fix the test before trusting any mutation result")
    sys.exit(1)
print("  ok   baseline passes\n")

survivors = []
for name, path, old, new in MUTATIONS:
    original = io.open(path, encoding="utf-8").read()
    if old not in original:
        print(f"  SKIP {name}: anchor not found in {path}")
        survivors.append(f"{name} (anchor missing)")
        continue
    io.open(path, "w", encoding="utf-8", newline="").write(
        original.replace(old, new, 1))
    try:
        res = run_test()
        caught = res.returncode != 0
        if caught:
            print(f"  ok   caught: {name}")
        else:
            print(f"  FAIL survived: {name}")
            survivors.append(name)
    finally:
        io.open(path, "w", encoding="utf-8", newline="").write(original)

print()
res = run_test()
if res.returncode != 0:
    print("WARNING: files not restored cleanly, baseline still fails")
    sys.exit(1)
print("baseline restored and passing")

if survivors:
    print(f"\nSURVIVED ({len(survivors)}): {survivors}")
    sys.exit(1)
print(f"\nall {len(MUTATIONS)} mutations caught")
