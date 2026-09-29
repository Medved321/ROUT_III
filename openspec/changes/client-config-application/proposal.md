## Why

Клиент читает `config.json`, но пользователь не видит эффекта: правки
`fov`/`view_dist`/`render_scale`/`fps_cap` не доходят до игры, а введённые на
экране подключения хост/порт/имя теряются после перезапуска.

Причины (проверено по коду):

- **Инициализация бриджа перекрывает конфиг.** `GameClient.__init__` применяет
  настройки (`client/client.py:62`, `client/settings.py:162-180`), задавая
  `const.FOV`, `rendering.SCALE`, `gs.view_dist`, `gs.render_scale`. Но при входе
  в игру создаётся `RenderBridge` (`client/client.py:359`), чей конструктор
  (`client/engine/bridge.py:49,54,56`) заново выставляет
  `gs.render_scale = scale` и `gs.view_dist = const.VIEW_DIST_DEFAULT`. Значение
  `scale` по умолчанию `= const.SCALE` вычислено **на этапе импорта** и не
  отражает позднюю правку `const.SCALE` в `apply_runtime`. Итог: `view_dist` и
  `render_scale` из конфига откатываются к дефолтам, а рендер и частицы
  расходятся по масштабу (`rendering.SCALE` ≠ `gs.render_scale`).
- **Экран подключения не сохраняет ввод.** `ConnectScreen` меняет поля
  `host`/`port`/`name` только в памяти (`client/connect_screen.py:70-78`);
  `GameClient._attempt_connect` их читает (`client/client.py:154-179`), но
  никогда не пишет обратно в `Settings`/`config.json`. `config.json` вообще не
  содержит `server_host`/`server_port`/`player_name`, то есть сохранение ни
  разу не выполнялось.

## What Changes

- **Единый момент применения настроек.** Значения `config.json` применяются
  после полной инициализации рендера/бриджа и больше ничем не перекрываются;
  `RenderBridge` берёт `view_dist`/`render_scale` из `Settings`, а не из
  «замороженных» дефолтов, и не сбрасывает уже применённые значения.
- **Согласованность масштаба.** `const.SCALE`, `rendering.SCALE` и
  `gs.render_scale` всегда равны одному значению из конфига.
- **Сохранение адреса сервера и имени.** Успешное подключение с экрана
  подключения записывает `server_host`/`server_port`/`player_name` в
  `config.json`; при следующем запуске экран предзаполнен и подключается по
  конфигу, если не заданы `--host`/`--port`/`--name`.

**Non-goals:** сетевой протокол, пайплайн апдейтера, UI настроек за пределами
применения значений, горячая перезагрузка конфига без перезапуска.

## Capabilities

### New Capabilities

- `client/config-application`: значения `config.json` (FOV, дальность,
  масштаб лучей, лимит FPS, чувствительность, HUD) реально применяются в игре;
  адрес сервера и имя переживают перезапуск клиента.

### Modified Capabilities

- `client/onboarding`: требование «Адрес сервера и имя игрока в конфиге»
  дополняется записью в конфиг из экрана подключения (сейчас только чтение).

## Impact

- `client/engine/bridge.py`: убрать сброс `gs.view_dist`/`gs.render_scale` из
  «замороженных» дефолтов; брать значения из `Settings`/`game_state`.
- `client/settings.py`: порядок применения, чтение текущих значений в бридже,
  `save()` после успешного подключения.
- `client/client.py`: сохранять `host`/`port`/`name` в `Settings` при
  `_attempt_connect`; применять настройки после создания бриджа.
- `client/connect_screen.py`: без изменений API (значения читает `GameClient`).
- `config.json`: появится с `server_host`/`server_port`/`player_name`.
- Тесты: `smoke_ui.py`, `smoke_launch.py`, новый smoke на применение
  `view_dist`/`render_scale` и на persist адреса.
