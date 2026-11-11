# Tasks

## 1. Режим «Телефон» в настройках

- [x] 1.1 Добавить `CONTROL_MODE_TOUCH = 2`, «Телефон» в `CONTROL_MODES` и новые ключи `touch_stick_size` / `touch_button_size` / `touch_opacity` / `touch_look_speed` в `DEFAULTS` и `SPEC` (`client/settings.py`); проверить, что `Settings()` отдаёт дефолты и `save()`/`load()` их сохраняет.
- [x] 1.2 Починить разбор в `control_mode()`: строка «телефон» возвращает `2`, а не «Мышь», и не ломает прежние «Мышь»/«Трактор»/`0`/`1`; проверить `smoke_resolution_controls.py` (расширить таблицей из четырёх значений) — на старом коде «телефон» падает в «Мышь».
- [x] 1.3 Добавить `is_touch()` рядом с `is_tractor()` и ветку «Телефон» в `control_hints()` («Стики — движение/обзор | Огонь | Дэш | Использовать»); проверить, что лобби и лог старта партии показывают подсказки тача (`smoke_resolution_controls.py`).
- [x] 1.4 `format_value()` для новых ключей (размеры и прозрачность в процентах, скорость в рад/с) и `apply_runtime()`, который прокидывает их в `InputHandler`/`TouchLayout` без перезапуска; проверить, что после смены значения в настройках меняется то, что читает оверлей (`smoke_resolution_controls.py`).

## 2. Геометрия и отрисовка оверлея

- [x] 2.1 Создать `client/touch_controls.py` с `TouchLayout(width, height, settings)` → прямоугольники `move_stick`, `look_stick`, `fire`, `dash`, `use`, `weapon_next`, радиусы баз и центры; размеры — `engine/viewport.ui_px` от авторских единиц вёрстки, умноженных на настройки-множители (`touch_stick_size`/`touch_button_size`). Проверить: на 640×480, 1000×700, 2400×1080 и 3840×2160 прямоугольники не пересекаются и лежат внутри экрана.
- [x] 2.2 Реализовать отрисовку `draw(surface, state)` — база, ручка по отклонению, кнопки с подписями; поверхности кэшировать по `(kind, size_px, alpha)` и чистить на ресайзе/смене настроек. Проверить: `smoke_touch_controls.py` — после свипа размеров кэш ограничен, а `draw()` не создаёт `Surface` на кадр (счётчик через подменённый `pygame.Surface`).
- [x] 2.3 Сделать hit-тест частью того же модуля (`hit(pos)` → имя органа) и доказать совпадение с отрисовкой: хитбокс кнопки равен её нарисованному прямоугольнику, у стика — круг базы. Проверить: `smoke_touch_controls.py` — клик в центр каждого органа попадает в него, клик на 2 px наружу — никуда.

## 3. Ввод касаний

- [x] 3.1 Читать `FINGERDOWN`/`FINGERMOTION`/`FINGERUP` в `InputHandler.update()` и вести состояние по `finger_id`: палец захватывается стиком, если попал в его базу/хитбокс, и держится до `FINGERUP` этого id. Проверить: `smoke_touch_controls.py` — второй палец не перехватывает первый стик, `FINGERUP` чужого id не отпускает.
- [x] 3.2 Мультитач: движение и обзор одновременно, отпускание одного пальца не сбрасывает второй. Проверить: `smoke_touch_controls.py` — `move_y`/`move_x` держатся, пока правый стик крутит `rotate_delta` другого знака.
- [x] 3.3 Обзор: `rotate_delta = clamp(dx/radius) * touch_look_speed * dt` с потолком `MAX_TURN_DT`, вертикаль игнорируется, касание вне органов камеру не крутит. Проверить: `smoke_touch_controls.py` — одинаковое отклонение даёт одинаковый угол при 30 и 240 FPS, тап по пустому месту даёт `rotate_delta == 0`.
- [x] 3.4 Кламп отклонения до радиуса: палец за базой не расширяет её и не увеличивает скорость. Проверить: `smoke_touch_controls.py` — отклонение 3× радиуса даёт то же `rotate_delta`, что и 1×.
- [x] 3.5 Цифровое движение с гистерезисом октантов (0.35 на включение, 0.25 на выключение) → те же восемь направлений, что `WASD`. Проверить: `smoke_touch_controls.py` — диагональ даёт `(1,1)`, дребезг у границы не переключает ось.
- [x] 3.6 Кнопки «Огонь», «Дэш», «Использовать», перебор оружия: огонь — удержание (`shoot`) плюс фронт (`shoot_pressed`), дэш — только фронт (`dash_pressed`, один пакет на нажатие), `use` — защёлка `_use_pending`, которую гасит `_input_snapshot()`. Проверить: `smoke_touch_controls.py` — удержание «Огня» не даёт дэш, «Дэш» не стреляет, короткий тап «Использовать» доходит до отправки.
- [x] 3.7 На Android выключить платформенную эмуляцию мыши целиком — `SDL_TOUCH_MOUSE_EVENTS=0` в `main.py` **до** импорта pygame; в игре это значит, что `MOUSEBUTTONDOWN`/`mouse.get_pressed()[0]`/`mouse.get_rel()` синтетических событий не получают, а меню принимают палец через `GameClient._menu_finger_events()` (тап → клик, вертикальный свайп → `MOUSEWHEEL`). На десктопе флаг не ставится: мышь остаётся указателем по оверлею. Проверить: `smoke_touch_controls.py` — один тап «Огня» даёт один интент при включённом флаге; `smoke_android_build.py` — порядок флага относительно импорта pygame и перевод пальца в клик/прокрутку.
- [x] 3.8 `InputHandler.reset_touch()` — сброс захватов, отклонений и защёлок; в touch-режиме клавиши движения/огня/дэша не читаются, `Esc` и цифры оружия работают. Проверить: `smoke_touch_controls.py` — после сброса персонаж стоит, `Esc` по-прежнему ставит `pause_requested`.

## 4. Интеграция в игровой цикл

- [x] 4.1 В `GameClient._render()` рисовать оверлей после `self.bridge.render()` и до `pygame.display.flip()`, только когда режим «Телефон»; при других режимах — ни одного вызова и ни одного импорта. Проверить: `smoke_touch_controls.py` — в режиме «Мышь» оверлей не рисуется и модуль не импортируется, в «Телефоне» — рисуется в поверхность окна.
- [x] 4.2 Звать `input_handler.reset_touch()` в `_enter_pause`, `_exit_pause`, `_enter_end`, `_exit_end`, `_leave_to_lobby`, `_init_from_server` **до** `pygame.event.clear()`, плюс на `WINDOWFOCUSLOST`. Проверить: `smoke_touch_controls.py` — пауза с зажатым стиком оставляет персонажа стоять после возврата; повтор старой ошибки (сброс после `clear`) тест роняет.
- [x] 4.3 В `_game_loop` лог старта партии берёт раскладку из `control_hints()`, чтобы подсказки не обещали мышь в режиме «Телефон». Проверить: `smoke_resolution_controls.py`.
- [x] 4.4 Убедиться, что `_process_input`/`_input_snapshot` не менялись по смыслу: те же `_dash_pending`, `_fire_press_buffered`, `INPUT_FLAG_*`. Проверить: `smoke_fire_intent.py` и `smoke_player_dash.py` остаются зелёными без правок.

## 5. Экран настроек

- [x] 5.1 Вывести четыре тач-ключа строками `SPEC` (ползунки/стрелки) и переключатель режима с тремя значениями; проверить прокрутку списка и что выбранная строка видна (`smoke_menu_layout.py`).
- [x] 5.2 Добавить `kind: "text"` для `server_host` и переиспользовать существующее поле ввода: Enter применяет, Esc отменяет, неверный ввод отвергается сообщением; на Android вокруг правки звать `pygame.key.start_text_input()`/`stop_text_input()` и обрабатывать `TEXTINPUT`. Проверить: `smoke_resolution_controls.py` — поле правит `server_host`, `save()` его сохраняет, `launch_config()` читает.
- [x] 5.3 Проверить, что новые строки не ломают вёрстку на 640×480 и 3840×2160: прямоугольники не пересекаются, подписи усекаются, клик в центр строки попадает в неё (`smoke_menu_layout.py`).

## 6. Android-упаковка

- [x] 6.1 Добавить `main.py` в корне — точку входа p4a: ставит `SDL_TOUCH_MOUSE_EVENTS=0` до импорта pygame, `os.chdir` к распакованному дереву, `launch_config([])` без argv, `require_numpy()`, `asyncio.run(client.run())`; проверить: `python -c "import main"` не падает, `launch_config([])` отдаёт адрес из конфига (вне Android — как раньше).
- [x] 6.2 Добавить `client/mobile.py` (`IS_ANDROID` по `ANDROID_ARGUMENT`/`ANDROID_APP_PATH`/`P4A_BOOTSTRAP`, `TOUCH_NO_MOUSE`, `UPDATER_ENABLED`, `writable_root()`) и отключить на Android `_maybe_update()`/`update_offer`, оставив отказ по `PROTOCOL_VERSION`; проверить: `smoke_config.py` и `smoke_update_prompt.py` — на подменённом `IS_ANDROID=True` запроса обновления нет, на десктопе всё как раньше.
- [x] 6.3 Написать `buildozer.spec`: `p4a.bootstrap = sdl2`, `p4a.local_recipes = ./tools/android/recipes`, `android.archs = arm64-v8a`, `android.api = 36`, `android.minapi = 24`, `android.ndk_api = 24`, `android.ndk = 28c`, `android.permissions = INTERNET`, `orientation = landscape`, `fullscreen = 1`, `android.private_storage = True`, `requirements = python3, pygame, numpy`, исключения для `.git`, `openspec/`, `releases/`, `tools/`, `music/`, `smoke_*.py`, `config.json`. Проверить: `smoke_android_build.py` — значения спек и исключения; сама сборка — на CI (см. 7.1).
- [x] 6.4 Положить локальные рецепты `tools/android/recipes/`: `pygame` 2.6.1 (p4a поставляет 2.1.0, а `requirements.txt` требует ≥ 2.5) и `python3`/`hostpython3` **оба** 3.12.14 (p4a v2026.05.09 собирает 3.14.2; при расхождении версий p4a отказывается собирать). Патчи p4a подключаются абсолютным путём в каталог его рецепта; все четыре проверены на CPython 3.12.14. Проверить: `smoke_android_build.py` — пины, совпадение версий, абсолютные пути патчей; собранный APK сообщает версию pygame ≥ 2.5 (`adb logcat`/экран настроек).
- [x] 6.5 Убедиться, что `client/numpy_bootstrap.require_numpy()` на Android не пытается `pip install` (numpy приходит требованием APK) и печатает `ANDROID_FAILED_MESSAGE`, а `client/updater.py` не вызывается; проверить `smoke_numpy_bootstrap.py` (сценарий «д») и `smoke_android_build.py`.
- [x] 6.6 Сделать сбой запуска видимым без `adb`: `client/fatal.py` (`StartupLog` с этапами, зеркало в `stderr`, `faulthandler`, маркер `rout3-boot.json` со статусами `startup`/`failed`/`running` и хвостом журнала, `show_fatal()` с перебором режимов дисплея) и `main.py` (единый `except BaseException`, экран прошлого запуска, `mark_running()` перед циклом событий, `clear_boot()` после успеха и после показанного отчёта, никакого `sys.exit`). Проверить: `smoke_android_build.py` (`section_crash_reporter` — маркер переживает падение, хвост с traceback виден на следующем старте, экран реально что-то рисует).
- [x] 6.7 Добавить `tools/android_log.py` — снятие диагноза по `adb` одной командой: модель/ABI/версия Android, `logcat` + буфер `crash`, `run-as … cat files/rout3-startup.log` и `rout3-boot.json`, отчёт в один файл; предупреждение о несовпадении ABI APK и устройства. Проверить: `smoke_android_build.py` (`section_device_tool`).

## 7. Сборка и запуск на телефоне

- [x] 7.1 Собрать APK на **облачном CI** (`.github/workflows/android.yml`, x86_64 `ubuntu-latest`): workflow ставит buildozer, выполняет `buildozer -v android debug` и выкладывает `bin/*.apk` артефактом `rout3-android-apk` (30 дней), плюс `workflow_dispatch` для ручного запуска. Локально на Jetson (aarch64) сборка невозможна — NDK не выпускает host-тулчейн под linux-aarch64. Проверить: прогон зелёный и артефакт существует; `smoke_android_build.py` держит, что workflow собирает и выгружает APK.
- [ ] 7.2 Скачать артефакт и установить на телефон (Android 12+, arm64) — `adb install -r` или открыть APK на устройстве; проверить, что приложение стартует без Python и без сети, меню читаемо, кадр занимает экран, системные панели не перекрывают HUD.
- [ ] 7.3 Подключиться к серверу на Jetson (`192.168.1.120:7777`, та же Wi-Fi) через поле адреса в настройках, создать/войти в партию; проверить, что телефон виден серверу как обычный клиент и `PROTOCOL_VERSION` не менялся.
- [ ] 7.4 Проверить на устройстве: два стика одновременно, обзор только правым, раздельные «Огонь»/«Дэш», тап по пункту меню и свайп списка, пауза и возврат без залипания, сохранение настроек после перезапуска приложения (и после обновления APK). Проверить наблюдаемым поведением и записать результат в задачу.
- [ ] 7.5 Установить APK с маркером запуска на телефон и снять первый диагноз: экран «прошлый запуск прервался» (этап + хвост журнала) либо отчёт `tools/android_log.py` (logcat + приватные файлы). Записать в задачу этап, на котором умер запуск, версию Android и ABI; без этих данных причина мгновенного закрытия не определяется.

## 8. Документация и интеграция

- [x] 8.1 Обновить `AGENTS.md`: третий режим управления (раскладка, где живёт оверлей, сброс удержаний) и раздел про Android-сборку (bootstrap, архитектуры, локальные рецепты, где лежит APK). Проверить: описанные команды и пути существуют.
- [x] 8.2 Прогнать `tools/check_engine_imports.py` — тач-слой не попал в `engine/`. Проверить: сканер зелёный.
- [x] 8.3 Прогнать весь набор `smoke_*.py` (в первую очередь `smoke_resolution_controls.py`, `smoke_config.py`, `smoke_update_prompt.py`, `smoke_menu_layout.py`, `smoke_viewport.py`, `smoke_fire_intent.py`); проверить: новых падений нет. Измерено: 55/63 зелёных, а 8 падений (`smoke_items_mutation`, `smoke_launchers`, `smoke_linux_launchers`, `smoke_mt1_server`, `smoke_player_dash`, `smoke_projectiles`, `smoke_sector_editor_screen`, `smoke_text_map`) воспроизводятся на базовом коммите `8479804` в чистой копии — к этому change'у они отношения не имеют.

## Workflow follow-up

- Заархивировать change после проверки на телефоне и обновить основные спеки.
