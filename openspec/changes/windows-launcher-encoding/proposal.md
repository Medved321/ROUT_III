## Why

Двойной клик по `run_client.bat` (и `run_server.bat`) ломается: cmd.exe рвёт
командные строки на фрагменты и запускает не тот интерпретатор. Наблюдаемые
ошибки:

```
'TALL' is not recognized as an internal or external command
C:\Python314\python.exe: can't open file '...\run': [Errno 2] No such file or directory
'run' is not recognized as an internal or external command
'launch_client.py' is not recognized ...
'3' is not recognized ...
'ановите' is not recognized ...
```

Причина (проверено по байтам): оба `.bat` сохранены как **UTF-8 без BOM с
LF-переводами строк** (`run_client.bat`: 461 байт, 16 LF, 0 CRLF;
`run_server.bat`: 452 байта, 16 LF, 0 CRLF) и содержат кириллицу в
`echo`-строках после `chcp 65001`. cmd.exe при активной кодировке 65001
разбирает batch-файл по байтовым смещениям и на многобайтных символах
«съезжает», из-за чего выполняет куски строк как отдельные команды. Как
следствие строка `py -3.12 tools\bootstrap.py run launch_client.py %*`
распадается, `py` берёт Python по умолчанию (`3.14`), а `run` трактуется как
имя скрипта. Кириллица в консоли при этом выводится mojibake.

Существующая спека `client/onboarding` требует автоустановки зависимостей и
лаунчера, но ничего не говорит о кодировке/разборе самих `.bat`, поэтому
дефект не покрыт.

## What Changes

- `.bat`-лаунчеры (`run_client.bat`, `run_server.bat`) переводятся на **CRLF**
  и объявляются **ASCII-only**: никаких не-ASCII символов, `echo`-сообщения —
  латиницей; кириллица (если нужна) выводится только через `python`-скрипт, а
  не через `echo` в batch.
- Убирается зависимость корректного разбора от `chcp 65001`; кодировка
  консоли настраивается так, чтобы не влиять на парсинг.
- Явно фиксируется порядок кандидатов интерпретатора (`py -3.12` → `py -3`
  → `py` → `python`) и корректный проброс аргументов `%*` без искажений.
- Понятное сообщение, если подходящий Python (3.12) не найден, вместо
  «`is not recognized`».

**Non-goals:** логика bootstrap/venv (`tools/bootstrap.py`), выбор артефакта
(`tools/launch_client.py`), сам Python-код клиента/сервера не меняются.

## Capabilities

### New Capabilities

- `client/launcher`: детерминированный запуск игры через Windows `.bat` —
  корректный разбор cmd.exe, устойчивый выбор интерпретатора и читаемые
  сообщения об ошибках.

### Modified Capabilities

<!-- нет изменённых: требования client/onboarding сохраняются, это их
дополнение на уровне разбора .bat -->

## Impact

- `run_client.bat`, `run_server.bat`: перевод строк CRLF, ASCII-only текст,
  надёжный проброс аргументов.
- Возможен общий ASCII-хелпер/шаблон для `.bat` (без кириллицы).
- Тесты: проверка байтов файлов (нет LF-only, нет байтов ≥ 0x80), прогон
  лаунчера на машине с Python 3.14 по умолчанию и установленным 3.12.
