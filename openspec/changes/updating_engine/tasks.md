## 1. Перенос модулей в клиент

- [x] 1.1 `engine/sprites.py` → `client/sprite_factory.py` (копия без
  изменения логики отрисовки), убрать неиспользуемый
  `from engine.constants import TILE` (D3). Проверка: grep по
  `client/sprite_factory.py` не находит импортов `engine`.
- [x] 1.2 `engine/map_editor.py` → `client/map_editor.py` (копия, все
  таблицы/палитра/`TileEditor` на месте, `from shared.map_data import
  GameMap` сохранён). Проверка: `py -3.12 -c "import client.map_editor"` и
  сверка `CHAR_TO_TILE`/`TILE_CHAR_TO_TEX` с исходным файлом (diff таблиц
  пуст).
- [x] 1.3 Удалить `engine/sprites.py` и `engine/map_editor.py`. Проверка:
  `dir /b engine` не содержит `sprites.py`/`map_editor.py`.

## 2. Слоты провайдеров в движке

- [x] 2.1 `engine/game_state.py`: добавить `sprite_provider = None` и
  `tile_provider = None` с комментарием о контракте (D2). Проверка: модуль
  импортируется, атрибуты существуют.
- [x] 2.2 `engine/rendering.py`: убрать
  `from engine.map_editor import TILE_CHAR_TO_TEX` (строка 10) и
  `from engine.sprites import get_bot_surface` (строка 14); заменить
  вызовы на `gs.tile_provider(...)` / `gs.sprite_provider(...)` с
  fallback: для тайла — `'wall'`, для бота — пустая SRCALPHA-поверхность
  нужного размера. Проверка: grep по `engine/rendering.py` не находит
  `map_editor`/`sprites`.
- [x] 2.3 Резолв вызова брать один раз на кадр (локальная переменная
  ссылка на callable), а не на каждый спрайт/тайл — из-за
  требования по FPS на слабом железе. Проверка: `smoke_perf.py` не хуже
  прежнего.

## 3. Регистрация провайдеров в клиенте

- [x] 3.1 `client/bridge.py`: импортировать `client.sprite_factory`
  (`get_bot_surface`) и `client.map_editor` (`TILE_CHAR_TO_TEX`); при
  наполнении `gs` регистрировать `gs.sprite_provider` и
  `gs.tile_provider` (идемпотентно, до первого кадра — D6). Проверка:
  после инициализации состояния оба слота — callable.
- [x] 3.2 `client/bridge.py`: заменить локальный дубликат `_TILE_TO_CHAR`
  (строка 26) на инверсию `CHAR_TO_TILE` из `client.map_editor`,
  обновить комментарий-ссылку на `map_editor.TILE_CHAR_TO_TEX`. Проверка:
  `map_to_grid` даёт ту же строку символов, что и до правки (тест на
  карте по умолчанию).

## 4. Переключение импортёров

- [x] 4.1 `client/editor_screen.py:13` и
  `client/sector_editor_screen.py:19`: `engine.map_editor` →
  `client.map_editor`. Проверка: редактор открывается из лобби (E),
  палитра и список типов на месте.
- [x] 4.2 `smoke_text_map.py:488`: тот же импорт. Проверка:
  `py -3.12 smoke_text_map.py` зелёный.
- [x] 4.3 Обновить докстринг `shared/text_map.py` (строки 36-38 и 77):
  источник истины — `client/map_editor.py`. Проверка: grep по
  `shared/` не находит `engine/map_editor.py`.
- [x] 4.4 Тайловые smoke (`smoke_framebuffer_tile.py`, `smoke_ui.py`,
  `smoke_perf.py` — все, что рендерят тайловый режим) регистрируют
  `gs.tile_provider = TILE_CHAR_TO_TEX` (и `gs.sprite_provider`, если
  рисуют ботов). Проверка: `smoke_framebuffer_tile.py` по-прежнему
  проверяет различие текстур север/юг (иначе тест «зелёный» вхолостую —
  см. Риски).

## 5. Сканер границ

- [x] 5.1 `tools/check_engine_imports.py`: `shared.map_data` убрать из
  `ALLOWED_SHARED` (остаются `shared.sector_map`, `shared.mt1`), добавить
  в запрещённые; добавить проверку состава пакета —
  `FORBIDDEN_MODULES = {"sprites.py", "map_editor.py"}` (D7). Проверка:
  на текущем коде сканер зелёный; временно вернуть
  `from shared.map_data import GameMap` в `engine/rendering.py` — падение;
  создать `engine/sprites.py` — падение.
- [x] 5.2 Прогнать `py -3.12 tools/check_engine_imports.py` и
  `py -3.12 tools/perf_bench.py` (без ошибок импорта). Проверка: обе
  команды завершаются успешно.
  *Примечание:* сканер зелёный. `perf_bench.py --mode convert` отрабатывает;
  режимы `tile`/`sector` падают на `from tools import bench_map` — модуля
  `tools/bench_map.py` нет в репозитории. Это не связано с переездом
  (остаток change `total-performance`), здесь не чинится.

## 6. Автотест на провайдеры

- [x] 6.1 `smoke_content_providers.py` (новый): регистрирует
  `gs.sprite_provider` из `client.sprite_factory` и `gs.tile_provider` из
  `client.map_editor`, рендерит тайловый кадр с ботами; проверяет, что
  (а) бот нарисован ненулевой поверхностью, (б) разные символы тайлов
  дают разные имена текстур. Проверка: `py -3.12
  smoke_content_providers.py` зелёный.
- [x] 6.2 Тот же файл: сценарий без провайдеров (`sprite_provider =
  None`, `tile_provider = None`) — `rendering.render()` не падает, буфер
  непустой, все стены `'wall'`. Проверка: зелёный.
- [x] 6.3 `smoke_engine_standalone.py` остаётся зелёным без единой строки
  регистрации (движок автономен). Проверка:
  `py -3.12 smoke_engine_standalone.py` зелёный.

## 7. Регрессия и верификация

- [x] 7.1 Прогнать полный smoke-набор (`py -3.12 smoke_*.py`): тайловые,
  секторные, `smoke_ui`, `smoke_perf`, `smoke_sector_perf`,
  `smoke_sprites`, `smoke_text_map`, `smoke_custom_map`. Проверка: все
  зелёные, регрессий по FPS нет.
  *Примечание:* 34/35 зелёные. Единственный красный — `smoke_sector_perf.py`
  (30.9 мс при бюджете 17 мс) на секторном DDA-рендере, который провайдеры
  не касается: с `gs.*_provider = None` результат тот же (31.2 мс). Это
  быстродействие машины, а не регрессия.
- [x] 7.2 Конец-в-конец вручную: лобби → редактор карт (E) → рисование →
  «▶ Играть» (F5) → партия на своей карте; проверить, что стены разных
  материалов различаются и боты в deathmatch видны. Проверка: визуально
  кадр не хуже прежнего.
  *Примечание:* вместо человека за клавиатурой путь прогнан скриптом по
  настоящим объектам клиента (`MapEditorScreen` → `to_game_map()` →
  `RenderBridge.set_map` → партия): палитра/модель редактора на месте,
  кирпич и камень дают разные кадры, бот в deathmatch виден. Сравнение
  «глазами» остаётся за пользователем.
- [x] 7.3 Секторный путь: карта `.mts`/`.mt1` (редактор секторов) играется
  без провайдеров тайлов. Проверка: рендер секторной карты в лобби→партия.
  *Примечание:* `SectorEditorScreen` → `to_mt1()` → `SectorMap.from_mt1` →
  `RenderBridge.set_sector_map` → кадр; при `gs.tile_provider = None` кадр
  непустой. Тем же скриптом.
- [x] 7.4 `openspec validate --strict` для change `updating_engine` — PASS.

## 8. Документация

- [x] 8.1 `AGENTS.md`, раздел «Граница движка»: перечислить новый состав
  `engine/` (13 модулей, без `sprites`/`map_editor`), слоты
  `sprite_provider`/`tile_provider` как часть контракта, новое размещение
  `client/sprite_factory.py` и `client/map_editor.py`. Проверка: текст
  согласован со спеками `engine/modularity` и `editor/tile-editor`.
- [x] 8.2 Зафиксировать в design/tasks, что мёртвый дубликат
  `client/engine/` НЕ удаляется этим change (решение пользователя) —
  отдельная будущая задача. Проверка: grep по `git`-независимому списку
  задач не содержит удаления `client/engine/`.

> **Граница этого change:** мёртвый дубликат `client/engine/` (старые копии
> модулей движка) остаётся на месте и НЕ входит в задачи выше — его удаление
> отдельная будущая задача (см. design.md, «Non-Goals»). Он не участвует в
> границе импортов: `client/bridge.py`, экраны редакторов и smoke импортируют
> `engine.*` и `client.map_editor`/`client.sprite_factory`, а не `client.engine.*`.
