## Context

Сейчас рендер-ядро лежит в `client/engine/` (18 модулей). Аудит импортов
показывает, что только два модуля тянут клиентские/протокольные
зависимости:

- `bridge.py` → `client.engine.*` + `shared.protocol.ENEMY_TYPES`,
  `shared.sector_map` — это адаптер «сеть → `gs`», а не сам рендер;
- `sector_editor.py` → `shared.sector_map` — инструмент редактора.

Остальные `client/engine/*` импортируют только stdlib, `pygame`, `numpy` и
внутренние `client.engine.*`. Из shared рендер пользуется контрактными
моделями (`shared.sector_map` через `bsp.py`, `shared.mt1` через
`sector_render.py`, `shared.map_data` через `map_editor.py`). Формат `.mt1`
(`shared/mt1.py`) автономен (struct+zlib, ноль чужих импортов) и по факту
уже является единственным каналом «сервер → рендер» для секторных карт.
Сервер stdlib-only никогда не импортирует движок.

Перенос: `client/engine/` → `engine/` верхнего уровня; `bridge.py` и
`sector_editor.py` выносятся в `client/`. Внутренние импорты
`client.engine.*` → `engine.*`. Внешние импортёры (`client.py`,
`settings.py`, `performance.py`, `editor_screen.py`,
`sector_editor_screen.py`, 18 + smoke-тестов, `tools/perf_bench.py`,
`tools/build_client.py`) обновляются.

## Goals / Non-Goals

**Goals:**
- Движок — самостоятельный пакет `engine/` верхнего уровня, импортируемый
  без клиента и сервера.
- Явная, проверяемая граница импортов (`shared.sector_map`, `shared.mt1`,
  `shared.map_data` разрешены; `client.*`, `server.*`, `shared.protocol`,
  `shared.reliability`, `shared.version` запрещены).
- Клиент подключается к движку только через bridge-адаптер; единственная
  связь с сервером — контракт `.mt1` (+`SectorMap`/`GameMap`).
- Сборка zipapp включает `engine/`.
- Полная регрессия: лобби→партия→игра, тайловые и секторные карты,
  редакторы — работают как раньше.

**Non-Goals:**
- Изменение семантики рендера, протокола, ECS или игровых механик.
- Выделение из движка отдельных «подпакетов» (визуальные эффекты,
  спрайты и т.п.) — всё рендер-ядро остаётся одним пакетом.
- Менять структуру `shared/` (модели карт остаются контрактом в `shared/`).

## Decisions

### D1. Целевой layout: `engine/` на верхнем уровне
```
ROUT III/
├── engine/   # независимый движок (15 модулей, см. spec)
├── client/   # приложение: сеть, лобби, HUD-обёртка, bridge, редакторы
├── server/   # авторитетный сервер (stdlib-only, движок не трогает)
├── shared/   # контракт: mt1, sector_map, map_data, протокол и пр.
```
Пакет/директория `client/engine/` удаляется после миграции.

### D2. Что переезжает, что остаётся
В `engine/`: `constants`, `game_state`, `framebuffer`, `quality`,
`wallcache`, `np_flats`, `textures`, `sprites`, `sprite_cache`,
`particles`, `map_utils`, `sector_render`, `bsp`, `rendering`,
`map_editor`.
В `client/`: `bridge.py` (адаптер сети), `sector_editor.py` (инструмент),
а также экраны/настройки игры. `shared.map_data.GameMap` остаётся
контрактом, а `TILE_CHAR_TO_TEX`/`map_editor.py` едут с рендером (их уже
импортирует `rendering.py:10`).

### D3. Границы импорта
Разрешено в `engine/`: stdlib, `pygame`, `numpy`, и только
`shared.sector_map`, `shared.mt1`, `shared.map_data`.
Запрещено в `engine/`: `client.*`, `server.*`, `shared.protocol`,
`shared.reliability`, `shared.version`.
Проверка — скрипт-сканер `tools/check_engine_imports.py`, который читает
AST всех `engine/*.py` и падает при нарушении; интегрируется в smoke-тест.
Это формализует требование «движок независим, связь только через `.mt1`».

### D4. Адаптер bridge остаётся в клиенте
`client/bridge.py` импортирует `engine.*` и `shared.protocol.ENEMY_TYPES`,
наполняет `engine.game_state` (игроки, враги, оружие, частицы). Движок
никогда не знает о `shared.protocol`. Ссылки в `client.py`,
`smoke_*`, `tools/perf_bench.py` на `client.engine.bridge` → `client.bridge`.

### D5. Ассеты — через базовый каталог, не через CWD
`rendering.py` грузит `skins/*.png` путями относительно CWD. Движок
получает базовый каталог ассетов: `engine.game_state.assets_dir` (или
константа, переопределяемая извне); пути `'skins/xxx.png'` резолвятся от
него. Это нужно для запуска из zipapp (артефакт распаковывает ассеты в
известный каталог) и для офлайн-рендера теста. Редакторских путей это не
касается.

### D6. Миграция массы импортов
`client.engine.*` → `engine.*`:
- внутренние импорты внутри самого движка (все `client.engine` → `engine`);
- `client/client.py`, `client/settings.py`, `client/performance.py`,
  `client/editor_screen.py`, `client/sector_editor_screen.py`;
- 18 smoke-файлов + `tools/perf_bench.py`.
Механический рефакторинг; порядок: сначала движок, затем импортёры, затем
удаление `client/engine/`.

### D7. build_client включает `engine/`
`PACKAGES = ("client", "shared")` → `("client", "shared", "engine")`.
В zipapp-артефакт идёт и `engine/`, чтобы рантайм из архива рендерил.
numpy-бутстрап не меняется.

### D8. Офлайн-проверка движка
Новый `smoke_engine_standalone.py`: без пакета `client` грузит тестовую
`.mt1`/SectorMap, кладёт состояние в `engine.game_state`, вызывает
`engine.rendering` (sector или tile путь), проверяет непустой `gs.frame`
ожидаемого размера. Доказывает «движок можно рендерить без клиента и
сервера».

## Risks / Trade-offs

- **Большой механический диф импортов** → поэтапная миграция
  (движок → импортёры → удаление старого пакета); smoke-тесты — критерий
  готовности каждого этапа.
- **Риск случайного импорта `client.*` из движка после правок** →
  скрипт-сканер `check_engine_imports.py` (AST) закрепляет границу в CI.
- **Пути ассетов в zipapp** → D5: конфигурируемый `assets_dir`, а не CWD;
  артефакт уже распаковывает vendor — каталог ассетов резолвится рядом.
- **`map_editor` в движке тянет `shared.map_data`** → это разрешённый
  контракт; `TileEditor` остаётся моделью редактора, экраны редактора
  остаются в клиенте.
- **Низкий риск** (рефакторинг без изменения поведения): падение тестов —
  это только необновлённые импорты, откат тривиален (вернуть пакет).

## Migration Plan

1. Создать `engine/` (верхний уровень), перенести 15 модулей из
   `client/engine/`, заменить внутренние `client.engine.*` → `engine.*`.
2. Вынести `bridge.py` → `client/bridge.py`, `sector_editor.py` →
   `client/sector_editor.py`; обновить их импорты движка.
3. Обновить импортёры движка в клиенте (`client.py`, `settings.py`,
   `performance.py`, `editor_screen.py`, `sector_editor_screen.py`)
   и в smoke/perf (`18 smoke_*`, `tools/perf_bench.py`).
4. D5: ассеты через `assets_dir` (не CWD), D7: `build_client.py` + engine.
5. D8: `smoke_engine_standalone.py` + `tools/check_engine_imports.py`
   (сканер границ).
6. Удалить `client/engine/`; прогнать всю регрессию (lobby→party→game,
   тайлы, сектора, редакторы, артефакт zipapp); `openspec validate --strict`.

## Open Questions

- (нет — scope согласован с пользователем: состав движка зафиксирован,
  `bridge` и `sector_editor` остаются в клиенте, ассеты через базовый каталог)