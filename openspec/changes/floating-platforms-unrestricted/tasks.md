# Tasks

## 1. Data model and validation

- [x] 1.1 Update `shared/sector_map.py` floating-sector validation to remove artificial topology/placement/host restrictions while keeping data-integrity checks (`MIN_PLATFORM_THICKNESS`, valid references, thing ordinary-support rule, closed/interpretable geometry). Verify with a focused smoke case that previously rejected stacked/contact/overlapping floating sectors now validate, and invalid thin/unsupported-thing cases still fail.
- [x] 1.2 Update floating-sector support/geometry helpers (`support_at`, `sectors_at`, polygon/border handling as needed) so stacked slabs expose correct supports/ceilings without introducing gravity/jump. Verify with smoke cases for stacked slabs, under-slab passage, and step-up limited by `STEP_HEIGHT`.
- [x] 1.3 Update `shared/text_map.py` parse/render/validation for unrestricted floating room geometry and round-trip stacked/no-host/overlap cases. Verify by running a smoke that `parse(render(m))` preserves floating flags/heights/geometry for these cases.

## 2. Editor and authoring UX

- [x] 2.1 Update `client/sector_editor.py` floating toggle/geometry operations to allow valid stacked/contact/overlap configurations and only reject ambiguous or data-breaking edits. Verify with editor smoke covering floating toggle on multi-neighbor/stacked sectors and one undo step.
- [x] 2.2 Update `client/sector_editor_screen.py` property clamps/messages so floating height editing allows arbitrary valid map heights with minimum-thickness enforcement and no host/neighbor containment clamp. Verify with a smoke/manual check that high stacked slabs can be entered and saved without rejection.
- [x] 2.3 Add/adjust editor smoke coverage (`smoke_sector_editor.py`, `smoke_sector_editor_screen.py`) for unrestricted floating placement; verify by running `py -3.12 smoke_sector_editor.py` and `py -3.12 smoke_sector_editor_screen.py`.

## 3. Server 3D platform gameplay

- [x] 3.1 Implement height-aware slab-volume blocking for LOS in `shared/sector_map.py`/server LOS path used by `server/game_world.py`, so platforms block only when trajectory intersects slab volume at z. Verify with smoke cases: LOS blocked through slab volume, clear above slab, clear below slab.
- [x] 3.2 Implement height-aware hitscan in `server/weapons.py` (and shared helpers as needed), including shooter/target/slab heights, while preserving existing wall/door blocking. Verify with smoke cases for hitscan blocked by slab, passing over, and passing under.
- [x] 3.3 Implement height-aware projectile travel/collision in `server/game_world.py` for player/enemy projectiles against slab volumes and targets at height. Verify with smoke cases for projectile blocked by slab, passing over, and passing under; if client-visible projectile z is required, record protocol bump as a follow-up decision before implementation.
- [x] 3.4 Allow enemies to use platform tops as supports (`allow_platforms=True` path) with existing STEP_HEIGHT/ACTOR_HEIGHT rules and no jump/gravity. Verify with smoke where enemy steps onto reachable slab and cannot step onto slab above `STEP_HEIGHT`.

## 4. Renderer opacity and stacking

- [x] 4.1 Replace all-or-nothing sprite occlusion in `engine/platform_render.py`/`engine/rendering.py` with column/scanline clipping against platform occluder intervals so partially covered sprites are not see-through. Verify by updating `smoke_platform_render.py` to assert covered columns are hidden and uncovered columns remain visible.
- [x] 4.2 Ensure stacked/overlapping platform draw order and occluder intervals remain correct for near/far slabs. Verify with a smoke case containing stacked slabs where near slab hides far slab/sprite pixels as expected.
- [x] 4.3 Add/adjust platform render smoke coverage and run `py -3.12 smoke_platform_render.py`.

## 5. Integration and regression checks

- [x] 5.1 Add or update smoke tests for 3D LOS/hitscan/projectiles and enemy platform traversal; verify by running the new/updated smoke scripts with `py -3.12`.
- [x] 5.2 Run focused regression smokes for sector/platform behavior touched by this change (at minimum platform, sector-editor, sector-map/text-format smokes affected). Verify all selected smokes pass.
- [x] 5.3 Update any affected OpenSpec/design notes only if implementation reveals a necessary protocol decision (bullet z wire format); verify the decision is documented before code merge. Protocol v15 wire break: enemy/bullet records carry `z`; clients/servers must match `PROTOCOL_VERSION`.

## Workflow follow-up

- Review the change artifacts with the user and get an explicit apply request (`/opsx-apply` or equivalent) before editing project code.
- After implementation and verification, archive the OpenSpec change when the user requests it.
