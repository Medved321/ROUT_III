## Context

См. proposal.md — Why. Версия клиента, сообщаемая в HELLO, берётся из
`shared/version.py` (`CLIENT_VERSION`), который `tools/build_client.py` просто
копирует в zipapp-архив без правки. `PROTOCOL_VERSION` живёт там же и меняется
только вручную. Сервер сравнивает `client_version` с `releases.latest()`
(`server/server.py:111`).

## Goals / Non-Goals

**Goals:**
- Собранный артефакт сам описывает себя: `CLIENT_VERSION == --version`, `PROTOCOL_VERSION` из исходника.
- Старый механизм апдейта (предложение, скачивание, sha256, перезапуск) не трогается.

**Non-Goals:**
- Не меняем `server.py` и клиентский рантайм.
- Не вводим семантического сравнения версий (сравнение строк остаётся).

## Decisions

### D1: Штамп версии в staging-копии version.py
В `tools/build_client.py` после `shutil.copytree` перезаписываем
`staging/shared/version.py` строкой с `CLIENT_VERSION = "<version>"` и
`PROTOCOL_VERSION`, взятым из исходников (через import, `str`). Плюс:
- артефакт-файл (`rout-client-<version>.pyz`), `latest.txt` и HELLO-версия всегда согласованы;
- обычное `run_client.py` (из исходников) не затрагивается.

_Альтернатива:_ поднимать `CLIENT_VERSION` вручную перед каждой сборкой — легко
забыть, возвращает ту же ошибку. Отброшена.

### D2: Версия как plain string
Форматируем `shared/version.py` так же, как исходный (одна строка `CLIENT_VERSION`),
чтобы diff/логика не отличались.

## Risks / Trade-offs

- **[В модуле два значения]** если в будущем `version.py` обрастёт полями —
  штамп перезапишет только `CLIENT_VERSION`/`PROTOCOL_VERSION`. → Пишем файл
  целиком из штампуемых констант, это явно документировано в docstring-сборки.
- **[Число пересборки]** существующие 1.0.0-артефакты на чужих машинах так и
  останутся «1.0.0» — им просто снова предложат апдейт. → Ожидаемое поведение
  апдейтера, правится новой сборкой.