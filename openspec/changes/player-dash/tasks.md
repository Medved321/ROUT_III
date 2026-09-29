# Tasks

## 1. Протокол

- [x] 1.1 `shared/protocol.py`: константа `INPUT_FLAG_DASH = 0x01`;
      `encode_player_input(move_x, move_y, angle, shoot, weapon_id=1,
      flags=0)` → `!bbfBBB` (9 байт); `decode_player_input` возвращает
      6-значный кортеж. Значение по умолчанию — 0, чтобы существующие
      вызовы оставались валидными.
      Проверка: кейд в `smoke_combat.py` — round-trip флагов, длина 9.
- [x] 1.2 `shared/version.py`: `PROTOCOL_VERSION` 6 → 7. Старые клиенты
      отвергаются существующим рукопожатием, дублировать механизм не нужно.
      Проверка: `smoke_reliability.py` (кейд несовместимого протокола) и
      `smoke_hitscan.py` (разбор 7).
- [x] 1.3 `SPEC.md`: новая раскладка `Player Input` с байтом `flags`, описание
      бита дэша и `state = 3=dashing` в `World State`.
- [x] 1.4 Обновить фикстуры длины пакета: `smoke_mt1_server.py:145`
      (8 → 9 байт), `smoke_combat.py:16-17`, `smoke_hitscan.py:59-62,360-361`
      (арность `decode_player_input`), `smoke_perf.py` (снапшот из 6 полей).
      Проверка: все тесты зелёные без изменения ожидаемых значений.

## 2. Сервер

- [x] 2.1 `server/game_data.py`: `DASH_SPEED_MULT = 2.5`, `DASH_TICKS = 5`,
      `DASH_COOLDOWN_TICKS = 30`, `MAX_MOVE_STEP_PX = MOVE_SPEED_PX *
      TICK_INTERVAL`.
- [x] 2.2 `shared/components.py`: компонент `Dash(ticks=0, cooldown=0)`.
      Проверка: `smoke_player_dash.py` 2a — дефолт нулевой.
- [x] 2.3 `server/game_world.py`: `Dash()` при `add_player`;
      `_update_dash()` уменьшает оба таймера на каждом тике, вызов из
      `update()` сразу после `_apply_movement()`, чтобы дэш давал ровно
      `DASH_TICKS` тиков ускорения.
- [x] 2.4 `server/network.py:299` и `server/server.py:246`:
      `_on_player_input`/`_apply_input` принимают `flags`; условие старта
      `(flags & INPUT_FLAG_DASH) and (move_x or move_y) and dash.cooldown <= 0`
      выставляет `ticks` и `cooldown` одним действием (идемпотентно к дублям);
      без ввода движения запрос игнорируется и кулдаун не тратится.
      Проверка: `smoke_player_dash.py` 2a/2b/3.
- [x] 2.5 `server/server.py:_apply_input`: `spd` домножается на
      `DASH_SPEED_MULT` при `dash.ticks > 0`; `pl.state` = 3 во время дэша.
      Присваивается **после** `weapons_handle_shoot()`, который сам ставит
      `state = 2` — иначе приоритет дэша терялся бы.
      Проверка: `smoke_player_dash.py` 4a/4b/7.
- [x] 2.6 `server/game_world.py:_apply_movement()`: шаг длиннее
      `MAX_MOVE_STEP_PX` делится на `ceil(|step| / MAX_MOVE_STEP_PX)` равных
      подшагов от **стартовой** координаты, перемещение останавливается на
      первом заблокированном; оси остаются независимыми. При
      `|step| <= MAX_MOVE_STEP_PX` — ровно одна проверка на ось, как раньше.
      Проверка: `smoke_player_dash.py` 5a/5b/5c/6.

## 3. Клиент

- [x] 3.1 `client/input_handler.py`: поля `dash` / `dash_pressed`; пробел
      даёт `dash_pressed` на `KEYDOWN` и `dash` при удержании; сброс в
      `update()` как у `shoot`.
- [x] 3.2 `client/client.py`: `_dash_pending`; `_input_snapshot` захватывает
      ребро и гасит **и** его, и защёлку, поэтому одно нажатие даёт ровно один
      пакет даже если между двумя отправками (25 Гц) успело пройти несколько
      кадров; удержание не повторяет. Позицию не предсказываем — дэш целиком
      серверный.
      Проверка: `smoke_player_dash.py` 8.
- [x] 3.3 `client/network_client.py`: `send_input(..., flags=0)` и
      `encode_player_input(..., flags)`.
- [x] 3.4 `client/performance.py:input_send_loop` разворачивает 6 полей.
- [x] 3.5 Управление отражено в подсказке логина входа
      (`client/client.py:_game_loop`): «WASD — движение, мышь — обзор, ЛКМ — огонь,
      Пробел — дэш».

## 4. Визуальная обратная связь

- [x] 4.1 `engine/rendering.py`, блок «other players»: при `state == 3`
      фигура рисуется светлее (хелпер `_brighter`). Размер записи игрока
      в снимке мира не меняется.
- [x] 4.2 `engine/rendering.py:2031`: убрать `pygame.key.get_pressed()[K_SPACE]`
      из признака «идёт огонь». Пробел теперь дэш, а не кнопка огня; огонь
      определяется `gs.fire_flag` (от сервера) и ЛКМ. Проверка: рендер с
      зажатым `K_SPACE` и `gs.fire_flag = 0` побайтово совпадает с рендером
      без нажатия (кейс 9 в `smoke_player_dash.py`).

## 5. Тесты и регресс

- [x] 5.1 Новый `smoke_player_dash.py` (8 кейдов: протокол, старт/таймеры,
      дубликат, требование движения, скорость/длительность, подшаг, счёт
      проверок, `state`, клиентский импульс).
      Проверка: тест падает при отключённом подшаге (`n = 1`) и при снятом
      приоритете `state`.
- [x] 5.2 `py -3.13 smoke_combat.py`, `smoke_hitscan.py`, `smoke_mt1_server.py`,
      `smoke_reliability.py`, `smoke_integration_combat.py`,
      `smoke_projectiles.py`. Проверка: зелёные; правлены только арность
      распаковки и длина пакета, ожидаемые значения не менялись.
      `smoke_projectiles.py` флейкует на живом UDP (известно) — зелёный
      со второго прогона.
- [x] 5.3 `py -3.13 smoke_sector_server.py` (проходимость и подшаг),
      `smoke_sector_render.py`, `smoke_engine_standalone.py`,
      `smoke_perf.py`, `smoke_ui.py`, `smoke_update_prompt.py`,
      `smoke_infighting.py`, `smoke_tiers.py`, `smoke_content_providers.py`,
      `smoke_weapon_anim.py`, `tools/check_engine_imports.py`.
      Проверка: зелёные.
- [x] 5.4 `openspec validate player-dash --strict`. Проверка: valid.
