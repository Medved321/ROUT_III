## 1. Выделение пакета `engine/`

- [x] 1.1 Создать каталог `engine/` на верхнем уровне; перенести из
  `client/engine/` модули: `constants.py`, `game_state.py`,
  `framebuffer.py`, `quality.py`, `wallcache.py`, `np_flats.py`,
  `textures.py`, `sprites.py`, `sprite_cache.py`, `particles.py`,
  `map_utils.py`, `sector_render.py`, `bsp.py`, `rendering.py`,
  `map_editor.py`. Проверка: `import engine.rendering` работает без пакета
  `client`.
- [x] 1.2 Заменить все внутренние импорты `client.engine.*` → `engine.*`
  (внутри перенесённых модулей: `rendering`, `sector_render`, `np_flats`,
  `sprites`, `textures`, `wallcache`, `particles`, `map_utils`,
  `sprite_cache` и др.). Проверка: внутри `engine/` нет строк
  `client.engine` (grep по каталогу).
- [x] 1.3 Перенести `bridge.py` → `client/bridge.py` и `sector_editor.py` →
  `client/sector_editor.py`; их импорты движка перевести на `engine.*`
  (`from engine import ...`). Проверка: `client/bridge.py` импортирует
  `engine.*` и `shared.protocol`, а `engine/` не содержит этих файлов.

## 2. Обновление импортёров движка

- [x] 2.1 Заменить импорты `client.engine.*` → `engine.*` в
  `client/client.py`, `client/settings.py`, `client/performance.py`,
  `client/editor_screen.py`, `client/sector_editor_screen.py`. Проверка:
  клиент запускается до главного меню (`smoke_launch.py`).
- [x] 2.2 Заменить импорты `client.engine.*` → `engine.*` в smoke-тестах:
  `smoke_framebuffer_tile.py`, `smoke_mt1_format.py`,
  `smoke_framebuffer.py`, `smoke_np_flats.py`, `smoke_sector_editor.py`,
  `smoke_projectiles.py`, `smoke_config.py`, `smoke_sector_render.py`,
  `smoke_sector_flats.py`, `smoke_perf.py`, `smoke_sector_perf.py`,
  `smoke_ui.py`, `smoke_sprites.py`, `smoke_tiers.py`,
  `smoke_wallcache.py`, `smoke_weapon_anim.py`, `smoke_integration_combat.py`.
  Проверка: каждый smoke-файл запускается без ошибок импорта.
- [x] 2.3 Заменить импорты в `tools/perf_bench.py`. Проверка: бенч
  запускается.
- [x] 2.4 Удалить `client/engine/` (совсем). Проверка: во всём репозитории
  нет упоминаний `client.engine` (grep).

## 3. Ассеты и сборка

- [x] 3.1 D5: движок резолвит `skins/*.png` от базового каталога ассетов
  (`engine.game_state.assets_dir`, по умолчанию — каталог проекта),
  заменить прямые пути `'skins/...'` на резолв от него. Проверка:
  перемещение процесса/импортера в другой CWD не ломает загрузку текстур
  оружия.
- [x] 3.2 D7: `tools/build_client.py` — `PACKAGES = ("client", "shared",
  "engine")`. Проверка: `py -3.12 tools/build_client.py --version 1.1.0`
  собирает архив, содержащий `engine/`.

## 4. Границы и автономность движка

- [x] 4.1 Скрипт-сканер границ `tools/check_engine_imports.py` (AST всех
  `engine/*.py`): разрешены stdlib/pygame/numpy + `shared.sector_map`,
  `shared.mt1`, `shared.map_data`; запрещены `client.*`, `server.*`,
  `shared.protocol`/`reliability`/`version`. Проверка: на текущем коде
  отдаёт clean; добавление запрещённого импорта — падение.
- [x] 4.2 `smoke_engine_standalone.py`: без пакета `client` загрузить
  тестовую секторную карту (`.mt1`/`SectorMap`) и отрендерить кадр через
  `engine.rendering`; проверить непустой `gs.frame` нужного размера.
  Проверка: скрипт проходит на `py -3.12` при отсутствии `client`-импортов.

## 5. Регрессия и верификация

- [x] 5.1 Прогнать smoke-набор: `smoke_launch.py`, `smoke_custom_map.py`,
  тайловые и секторные сценарии, `smoke_sector_render.py`,
  `smoke_sector_flats.py`, `smoke_mt1_format.py`. Проверка: все зелёные.
- [x] 5.2 Конец-в-конец: собрать артефакт
  `py -3.12 tools/build_client.py --version 1.1.0 --set-latest`, запустить
  клиент из артефакта в лобби→партию→игру (тайловая и секторная карта).
  Проверка: рендер работает, `latest.txt`/`shared/version.py` синхронны.
- [x] 5.3 `openspec validate --strict` с change `engine-standalone` — PASS.

## 6. Документация

- [x] 6.1 Обновить `AGENTS.md`: структура проекта (пакет `engine/`,
  граница «движок ↔ сервер = .mt1»), упоминание `tools/check_engine_imports.py`.
  Проверка: текст согласован со спеками `engine/modularity`.