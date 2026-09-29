---
id: 2026-09-16-render-engine-port
status: proposed
date: 2026-09-16
author: assistant
schema: spec-driven
---

# Перенос рендер-ядра из ROUT_II_III

## Что сделано

- Перенесено (без системы включения уровней) графическое ядро из
  `Desktop/ROUT_II_III` в `client/engine/`:
  - `constants.py` — копия (TILE=64, W/H=1000x700, SCALE=4, FOV, WPN,
    ENEMY_DB, BLOCK_TILES и т.д.);
  - `sprites.py`, `particles.py`, `textures.py` — копии, импорты переписаны
    на `client/engine.*`;
  - `rendering.py` — копия, импорты на `client/engine.*`, блок «другой игрок
    (co-op)» переведён с `gs.other_*` на цикл по `gs.players` (ники N игроков);
  - `dda.py` не импортируется rendering.py; трассировка DDA выполняется внутри
    `rendering.py` (скорость луч-шага, чтение MAP). Само ядро DDA-луки целей
    (AI/LOS) в мультиплеере не используется.
  - скопированы спрайты оружия `skins/` (19 PNG, включая SSG).
- Адаптеры / мост:
  - `client/engine/game_state.py` — шим глобального `gs` со значениями по
    умолчанию (все поля, которые читает рендер: HUD, частицы, мини-карта);
  - `client/engine/map_editor.py` — заглушка только с `TILE_CHAR_TO_TEX`;
  - `client/engine/map_utils.py` — обрезка `get_t` / `_point_in_sector` /
    `_player_in_any_sector`;
  - `client/engine/bridge.py` — `RenderBridge`: конвертация инт-карты
    `GameMap` в char-сетку (0→'.', 1→'#', 2→'T', 3→'W', 4→'M', 5→'I',
    6→'P'), пересчёт координат ×TILE, публикация позиций/ников игроков в
    `gs.players`, базовый `gs.p` (HP, аммо, оружие=пистолет), минимап открыт.
- Клиент (`client/client.py`): игровой цикл рендера переведён на `RenderBridge`
  вместо `Raycaster`/`Renderer.render_3d`; лобби осталось на нашем
  `LobbyScreen`; окно стало 1000x700.
- Протокол: в `WORLD_STATE` добавлено имя игрока (длина + utf-8, ≤32) —
  для ников игроков в рендере; обновлены `encode_world_state` /
  `decode_world_state` и `GameWorld.get_all_player_states`.
- Не перенесено: система уровней/меню/звук/враги ROUT_II_III, редактор
  карт, сектора/слафтер/деатматч-настройки — уровни остаются на
  `shared/map_data.GameMap`.

## Проверено

- `py_compile` по всем .py проекта.
- Headless-рендер: `RenderBridge` + `GameMap`, два удалённых игрока,
  кадр непустой (74/41 уникальных цветов по сетке 50px), спрайты оружия
  грузятся из `skins/`.
- Интеграция: сервер + клиент A + клиент B (127.0.0.1), create/join party,
  в `World State` приходят имена (`Beta`), `_init_from_server` →
  `_update_local_state` → `_render` (2 кадра) без исключений.

## Открытые вопросы

- Вид оружия/выбор оружия: оружие зафиксировано (пистолет, wpn=1);
  раскладка оружия по слотам — задача следующего шага.
- Дистанции/фог: используются их default (view_dist=900, fog off);
  настройки из меню ROUT_II_III не перенысятся (система настроек своя).
- `Renderer.flip()` (перетирка внутренней 320x200 поверхности) больше не
  используется в игровом цикле; лобби рисует напрямую в окно.
- Блок «Other players (co-op)» был вложен в `for s_obj in objs:` (пустой в
  мультиплеере) → игроки не рисовались. Исправлено: блок вынесен на уровень
  секции спрайтов (4-space), рисуется независимо от `objs`.
- Враги в мультиплеере отсутствуют по дизайну: сервер не спавнит врагов
  (только игроки); «врагов нет» — ожидаемое поведение. **(устарело — см.
  «Обновление: бой, оружие и режимы»)**

## Обновление 2026-09-17: бой, оружие и режимы (PvE/PvP)

Координатное решение изменено: **сервер переведён в пиксельные координаты
(TILE=64)**, как ROUT_II_III и рендер; bridge передаёт координаты как есть.

### Сервер

- `shared/map_data.py`: `TILE = 64`, px-хелперы `is_wall_px`, `can_move_px`
  (круг vs клетки), `has_los_px` (шаг 8 px), `spawn_px` (центры пустых клеток).
- `server/game_data.py` — новый: весь баланс в px (WPN из 6 оружий, ENEMY_DB
  из 20 типов, SPAWN_POOL, RANGED_DB, дистанции/радиусы/уроны, кулдауны в мс,
  корпуса).
- `server/game_world.py` — переписан: режимы `pve`/`pvp`, слайд-движение по
  осям через `can_move_px` (фикс отталкивания от стен, без снапа), пули
  врагов, корпуса, респавн игроков через `TICK_RATE*3`, ИИ врагов (цель с
  LOS, alert-AOE, separation, melee, ranged-проектили), снапшоты состояния.
- `server/weapons.py` — переписан: hitscan с пеллетами/спредом/конусом (шаги
  8 px, проверка стен), melee (chainsaw), расход аммо, кулдауны из 60fps-кадров,
  урон врагам (`kill_enemy` → корпус) и игрокам в PvP.
- `server/server.py`, `server/lobby.py`, `server/network.py`: режим партии,
  `weapon_id` во вводе, скорость `MOVE_SPEED_PX`, broadcast врагов/пуль/корпусов.

### Протокол

- `WORLD_STATE`: заголовок `!HBB` (tick, режим, число игроков); запись игрока
  `!BfffBBBBBBBB` (id, x, y, angle, hp, state, wpn + 5 аммо); враги
  `!H` + `!HfffBHB` (id, x, y, angle, type, hp u16, state); пули
  `!H` + `!ffBBB` (x, y, rgb); корпуса `!H` + `!ffB` (x, y, type).
- `CREATE_PARTY`: байт режима (`pve`=0, `pvp`=1); `LOBBY_STATE`: режим по
  каждой партии.

### Клиент

- `network_client.py`: состояние 6-tuple, `send_create_party(name, mode)`,
  `send_input(..., weapon_id)`; выход из партии → `state="lobby"`.
- `input_handler.py`: смена оружия клавишами 1–6 и колесом мыши.
- `client.py`: px-инициализация, локальные weapon/ammo/mode, fire-timer,
  передача врагов/пуль/корпусов/режима в bridge.
- `bridge.py` — переписан: px pass-through; заполняет `gs.players` (wpn/guns/
  ammo), `gs.enemies` (type→имя, цвет, hp, d_flash, anim), `gs.bul`,
  `gs.corpses`, `gs.lvl_type` по режиму, `fire_flag`/`w_tmr`.
- `rendering.py`: огонь по ЛКМ/`gs.fire_flag` (1809), «INF»-аммо только при
  `slaughter` (2161), `mode_str` для `pvp`/`pve` (2201+).
- `lobby_screen.py`: `[M]` переключает режим создания партии, режим виден в
  списке партий.

### Проверено (2026-09-17)

- `py_compile` по всем изменённым файлам (Python 3.12, pygame 2.6.1).
- `smoke_combat.py`: round-trip протокола (input/world/create/lobby/join),
  100 тиков мира, детерминированный PvP-урон (5 залпов → hp 200→0), boot сервера.
- `smoke_integration_combat.py`: сервер + 2 UDP-клиента — create/join PvE,
  движение, смена оружия (wpn доходит), получение врагов/корпусов/режима,
  выход в лобби; PvP-партия (нет врагов, смена оружия); full-render bridge
  под dummy SDL (враги/пули/корпуса, режим pve).
- Все файлы проекта запускаются на Python 3.12 (`py -3.12`); pygame стоит
  только там.

### Известные ограничения

- Мастер-клиент стрельбы: выстрел инициируется клиентом, но валидируется на
  сервере (кулдаун/аммо), сервер авторитетен по урону.
- ИИ врагов — упрощённая серверная модель (не полный порт ROUT_II_III AI).
- Сингл-аим в живом PvP-тесте нестабилен (полёт/тайминг), урон покрыт
  детерминированным юнит-тестом.

### Обновление (2026-09-17): анимация стрельбы и высота камеры

Запрос: «анимации стрельбы нет» + «камера будет выше, а то я всем по пояс».

- `client/client.py`: таймер анимации оружия теперь равен длительности
  `WPN[wpn]['f']` (пистолет 16 / дробовик 50 / винтовка 8 / BFG 30 / SSG 75
  кадров) вместо жёстких `6`; заводится по локальному кулдауну и только при
  наличии патронов (`info['a']`/`cost`), тикает раз в кадр.
- `client/engine/bridge.py`: `gs.w_tmr` получает полное значение таймера
  (не клампится к 6) — как `gs.w_tmr = wpn['f']` в ROUT_II_III; `gs.fire_flag`
  = таймер > 0.
- `client/engine/rendering.py`: введён `CAM_Z = 41` — в tile-режиме стены
  сдвинуты вниз на `(CAM_Z - TILE/2)*750/(rd+0.1)`, спрайты врагов/трупов и
  `spr_flr` предметов пересчитаны от новой линии пола `horizon + CAM_Z*sh/TILE`.
  Ранее стены/спрайты центрировались на горизонте → эффективная высота камеры
  была 32 (середина стены 64), отсюда вид «по пояс».
- `draw_corpse`: линия пола в tile-режиме тоже через `CAM_Z`.
- Новый тест `smoke_weapon_anim.py` (dummy SDL): кадр FIRE пистолета отличается
  от IDLE, кадры дробовика при `tmr` 50/30/10 различаются, смена оружия меняет
  картинку. PASS.
- Регрессия: `smoke_combat.py`, `smoke_integration_combat.py` — PASS.
- Должны быть добавлены следы от выстрелов и проджектайлы у плазма-пушки. **(выполнено — см. «Обновление 2026-09-18: плазма-шар и impact-события»)**

### Обновление 2026-09-18: плазма-шар и impact-события

Изменение: `openspec/changes/plasma-rifle-projectile` (spec-driven, все задачи выполнены).

**Протокол (`shared/protocol.py`, `PROTOCOL_VERSION` 2→3):**
- Bullets: `!ffBBB` → `!ffBBBB` (x, y, r, g, b, `from_player` uint8 flag).
- Новый блок impacts в конце: `!H count` + `!ffB` (x, y, type uint8: 0=wall, 1=enemy).
- `encode_world_state` принимает `impacts` (по умолчанию пусто).
- `decode_world_state` возвращает 7-tuple `(tick, players, enemies, bullets, corpses, mode, impacts)`.
- `SPEC.md`: обновлена секция World State (bullet flag, impacts, plasma description).

**Сервер (плазма-шар RIFLE):**
- `server/game_data.py`: `WPN[RIFLE]` — `'proj': True`, `proj_spd`/`PLAYER_PROJ_SPEED`, `proj_max_dist`/`PLAYER_PROJ_MAX_DIST`, `MAX_PLAYER_PROJ = 32`. Константы `IMPACT_TTL = 12`, `MAX_IMPACTS = 64`.
- `server/weapons.py`: ветка `wdef.get('proj')` в `handle_shoot` — спавн шара в `world.bullets` (from_player, vx/vy, src=entity_id), возврат без hitscan-урона. Impact-события в хитскан-ветках: `world.impact(x, y, type)` при попадании в стену/врага/игрока PvP.
- `server/game_world.py`: `_resolve_player_ball` (шар vs enemies + players PvP, исключая src), `_resolve_enemy_ball` (вынесен из бывшего `_update_bullets`), лимит MAX_PLAYER_PROJ (кап по oldest). `self.impacts`, `impact(x, y, type)`, `_update_impacts` (TTL), `get_impact_states`. `get_bullet_states` несёт `from_player`.
- `server/network.py`: `broadcast_world_state` принимает `impacts`, передаёт в encode.
- `server/server.py`: `_broadcast_parties` передаёт `w.get_impact_states()` в broadcast.

**Клиент:**
- `client/network_client.py`: `latest_state` — 7-tuple (includes impacts). `_handle_world_state` декодирует 7 элементов.
- `client/client.py`: `_update_local_state` распаковывает impacts; `_render` передаёт impacts в bridge.update.
- `client/engine/bridge.py`: `update` принимает `impacts`; разделяет bullets на `gs.bul` (enemy) и `gs.proj` (from_player, brightened col); спавнит локальные частицы для impact (wall → sparks + smoke, enemy → sparks + particles), дедупликация по signature с cooldown, лимит MAX_PARTICLES. Ветка orbs от рендеринга уже существовала и рисует 3-concentric circles для from_player.

**Рендер:** Ветка `from_player` в `rendering.py` (3 круга, без rotozoom) использовалась как есть; цвет берётся из WPN[RIFLE]['c'] через сервер.

**Проверено:**
- `py_compile` по всем изменённым файлам (Python 3.12).
- `smoke_combat.py`: round-trip bullets flag + impacts (0 и N); детерминированный RIFLE-шар (спавн/урон по полёту/стена + TTL impacts).
- `smoke_integration_combat.py`: сервер + 2 UDP-клиента, REGRESSION PASS.
- `smoke_projectiles.py`: интеграция — шар RIFLE виден второму клиенту, impact-события доходят, headless-рендер: кадр с шаром ≠ кадру без, impacts → particles (19 ≤ MAX_PARTICLES); урон по полёту.

### Известные ограничения
- Косметический спавн impact-частиц на клиенте повторяется пока событие живёт в WORLD_STATE (каждый received state), питается cooldown-дедупликацией (~12 кадров); MAX_PARTICLES защищает от переполнения.
- `spawn_sparks` не проверяет MAX_PARTICLES (в отличие от других spawn_*); в будущем — нормализовать все spawn_* под общий лимит.
