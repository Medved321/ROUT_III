# Tasks

## 1. Секторная миникарта

- [x] 1.1 Добавить `engine/sector_minimap.py`: сбор отрезков из активной секторной
      сцены, кэш по `(scene, размер окна)`, отрисовка геометрии без сущностей и
      fog-of-war. Проверить: `python tools/check_engine_imports.py` проходит, новый
      модуль в разрешённом списке движка.
- [x] 1.2 Подключить миникарту в `engine/rendering.py` из ветки `show_minimap`
      только при активной секторной сцене; тайловый путь оставить нетронутым.
      Проверить: `python smoke_sector_minimap.py` показывает геометрию, не показывает
      маркеры игрока/врагов и не падает без сцены.
- [x] 1.3 Добавить `smoke_sector_minimap.py`: геометрия и отсутствие сущностей,
      переключатель, размеры 640×480/1000×700/3840×2160, отсутствие аллокаций и
      пересчёта на устоявшемся кадре, мутация линии меняет рисунок.
      Проверить: тест проходит и падает на мутациях «рисуем сущности» и «нет кэша».
- [x] 1.4 Обновить `AGENTS.md`: список модулей `engine/` и правило миникарты.
      Проверить: в документе описаны оба режима и отказ от сущностей в секторном.

## 2. Удаление тайлового редактора

- [x] 2.1 Оставить в лобби единственный вход в редактор (кнопка и клавиша `E`),
      закрыть действие `editor` только на секторный редактор, удалить `_editor_kind`
      и ветки `tile`/`sector` в `client/client.py`. Проверить: `python smoke_ui.py` и
      `python smoke_menu_layout.py` проходят, в лобби один вход в редактор и все
      хитбоксы совпадают с метками.
- [x] 2.2 Удалить `client/editor_screen.py`, импорт/инстанс `MapEditorScreen`,
      тайловую ветку `play`; из `client/map_editor.py` убрать `TileEditor` и её
      палитру, сохранив `CHAR_TO_TILE`, `TILE_CHAR_TO_TEX`, `ENEMY_NAMES`,
      `ITEM_NAMES`. Проверить: `python -m py_compile` проходит, grep не находит
      `MapEditorScreen`/`TileEditor` за пределами архива, smoke-тесты импортируют
      константы как раньше.
- [x] 2.3 Убрать кнопку «Тайлы» и действие `tile` из `client/sector_editor_screen.py`.
      Проверить: `python smoke_sector_editor_screen.py` проходит, панель инструментов
      не содержит перехода в тайловый режим.
- [x] 2.4 Обновить `smoke_custom_map.py` под единственный редакторский путь.
      Проверить: тест проходит и по F5 создаётся партия с секторной картой.
- [x] 2.5 Обновить дельту `client/display-resolution` и тексты `AGENTS.md`/`README.md`
      про состав редакторов. Проверить: `openspec validate sector-minimap-editor-fps-cleanup --strict`
      проходит.

## 3. Подписи без декоративных глифов

- [x] 3.1 Заменить глифы в пользовательских строках лобби, настроек, паузы и
      редактора секторов на слова: `▶`, `✓`, `▾`, `▲`, `▼`, стрелки, `⇄`.
      Проверить: `python smoke_ui.py`, `python smoke_menu_layout.py` и
      `python smoke_sector_editor_screen.py` проходят, подписи помещаются в свои полосы.
- [x] 3.2 Убрать `▌`/`█` из текстового курсора и служебного оверлея в
      `engine/rendering.py` и редакторе. Проверить: `python smoke_viewport.py` и
      `python smoke_framebuffer_tile.py` проходят, видимый текст не содержит блоков.
- [x] 3.3 Добавить проверку запрещённых кодовых точек в `smoke_ui.py`: сканирование
      строковых литералов UI-модулей с whitelist кириллицы и пунктуации.
      Проверить: тест проходит на дереве и падает при точечной мутации с `✓`.
- [x] 3.4 Обновить `AGENTS.md`/`README.md`, чтобы подсказки в документации тоже
      использовали слова вместо стрелок. Проверить: grep не находит запрещённых
      глифов в пользовательских строках документации.

## 4. Настраиваемый FPS сервера

- [x] 4.1 Ввести в `shared/combat.py` `DEFAULT_TICK_RATE`, диапазон 5–120,
      `validate_tick_rate`, `tick_interval`, `fps60_per_tick` и параметр
      `tick_rate` в `wpn_cooldown_ticks`; `server/game_data.py` реэкспортирует
      старые имена как дефолты. Проверить: `python smoke_server_fps.py` проверяет
      границы и одинаковый кулдаун в секундах на 20/40 тиках.
- [x] 4.2 Добавить `server/cli.py` с единственным парсером `--fps/--host/--port`
      и использовать его в `run_server.py` и `server/__main__.py`.
      Проверить: `python smoke_server_fps.py` проверяет оба entry point, формат
      `--fps 30`, отказ при `--fps 3/abc` до открытия сокета.
- [x] 4.3 Перевести `GameServer` на `self.tick_rate`/`self.tick_interval`,
      передать частоту в `Lobby`, `GameWorld`, `handle_shoot`; сделать скорости,
      длительности и ms→ticks функциями от частоты. Проверить: `python smoke_fire_intent.py`
      на 20 и 30 тиках держит частоту, ноль отказов по cooldown и те же реальные
      интервалы; `python smoke_items.py` и `python smoke_combat.py` проходят.
- [x] 4.4 Дописать `tick_rate` в конец `HELLO_ACK`, поднять `PROTOCOL_VERSION` до 16.
      Проверить: `python smoke_server_fps.py` и `python smoke_reliability.py`
      проходят round-trip, старый клиент отвергается по версии, поля обновления
      не сдвинуты.

## 5. Синхронизация частоты на клиенте

- [x] 5.1 Сохранить частоту в `NetworkClient.hello_ack`, передать её в
      `GameClient` и использовать для врат стрельбы, предсказанной отдачи,
      `INTERP_DELAY` и перевода тика матча в секунды. Проверить: `python smoke_fire_intent.py`
      и `python smoke_weapon_anim.py` проходят на 20/30/40 тиках без обрезанной отдачи.
- [x] 5.2 Убрать литерал `20` и импорт фиксированного `TICK_INTERVAL` из
      клиентских путей расчёта времени и стрельбы. Проверить: grep не находит
      `// 20` и прямое использование `shared.combat.TICK_INTERVAL` в `client/`.
- [x] 5.3 Добавить сквозной `smoke_server_fps.py`: запуск с `--fps`, получение
      частоты клиентом, время матча в HUD, работа цикла на искусственных часах
      без карусели `sleep(0)`. Проверить: тест проходит и падает на мутации
      «клиент игнорирует объявленную частоту».

## 6. Интеграция и документация

- [x] 6.1 Обновить `SPEC.md` (таблица пакетов, формат `HELLO_ACK`, версию),
      `AGENTS.md` и `openspec/specs/001-doom-shooter.md`: тик по умолчанию 20,
      настраивается флагом, один редактор. Проверить: в документах нет устаревшего
      «тик 20 Гц» как единственного варианта и «редакторы карт».
- [x] 6.2 Прогнать полный набор: `python smoke_viewport.py`, `python smoke_menu_layout.py`,
      `python smoke_sector_editor_screen.py`, `python smoke_ui.py`, `python smoke_config.py`,
      `python smoke_framebuffer_tile.py`, `python smoke_platform_render.py`,
      `python smoke_items.py`, `python smoke_fire_intent.py`, `python smoke_reliability.py`,
      `python smoke_server_fps.py`, `python tools/check_engine_imports.py`,
      `python -m py_compile` изменённых файлов. Проверить: всё проходит без ошибок.
- [x] 6.3 Прогнать `openspec validate sector-minimap-editor-fps-cleanup --strict`.
      Проверить: валидатор сообщает об успехе.

## Workflow follow-up

- Архивировать change после ревью пользователя и синхронизировать дельты с `openspec/specs/`.
