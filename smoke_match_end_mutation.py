"""Mutation check: break the match-end system on purpose, one rule at a time.

A smoke test that passes on the old behaviour proves nothing.  Each mutation
below reverts one contract to what it was before match-end worked, and
smoke_match_end.py is re-run: every one of them has to FAIL.  If a mutation
survives, the test does not actually check that rule.

Run: py -3.12 smoke_match_end_mutation.py
"""

import io
import os
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")

# A small helper injected at the top of GameWorld.update to bring the old
# auto-respawn back: any dead player is revived on the spot every tick.
AUTORESPAWN = (
    "        self._update_secrets()\n        self._update_match()",
    "        for _p, _e in self.players.items():\n"
    "            _hp = self.entities.get_component(_e, Health)\n"
    "            if _hp is not None and _hp.value <= 0:\n"
    "                self._respawn_player(_e)  # MUTATED: auto-respawn\n"
    "        self._update_secrets()\n        self._update_match()",
)

MUTATIONS = [
    (
        "auto-respawn comes back (death revives on its own)",
        "server/game_world.py", *AUTORESPAWN,
    ),
    (
        "the exit never wins the level",
        "server/game_world.py",
        "            if math.hypot(pos.x - ex, pos.y - ey) <= EXIT_RADIUS:\n"
        "                self._end_match()\n"
        "                return",
        "            if False:\n"
        "                self._end_match()  # MUTATED: exit ignored\n"
        "                return",
    ),
    (
        "a dead player still wins on the exit",
        "server/game_world.py",
        "            if pos is None or hp is None or hp.value <= 0:\n"
        "                continue\n"
        "            if math.hypot(pos.x - ex, pos.y - ey) <= EXIT_RADIUS:",
        "            if pos is None or hp is None:\n"
        "                continue  # MUTATED: dead players count\n"
        "            if math.hypot(pos.x - ex, pos.y - ey) <= EXIT_RADIUS:",
    ),
    (
        "the PvP winner ignores frags (last player wins, never a draw)",
        "server/game_world.py",
        "            if kills > best:\n"
        "                best = kills\n"
        "                winner = pid\n"
        "                tie = False\n"
        "            elif kills == best:\n"
        "                tie = True",
        "            if True:\n"
        "                best = kills\n"
        "                winner = pid\n"
        "                tie = False  # MUTATED: frag order ignored",
    ),
    (
        "the world is not frozen when the match is over",
        "server/game_world.py",
        "        if self.match_phase == 'over':\n            return",
        "        if False:\n            return  # MUTATED: no freeze",
    ),
    (
        "a kill is not credited to the shooter",
        "server/weapons.py",
        "    if en['hp'] <= 0:\n"
        "        world.kill_enemy(en)\n"
        "        if shooter_eid is not None:\n"
        "            world.note_kill(shooter_eid)",
        "    if en['hp'] <= 0:\n"
        "        world.kill_enemy(en)  # MUTATED: kill not credited",
    ),
    (
        "dealt damage is not credited",
        "server/weapons.py",
        "    if shooter_eid is not None:\n"
        "        world.note_damage_dealt(shooter_eid, dmg)",
        "    if False:\n"
        "        world.note_damage_dealt(shooter_eid, dmg)  # MUTATED",
    ),
    (
        "a secret is counted on every tick",
        "server/game_world.py",
        "            found = self._secrets_found.setdefault(pid, set())\n"
        "            if sid in found:\n"
        "                continue\n"
        "            found.add(sid)\n"
        "            self.note_secret(eid)",
        "            self.note_secret(eid)  # MUTATED: no once-only",
    ),
    (
        "the match phase is dropped from the snapshot",
        "server/game_world.py",
        "            'phase': self.match_phase,",
        "            'phase': 'playing',  # MUTATED",
    ),
    (
        "per-player stats are dropped from the record",
        "server/game_world.py",
        "                \"stats\": self._stats_dict(eid),",
        "                \"stats\": {},  # MUTATED",
    ),
    (
        "a dead player's movement input is applied",
        "server/server.py",
        "        if world.match_phase == 'over':\n"
        "            return\n"
        "        hp_now = world.entities.get_component(entity_id, Health)\n"
        "        if (pl is not None and pl.state == 1) \\\n"
        "                or (hp_now is not None and hp_now.value <= 0):\n"
        "            return",
        "        if False:\n"
        "            return  # MUTATED: dead input applied",
    ),
    (
        "a dead player can fire",
        "server/server.py",
        "        if party.world.match_phase == 'over' \\\n"
        "                or (pl is not None and pl.state == 1) \\\n"
        "                or (hp is not None and hp.value <= 0):",
        "        if False:  # MUTATED: dead player fires",
    ),
    (
        "respawning a living player teleports and heals it",
        "server/game_world.py",
        "        if hp is not None and pl is not None and pl.state != 1 and hp.value > 0:\n"
        "            return",
        "        if False:\n            return  # MUTATED: live respawn not a noop",
    ),
    (
        "a picked item is not counted",
        "server/game_world.py",
        "        it['a'] = False\n        self.note_item(eid)\n        return True\n",
        "        it['a'] = False\n        return True  # MUTATED: pickup not counted\n",
    ),
    (
        "shots are not counted",
        "server/weapons.py",
        "    world.note_shot_fired(entity_id)\n    any_hit = False",
        "    any_hit = False  # MUTATED: shots_fired dropped",
    ),
    (
        "hits are not counted",
        "server/weapons.py",
        "    if any_hit:\n        world.note_shot_hit(entity_id)",
        "    if False:\n        world.note_shot_hit(entity_id)  # MUTATED",
    ),
]


def run_test():
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    return subprocess.run(
        [sys.executable, "smoke_match_end.py"],
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
