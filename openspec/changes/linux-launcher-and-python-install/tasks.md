# Tasks: запуск на Linux и автоустановка Python

## 1. POSIX shell-лаунчеры

- [x] 1.1 Создать `tools/ensure_python.sh` (`#!/usr/bin/env sh`): функции определения архитектуры, поиска интерпретатора (`ROUT_PYTHON_DIR` → `python3.12` → `python3` → `python`, версия ≥ 3.12, не free-threaded) и подготовки CPython. Проверка: `sh -n tools/ensure_python.sh`.
- [x] 1.2 Создать `run_client.sh` (цель `tools/launch_client.py`) и `run_server.sh` (цель `run_server.py`): вычисляют корень проекта относительно скрипта, получают интерпретатор из `tools/ensure_python.sh`, запускают `tools/bootstrap.py run <цель> "$@"`. Проверка: `sh -n`, запуск из другого каталога, аргументы доходят целиком.
- [x] 1.3 ЛФ/exec: добавить в `.gitattributes` строку `*.sh text eol=lf` и выставить исполняемый бит в индексе (`git update-index --chmod=+x run_client.sh run_server.sh tools/ensure_python.sh`). Проверка: в файлах нет CRLF, git сообщает режим `100755`.

## 2. Автоподготовка CPython 3.12

- [x] 2.1 Зафиксировать в `tools/ensure_python.sh` pinned-константы: URL-шаблон релиза `20260924`, `sha256` для `x86_64` и `aarch64` (см. design D1), каталог `${XDG_DATA_HOME:-$HOME/.local/share}/ROUT/python/3.12.14` с переопределением `ROUT_PYTHON_DIR`. Проверка: константы совпадают с ожидаемыми в `smoke_linux_launchers.py`.
- [x] 2.2 Реализовать загрузку и установку: `curl`/`wget` → `sha256sum`/`shasum -a 256` → распаковка в `mktemp -d` → перенос дерева в каталог из 2.1; идемпотентность (готовый `bin/python3` версии 3.12 не перекачивается). Проверка: повторный вызов не скачивает заново (проверка на Linux/WSL вручную).
- [x] 2.3 Негативные ветки: `sha256` не совпал → очистка и ненулевой код; неизвестная архитектура → понятное сообщение; нет `curl` и `wget` → понятное сообщение; `ROUT_PYTHON_NO_INSTALL=1` → без сети, подсказка установить Python и ненулевой код. Проверка: коды выхода и текст сообщений (стабы).

## 3. Переиспользование интерпретатора в bootstrap

- [x] 3.1 В `tools/bootstrap.py` `find_python()`: первым кандидатом проверять `sys.executable` (тот же `_probe`, версия ≥ 3.12, не free-threaded). Проверка: `py -3.12 smoke_launchers.py` и `py -3.12 smoke_launch.py` зелёные; на Linux `python3 tools/bootstrap.py ensure` возвращает путь без поиска `py`/`python`.

## 4. Регрессионные тесты

- [x] 4.1 Создать `smoke_linux_launchers.py`: формат файлов (shebang, только LF, нет CRLF, исполняемость), ссылки на `tools/ensure_python.sh` и проброс `"$@"`, совпадение pinned URL/`sha256`. Проверка: `py -3.12 smoke_linux_launchers.py` — PASS.
- [x] 4.2 Добавить в `smoke_linux_launchers.py` проверку `sh -n` для всех трёх скриптов, если `sh`/`bash` доступен (иначе — помеченный пропуск), и проверку выбора интерпретатора через стаб `python3.12` в `PATH` (без загрузки). Проверка: PASS или явный skip без падения.
- [x] 4.3 Убедиться, что на неисправленном `tools/bootstrap.py` (без `sys.executable` в кандидатах) сценарий «bootstrap через `python3` без `py`/`python`» из теста падает — свидетельство, что тест ловит дефект.

## 5. Верификация

- [x] 5.1 Прогнать весь smoke-набор (`Get-ChildItem smoke_*.py | ForEach-Object { py -3.12 $_.Name }`) и `py -3.12 tools/check_engine_imports.py`; падений, кроме двух известных нарушений `check_engine_imports`, нет.
- [x] 5.2 `openspec validate linux-launcher-and-python-install --strict` проходит.
- [x] 5.3 Ручная проверка на Linux (WSL/VM/CI при наличии): (а) наличие `python3.12` — лаунчер использует его; (б) симуляция отсутствия Python (`ROUT_PYTHON_DIR` указывает на пустой каталог, `python3.12`/`python3` убраны из `PATH`) — идёт загрузка pinned-сборки, проверка суммы, распаковка и успешный запуск; (в) повторный запуск — без загрузки.
