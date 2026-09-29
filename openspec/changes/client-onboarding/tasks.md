# Tasks: клиентский onboarding (новые машины)

## 1. Bootstrap и venv

- [x] 1.1 Создать `tools/bootstrap.py`: `find_python()` (кандидаты `py -3.12`, `py -3`, `py`, `python`), `ensure(venv_dir)` (создание `.venv`, проверка `import pygame`, при провале `pip install -r requirements.txt`, пересоздание venv в случае поломки), `run_argv(venv_dir, args)` (запуск через venv-python и проброс кода возврата). Поддержка `ROUT_NO_PIP=1` (пропуск pip) и `--venv <dir>` для тестов без сети. Проверка: `py -3.12 tools/bootstrap.py --venv <tmp> -c "import sys;print('ok')"` с `ROUT_NO_PIP=1` возвращает 0 и печатает `ok`; без аргумента — печатает подсказку.
- [x] 1.2 Проверка восстановления сломанного venv: удалить `pygame`-индикатор модуля в venv не трогая его (смок-тест подменяет инсталляцию); убедиться, что `run` пересоздаёт venv и продолжает. Проверка: юнит-тест на `ensure` с инжектированным «не импортируется pygame» и `ROUT_NO_PIP=1`.

## 2. Адрес сервера в конфиге

- [x] 2.1 В `client/settings.py` добавить в `DEFAULTS` ключи `server_host` (`"localhost"`), `server_port` (`7777`), `player_name` (`"Player"`). Проверка: читается/сохраняется в `config.json` через существующие `load/save` (юнит-тест на временный путь конфига).
- [x] 2.2 Добавить `launch_config(cli_args=None) -> (host, port, name)` в `client/settings.py`: значения из конфига, затем приоритет `--host`/`--port`/`--name` из CLI без записи в конфиг. Проверка: юнит-тест — `launch_config([])` = дефолты; с ключами конфига = значения конфига; с `--host` = CLI-значение, файл не меняется.

## 3. Экран подключения и поток GameClient

- [x] 3.1 Создать `client/connect_screen.py`: поля «хост», «порт», «имя», кнопка «Подключиться», строка статуса/ошибки; отрисовка и обработка клавиатуры/мыши по образцу `lobby_screen.py`. Проверка: headless-рендер экрана (dummy videodriver, как `smoke_ui.py`) — кадры с/без ошибки отличаются.
- [x] 3.2 Рефактор `GameClient.run()`: до подключения циклически показывает экран подключения (предзаполнен из `launch_config`); попытка `await network.connect(name)` с состоянием «Подключение…»; при провале — `last_error` конвертируется в сообщение («Сервер недоступен» для таймаута, текст несовместимости версии для протокола) и экран остаётся открытым; при успехе — прежняя диспетчеризация лобби/игры. Повторная попытка создаёт свежий `NetworkClient` из полей экрана. Проверка: интеграционный smoke — доступный сервер → вход в лобби; недоступный адрес → экран с сообщением, процесс жив.
- [x] 3.3 Убедиться, что `run_client.py` больше не создаёт `GameClient` с хардкод-хостом: передаёт только имя, хост/порт берутся через `launch_config`. Проверка: `py_compile` + интеграционный smoke из 3.2.

## 4. Лаунчер установленного артефакта

- [x] 4.1 Создать `tools/launch_client.py`: выбор между последним установленным `releases/rout-client-*.pyz` (через `shared.releases.latest_version`) и исходниками; сравнение версий по кортежам компонентов (`1.0.10` > `1.0.9`); нечисловые/битые версии трактуются как «запустить исходники»; запуск через `sys.executable`/venv-python с пробросом argv. Проверка: юнит-тест на выбор артефакта с временным каталогом releases (файл-заглушка pyz) — новее → pyz, старше/нет → исходники.
- [x] 4.2 Обновить `MAIN_TEMPLATE` в `tools/build_client.py`: разбор `--host/--port/--name` заменяется на `client.settings.launch_config(sys.argv)`. Проверка: `tools/build_client.py --version 9.9.9 --releases-dir <tmp>` собирает pyz; извлечённый `__main__.py` содержит вызов `launch_config`; артефакт запускается на пустом конфиге (pygame-окно не требуется, проверка только что стартует до коннекта).
- [x] 4.3 Совместить: `run_client.bat` вызывает `bootstrap run launch_client.py %*`; `run_server.bat` — `bootstrap run run_server.py %*`. Проверка: py_compile; ручной контрольный запуск обоих .bat один раз на целевой машине.

## 5. Интеграция и регрессия

- [x] 5.1 Прогнать существующие smoke-тесты (`smoke_combat.py`, `smoke_integration_combat.py`, `smoke_projectiles.py`, `smoke_weapon_anim.py`, `smoke_ui.py`) после изменений `GameClient`/`settings`. Проверка: все PASS.
- [x] 5.2 Новый `smoke_launch.py`: (а) `launch_config` юнит; (б) `bootstrap.ensure` в temp-venv с `ROUT_NO_PIP=1`; (в) `launch_client` выбор артефакта на временном `releases/`; (г) headless-рендер `ConnectScreen`. Проверка: скрипт печатает PASS.

## 6. Финальная проверка

- [x] 6.1 `py_compile` по всем изменённым и новым модулям. Проверка: `py -3.12 -m py_compile <файлы>` без ошибок.
- [x] 6.2 `openspec validate client-onboarding` — PASS.