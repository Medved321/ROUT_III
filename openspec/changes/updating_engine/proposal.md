## Why

Пакет `engine/` объявлен самодостаточным рендер-ядром, но два его модуля —
`engine/sprites.py` (процедурное создание спрайтов: боты, враги, кадры
анимации) и `engine/map_editor.py` (модель данных и палитра тайлового
редактора) — к рендеру отношения не имеют: это инструменты производства
контента, которые живут в пакете рендера только по историческим причинам
порта. Из-за них движок тянет `shared.map_data`, а редакторский код
(`TileEditor`, палитра `TB_ITEMS`/`MAT_COLORS`, списки `ENEMY_NAMES`/
`ITEM_NAMES`) физически отделён от своего единственного потребителя —
`client/`. Клиент, который владеет и спрайтами, и редакторами, вынужден
импортировать их у рендера, а движок — зависеть от формы данных, которую
создаёт редактор. Движок должен получать готовые поверхности и таблицы
извне, а не создавать их сам.

## What Changes

- **`engine/sprites.py` → `client/sprite_factory.py`**: создание спрайтов
  (поверхности ботов, отрисовка типов врагов, кадры анимации, кеш кадров)
  переезжает из движка в клиент. Движок больше не содержит кода, рисующего
  спрайты «с нуля».
- **`engine/map_editor.py` → `client/map_editor.py`**: модель тайлового
  редактора (`TileEditor`), палитра (`TB_ITEMS`, `MAT_COLORS`), таблицы
  `CHAR_TO_TILE`/`TILE_TO_CHAR`/`TILE_CHAR_TO_TEX` и списки
  `ENEMY_NAMES`/`ITEM_NAMES` переезжают в клиент рядом с экранами
  редакторов.
- **Провайдеры через `game_state`**: движок не создаёт спрайты и не знает
  таблицу тайлов. Он берёт их из состояния:
  `gs.sprite_provider` (поверхность бота по параметрам кадра) и
  `gs.tile_provider` (символ тайла → имя текстуры). Клиент регистрирует
  реализации через `client/bridge.py` при старте. Значения по умолчанию
  (None) — рендер деградирует предсказуемо, а не падает.
- **Рендер перестаёт зависеть от редактора**: `engine/rendering.py` берёт
  имя текстуры стены из `gs.tile_provider`; `engine/sprite_cache.py`
  получает спрайты через тот же провайдер. У движка остаётся нулевая
  зависимость от `shared.map_data` — из трёх разрешённых контрактных
  модулей (`shared.sector_map`, `shared.mt1`, `shared.map_data`) он больше
  не использует ни одного.
- **Границы импортов ужесточаются**: `tools/check_engine_imports.py`
  запрещает `shared.map_data` в `engine/` и требует, чтобы модули
  `sprites.py`/`map_editor.py` в движке отсутствовали. `client/` при этом
  может импортировать `engine.*` как раньше.
- **Мёртвый дубликат `client/engine/` не трогаем** (в нём лежат старые
  копии модулей): его удаление — отдельная задача вне этого change.

## Capabilities

### New Capabilities

- `editor/tile-editor`: модель данных и палитра тайлового редактора живут в
  клиенте, а не в движке; движок получает отображение «тайл → текстура»
  снаружи и не зависит от `GameMap`/редакторских таблиц.

### Modified Capabilities

- `engine/modularity`: уточняется состав пакета `engine/` — из него
  исключаются `sprites` и `map_editor`, а их данные приходят через
  слоты `game_state`; ужесточается список разрешённых импортов.

## Impact

- **Новые модули**: `client/sprite_factory.py` (из `engine/sprites.py`),
  `client/map_editor.py` (из `engine/map_editor.py`).
- **Удаляются из движка**: `engine/sprites.py`, `engine/map_editor.py`.
- **Движок**: `game_state.py` (слоты `sprite_provider`/`tile_provider`),
  `rendering.py` (импорт `TILE_CHAR_TO_TEX` → `gs.tile_provider`, вызов
  `get_bot_surface` → `gs.sprite_provider`), возможно `sprite_cache.py`
  (единая точка получения спрайта). `engine/__init__.py`, если он
  реэкспортирует перенесённое.
- **Клиент**: `bridge.py` (регистрация провайдеров, комментарий про
  `TILE_CHAR_TO_TEX`), `editor_screen.py`, `sector_editor_screen.py` —
  импорты `engine.map_editor` → `client.map_editor`.
- **Инструменты**: `tools/check_engine_imports.py` — запрет
  `shared.map_data` и проверка отсутствия перенесённых модулей.
- **Тесты**: `smoke_text_map.py` (`from engine.map_editor import ...`),
  `smoke_engine_standalone.py` (проверка автономности должна остаться
  зелёной без провайдеров), `smoke_sprites.py`/`smoke_ui.py`/`smoke_perf.py`
  — правка путей импорта. Новый smoke на согласованность провайдеров.
- **Сервер, протокол, формат карт (`.mt1`, `.json`, `.mts`) не меняются.**
- **Сборка**: `tools/build_client.py` уже включает и `client/`, и
  `engine/` — правок не требуется.
