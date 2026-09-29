## Why

Рендер-ядро (`client/engine/`) уже почти не зависит от сети и протокола:
из shared оно импортирует только контрактные модели (`shared.sector_map`,
`shared.mt1`, `shared.map_data`), а из остального клиента — ничего; сервер
уже работает stdlib-only и никогда не импортирует движок. Формат `.mt1`
(`shared/mt1.py`, контейнер+кодеки) полностью автономен и по факту является
единственным каналом данных между сервером и рендером. При этом движок
физически лежит внутри пакета `client/`, и единственная причина для его
самостоятельного использования — собственно «быть движком» — не отражена
ни в структуре, ни в границах импорта. Цель — выделить рендер в
самостоятельный пакет `engine/` верхнего уровня с явной границей:
«движок не знает про клиент и сервер; его вход — данные карты (`.mt1` /
`GameMap` / `SectorMap`) и игровое состояние, выход — кадр».

## What Changes

- **Новый пакет `engine/` на верхнем уровне** вместо `client/engine/`:
  переезжают 15 модулей рендер-ядра (`constants`, `game_state`,
  `framebuffer`, `quality`, `wallcache`, `np_flats`, `textures`, `sprites`,
  `sprite_cache`, `particles`, `map_utils`, `sector_render`, `bsp`,
  `rendering`, `map_editor`). Внутренние импорты `client.engine.*`
  становятся `engine.*`.
- **Из `client/engine/` в `client/`** выносятся два клиентских адаптера:
  `bridge.py` (мост «сеть → `gs`», импортирует `shared.protocol.ENEMY_TYPES`)
  и `sector_editor.py` (инструмент, `shared.sector_map`). Движок не
  импортирует ни сетевых модулей, ни `shared.protocol`.
- **Явная граница контракта**: движку доступны только `shared.sector_map`,
  `shared.mt1`, `shared.map_data` (модели карт и формат). Всё остальное
  (`server.*`, `client.*` кроме самого engine, `shared.protocol`,
  `shared.reliability`, `shared.version`) — вне пакета.
- **Самодостаточные ассеты**: `rendering.py` сейчас грузит `skins/*.png`
  от текущей директории (`CWD`); движок получает базовый каталог ассетов
  (настраивается извне), а не читает CWD.
- **Сборка**: `tools/build_client.py` включает `engine/` в артефакт
  (zipapp) наравне с `client/` и `shared/`.

## Capabilities

### New Capabilities
- `engine/modularity`: движок — независимый пакет `engine/` верхнего уровня
  с явными границами импорта и публичным входом/выходом (карта + состояние
  → кадр); клиент примыкает к нему через адаптер (bridge), сервер не
  зависит от движка вообще.

### Modified Capabilities
- (архитектурная реорганизация; спецификации capability `renderer/*`,
  `map/sector-format` и `editor/*` не меняют семантику — переносится
  реализация, а не поведение)

## Impact

- `client/`: `client.py`, `settings.py`, `performance.py`, `editor_screen.py`,
  `sector_editor_screen.py` обновляют импорты `client.engine.*` → `engine.*`;
  `bridge.py` и `sector_editor.py` переносятся в `client/` из `client/engine/`;
  `client/engine/` удаляется.
- `tools/build_client.py`: `PACKAGES += "engine"`.
- Smoke-тесты (18 файлов) и `tools/perf_bench.py`: импорты `engine.*`.
- Новый проверочный скрипт: движок рендерит кадр без клиента/сети (только
  `.mt1` + `game_state`), плюс скан-тест запрещённых импортов (`server`,
  `client`, `shared.protocol`) внутри `engine/`.
- Сервер не изменяется (stdlib-only, импортирует только `shared.*`).