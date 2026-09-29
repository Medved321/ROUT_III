# Tasks

## 1. Старение эффектов (предусловие, баг)

- [x] 1.1 `engine/rendering.py`: импортировать `update_particles`,
      `update_decals`, `update_weather` и вызвать все три раз за кадр перед
      блоком «# Particles».
- [x] 1.2 Проверка: 600 кадров с попаданием в каждом — `len(gs.particles)` не
      залипает на `MAX_PARTICLES`, мёртвые частицы исчезают, линия жизни убывает.
      До фикса этот тест падал: воспроизведено 200 частиц уже на 50-м кадре и
      0 новых эффектов после. После фикса: steady state 75-81, пик 86.

## 2. Эффекты дэша

- [x] 2.1 `engine/constants.py`: `DASH_FX_BURST`, `DASH_FX_TRAIL`,
      `DASH_FX_TRAIL_PX`, `DASH_FX_COLOR` (+ `DASH_FX_FADE`,
      `DASH_FX_STREAKS`, `DASH_FX_STREAKS_MIN` для полос).
- [x] 2.2 `engine/particles.py`: `spawn_dash_burst(x, y, col)` — кольцевой
      выброс; `spawn_dash_puff(x, y, col, ang)` — порция следа, летит против
      `ang`. Оба уважают `_effect_limit(MAX_PARTICLES)` и не смотрят на
      `gs.blood_amount`.
- [x] 2.3 `client/bridge.py`: память фронта по `id` игрока
      (`_dash_active` / `_dash_px` / `_dash_pos`), вспышка на фронте `0 -> 3`,
      порция следа на каждые `DASH_FX_TRAIL_PX` пройденного пути, направление
      из смещения позиции, сброс на спаде `3 -> 0`. Сентинел
      `LOCAL_DASH_ID = -1` для локального игрока, цвет — `DASH_FX_COLOR`.
- [x] 2.4 `client/bridge.py`: аргумент `update(..., local_state=0)`;
      `client/client.py` запоминает авторитетный `state` локального игрока из
      `latest` (плюс сброс в `_leave_to_lobby`) и передаёт его в каждый
      `bridge.update(...)`.
- [x] 2.5 Проверка (кейсы 2-8 `smoke_dash_fx.py`): вспышка ровно один раз за
      дэш; на месте пыли нет; `gs.blood_amount = 0` не гасит след; лимит
      соблюдён и при `effects_factor = 0.1`; при `state = 0` эффектов нет;
      след не зависит от FPS (1 крупный шаг = 20 мелких).

## 3. Экранные полосы

- [x] 3.1 `engine/game_state.py`: `dash_fx = 0.0`.
- [x] 3.2 `engine/rendering.py`: `draw_dash_streaks()` — одна модульная
      `SRCALPHA`-поверхность (создаётся лениво, переиспользуется), таблица из
      `DASH_FX_STREAKS` полос считается один раз при импорте с фиксированным
      seed, плотность к краям, количество по `gs.effects_factor` с минимумом
      `DASH_FX_STREAKS_MIN`, затухание `gs.dash_fx` в самом хелпере.
- [x] 3.3 Вызов между блоком «Distance fog» и блоком «HUD».
- [x] 3.4 `client/bridge.py`: на фронте дэша локального игрока
      `gs.dash_fx = 1.0`.
- [x] 3.5 Проверка (кейсы 9-10): при `dash_fx = 0` оверлей не рисуется и
      поверхность даже не создаётся; при `dash_fx = 1` кадр отличается; строки
      вне вьюпорта побайтово не тронуты (HUD не закрыт); огибающая гаснет за
      19 кадров; дэш чужого игрока полос не даёт; на `effects_factor = 0`
      полосы есть.

## 4. Документация

- [x] 4.1 `AGENTS.md`: в «Управление» добавлено «пробел не стреляет», новый
      раздел «Обратная связь дэша» (фронт, след по расстоянию, полосы между
      туманом и HUD).
- [x] 4.2 `SPEC.md`: в раскладке записи игрока отмечено, что `state=3`
      управляет клиентскими эффектами дэша, пакет не меняется.

## 5. Тесты и регресс

- [x] 5.1 Новый `smoke_dash_fx.py` (10 кейсов): старение пула, фронт вспышки,
      след по расстоянию, независимость от FPS, остановка следа вместе с дэшем,
      отклонённый дэш, независимость от `gs.blood_amount`, лимиты, цвет игрока,
      экранные полосы (границы вьюпорта, затухание), армирование полос только
      своим дэшем.
- [x] 5.2 Кейс 9 в `smoke_player_dash.py`: `pygame` 2.6 не умеет подменять
      состояние клавиш, поэтому ввод подменяется заглушками
      (`pygame.key.get_pressed` / `pygame.mouse.get_pressed`) с зажатым одним
      только `K_SPACE`; кадр обязан совпасть с кадром без нажатия, а
      `fire_timer = 20` и зажатая ЛКМ — отличаться.
      Проверка: тест падает при возврате `K_SPACE` в признак огня.
- [x] 5.3 `py -3.13 smoke_player_dash.py`, `smoke_dash_fx.py`,
      `smoke_projectiles.py`, `smoke_perf.py`, `smoke_weapon_anim.py`,
      `smoke_sector_render.py`, `smoke_engine_standalone.py`,
      `smoke_content_providers.py`, `smoke_combat.py`, `smoke_hitscan.py`,
      `smoke_mt1_server.py`, `smoke_reliability.py`,
      `smoke_integration_combat.py`, `smoke_sector_server.py`, `smoke_ui.py`,
      `smoke_update_prompt.py`, `smoke_infighting.py`, `smoke_tiers.py`,
      `tools/check_engine_imports.py`. Проверка: зелёные.
      `smoke_projectiles.py` флейкует на живом UDP (известно) — зелёный
      со второго прогона.
- [x] 5.4 `openspec validate dash-fx --strict` и `openspec validate
      player-dash --strict`. Проверка: valid.
