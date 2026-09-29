## 1. Протокол (shared)

- [x] 1.1 Расширить bullets в `encode_world_state`/`decode_world_state` с `!ffBBB` до `!ffBBBB` (x, y, r, g, b, `from_player` flag) и проверка: round-trip тест в `smoke_protocol`-стиле
- [x] 1.2 Добавить impact-события в `WORLD_STATE`: блок `!H count` + `!ffB` (x, y, type) в encode/decode; проверка: round-trip с 0 и N событиями
- [x] 1.3 Поднять `PROTOCOL_VERSION` на +1 и обновить `SPEC.md` (формат bullets с flag, impacts, описание плазмы); проверка: `README`/SPEC согласованы
- [x] 1.4 Обновить сигнатуры `encode_world_state`(`impacts`) и их вызовы в `server/network.py`/`server/server.py`; проверка: `py_compile` всех изменённых модулей

## 2. Сервер: плазма-шар RIFLE

- [x] 2.1 В `server/game_data.py`: `WPN[RIFLE]` → `proj: True`, константы `PLAYER_PROJ_SPEED` (px/tick через `fps60_per_tick`), `PLAYER_PROJ_MAX_DIST`, `MAX_PLAYER_PROJ`; проверка: `py_compile`
- [x] 2.2 В `server/weapons.py:handle_shoot`: ветка для `wdef['proj']` — спавн шара в `world.bullets` с `from_player: True`, `vx/vy` по углу, `max_dist`, `dmg`; вычесть аммо/кулдаун как раньше; проверка: юнит-тест, что после выстрела RIFLE в `world.bullets` появился шар с `from_player`
- [x] 2.3 В `server/game_world.py:_update_bullets`: шагать и отвечать за столкновения шара игрока (враг через `_damage_enemy`/`kill_enemy`, игрок PvP через HP-минус) симметрично пулям врагов; лимит `MAX_PLAYER_PROJ`; проверка: симуляция — шар долетает до врага и убивает, в стену — гаснет, урона мгновенного нет
- [x] 2.4 В `get_bullet_states`/снапшот: нести `from_player` для новых шаров (по флагу в каждом bullet); проверка: снапшот для PvE содержит шар с флагом

## 3. Сервер: impact-события для следов

- [x] 3.1 В `server/game_world.py`: поле `self.impacts`, метод записи `impact(x, y, type)`, TTL-чистка + лимит (64) в шаге тика; проверка: симуляция — 2 выстрела в стену дают ≤ лимита событий с TTL
- [x] 3.2 В `server/weapons.py:handle_shoot` (хитскан-ветки): при попадании в стену/врага добавить impact-событие с координатами точки; проверка: юнит-тест — выстрел в стену даёт impact type=wall в точке луча
- [x] 3.3 Снапшот impact (`get_impact_states` + `server.py`), прокинуть в `encode_world_state`; проверка: `smoke_combat`-регрессия + ручной PvE-залп

## 4. Клиент: шар и следы

- [x] 4.1 `client/client.py` + `bridge.py`: принимать `bullets` с `from_player`, класть их в `gs.bul`/`gs.proj` с `from_player: True`; проверка: headless-рендер кадра с шаром (как `smoke_weapon_anim`) — кадр с шаром отличается от кадра без
- [x] 4.2 `bridge.py` + клиент: принимать `impacts`, для каждого вызвать локальные частицы (`spawn_sparks`/`spawn_smoke_trail`/`spawn_particles` по `type`); проверка: `draw_particles`/`gs.particles` непусто после impact; лимиты `MAX_PARTICLES` не превышены
- [x] 4.3 Рендер orb: проверить цвет/размер ветки `from_player` под цвет шара RIFLE (сейчас рендер общий — если нужно, ключ цвета из `WPN[RIFLE]['c']`); проверка: кадр `smoke_weapon_anim`-рендера PASS

## 5. Регрессия и валидация

- [x] 5.1 `py_compile` по всем изменённым файлам (`py -3.12`)
- [x] 5.2 `smoke_combat.py`, `smoke_integration_combat.py` PASS (протокол: новые поля, wire-формат)
- [x] 5.3 Новый/расширенный smoke: шар RIFLE виден второму клиенту (PvE), урон по долёту, impact-частицы; PASS
- [x] 5.4 `openspec validate plasma-rifle-projectile` — PASS; обновить ручной md `openspec/changes/2026-09-16-render-engine-port.md` (закрыть TODO «следы и проджектайлы»)