## Why

На Linux игру нельзя запустить штатно: `run_client.bat`/`run_server.bat` — это
batch-файлы cmd.exe, а Windows-лаунчер `py` на Linux отсутствует. При этом
проект исходит из того, что «на машине достаточно установленного Python», но на
Linux это не гарантировано: на чистой системе CPython 3.12 может не быть вовсе,
и first-run упирается в ручную установку интерпретатора.

## What Changes

- **POSIX shell-лаунчеры** `run_client.sh` и `run_server.sh`: исполняемые,
  работают из любого каталога, находят подходящий CPython (≥ 3.12, не
  free-threaded) и запускают игру через общий `tools/bootstrap.py run …`
  (та же схема venv + `requirements.txt`, что и на Windows), пробрасывая
  аргументы без искажений.
- **Автоподготовка интерпретатора** `tools/ensure_python.sh`: если подходящего
  Python нет, скачивается pinned **standalone CPython 3.12.14**
  (python-build-standalone, `install_only`) под архитектуру машины
  (`x86_64` / `aarch64`, gnu), проверяется `sha256`, распаковывается в
  пользовательский каталог (`${XDG_DATA_HOME:-$HOME/.local/share}/ROUT/python`).
  Права root не нужны, повторный запуск переиспользует уже установленный
  интерпретатор.
- **`tools/bootstrap.py` учитывает собственный `sys.executable`** первым
  кандидатом: вызов `python3.12 tools/bootstrap.py run …` на Linux не должен
  искать `py`/`python` и падать там, где в системе есть только `python3`.
- **`.gitattributes`**: `*.sh` — `text eol=lf`; на `.sh` выставляется
  исполняемый бит в индексе git.
- **Регрессионные проверки** shell-лаунчеров: LF-переводы, shebang,
  исполняемость, проброс `"$@"`, совпадение pinned URL/`sha256`, синтаксис
  (`sh -n`), выбор интерпретатора через стаб — без реальной загрузки в тестах.

**Non-goals:** Windows-`.bat` (`client/launcher`) и автоустановка зависимостей
(`client/onboarding`) не меняют своих требований; никаких системных пакетов или
`sudo`; поддержка архитектур вне `x86_64`/`aarch64` gnu и musl-дистрибутивов не
вводится (для них — понятная ошибка); игровой код, протокол и `shared/` не
затрагиваются.

## Capabilities

### New Capabilities
- `client/linux-launcher`: запуск клиента и сервера на Linux через POSIX
  shell-лаунчеры с автоматической подготовкой CPython 3.12 в пользовательском
  каталоге, когда подходящего интерпретатора на машине нет.

### Modified Capabilities
- нет — требования существующих (неархивированных) capability `client/launcher`
  (детерминированный разбор Windows-`.bat`) и `client/onboarding` (venv,
  зависимости, запуск артефакта) сохраняются; Linux-платформа и подготовка
  интерпретатора выделены в отдельную capability.

## Impact

- **Новые файлы**: `run_client.sh`, `run_server.sh`, `tools/ensure_python.sh`,
  `smoke_linux_launchers.py`; запись `*.sh text eol=lf` в `.gitattributes`.
- **Изменяемые файлы**: `tools/bootstrap.py` (`find_python()` — `sys.executable`
  первым кандидатом); при необходимости раздел «Запуск» в `AGENTS.md`/`SPEC.md`.
- **Не меняется**: протокол (`shared/`), сервер, игровой код клиента,
  `run_*.bat`, `requirements.txt` (всё та же `pygame>=2.5.0`).
- **Первый запуск без Python**: сетевая загрузка ~65 МБ (tar.gz) и распаковка
  (~200 МБ в пользовательском каталоге); нужны `curl` или `wget`, `sha256sum`,
  `tar`.
- **Риск**: pinned-URL/`sha256` устаревают при смене релиза
  python-build-standalone — фиксируются константами и покрываются тестом;
  `WrongSha`/нет сети/неизвестная архитектура завершаются понятной ошибкой и
  ненулевым кодом, без запуска игры.
