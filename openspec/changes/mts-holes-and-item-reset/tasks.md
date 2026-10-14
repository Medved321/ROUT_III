# Tasks

## 1. `.mts`: КОМНАТА с несколькими контурами

- [x] 1.1 `_Room` хранит список контуров (`contours`/`contour_lines`) и методы
      `add_corner`/`new_contour`; `_parse_document` обрабатывает строку `контур`
      (требует замкнутый предыдущий контур ≥3 углов) — `shared/text_map.py`.
- [x] 1.2 `_add_loop(points, room, si, ci, *, hole)` строит петлю по материалу
      (`cross<0` для внешней, `cross>0` для дыры), сохраняя логику общей грани
      (front/back, диагностика) — `shared/text_map.py`.
- [x] 1.3 `render` выводит ВСЕ петли сектора (внешняя + `контур`-дыры), в
      порядке `-abs(area)` и с канонизацией поворота; сектор из одной петли — без
      строк `контур` — `shared/text_map.py`.
- [x] 1.4 Проверить, что `py -3.12 -m py_compile shared/text_map.py` и
      `py -3.12 smoke_text_map.py` проходят (существующие сценарии не сломаны).

## 2. Тесты формата

- [x] 2.1 Добавить в `smoke_text_map.py` сценарий «комната с дырой»:
      вложенный `контур` + отдельная внутренняя `КОМНАТА`, проверить, что общая
      грань двусторонняя (`front` = внешний сектор), и что `parse(render(m))`
      сохраняет число двусторонних линий.
- [x] 2.2 Добавить сценарий «треугольник-дыра» и «сектор из одной петли не даёт
      строк `контур`».
- [x] 2.3 Проверить на реальных картах: `maps/sector1.mt1` (7 двусторонних) и
      `maps/sector2.mt1` (6 двусторонних) переживают `render`→`parse` (число и
      пары секторов совпадают); зафиксировать как сценарий или скрипт-проверку.

## 3. Предметы: убрать таймерный респавн

- [x] 3.1 `spawn_items_from_map` больше не пишет поле `respawn`;
      `_update_items` оставляет только цикл подбора — `server/game_world.py`.
- [x] 3.2 `_apply_item` не заводит таймер (и в ветке неизвестного типа);
      убрать `ITEM_RESPAWN_TICKS` из импорта и из `server/game_data.py`.
- [x] 3.3 `_reset_keys` → `_reset_items`: сбрасывает маски ключей и возвращает
      ВСЕ предметы (`a=True`); вызов в `_respawn_player`; обновить docstring
      `_reset_enemies` («предметы возвращает рестарт волны») — `server/game_world.py`.

## 4. Тесты предметов

- [x] 4.1 Переписать секцию респавна в `smoke_items.py`: взятый предмет не
      возвращается по времени; по смерти игрока возвращаются все предметы с тем
      же `id`; убийство врага ничего не возвращает; убрать чтение поля `respawn`.
- [x] 4.2 Обновить `smoke_items_mutation.py`: мутации «предмет не возвращается
      по смерти», «предмет возвращается по времени», «смерть не сбрасывает
      предметы» должны ронять тест.

## 5. Регресс и оформление

- [x] 5.1 `py -3.12 smoke_text_map.py`, `smoke_items.py`,
      `smoke_items_mutation.py`, `smoke_mt1_format.py`, `smoke_respawn.py`,
      `smoke_sector_server.py`, `smoke_sector_model.py`, `smoke_sector_render.py`.
- [x] 5.2 `py -3.12 tools/check_engine_imports.py`.
- [x] 5.3 `openspec validate mts-holes-and-item-reset --strict` (valid; INFO о
      несуществующих целевых спеках `map/text-format`/`map-items` — ожидаемая
      зависимость от `pickup-refusal-on-full`, `sector-steps-and-solid-walls`,
      `sector-doors-and-keys`).
- [x] 5.4 Пересохранить `maps/sector1.mts`, `maps/sector2.mts` новым `render`
      (текущие на диске потеряли дыры).
- [x] 5.5 Обновить `AGENTS.md` (секция «Предметы и подбор»: нет таймерного
      респавна, возврат по смерти; при необходимости — про `.mts`) и `SPEC.md`.
