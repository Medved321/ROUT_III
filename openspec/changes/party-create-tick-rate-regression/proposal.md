# Proposal

## Why

Слияние веток `b808b69` и `946e61e` оставило в `HEAD` рассогласованную пару
файлов:

- `server/lobby.py` создаёт `Party` с `tick_rate=self.tick_rate` и передаёт его в
  `GameWorld(...)`;
- `server/game_world.py` остался версии `946e61e`, где `GameWorld.__init__` не
  принимает `tick_rate`;
- `shared/protocol.py` уже дописывает `tick_rate` в `HELLO_ACK`, а
  `shared/version.py` остался с `PROTOCOL_VERSION = 15` вместо `16`.

Любая попытка создать партию (`CREATE_PARTY`, и кнопкой «Создать», и клавишей
`P`) падала на сервере с `TypeError: GameWorld.__init__() got an unexpected
keyword argument 'tick_rate'`. `ServerNetwork.datagram_received` логирует такое
исключение как `Malformed datagram` на уровне DEBUG и не отвечает клиенту, поэтому
кнопка выглядела «ничего не делает»: остальные кнопки лобби работают, потому что
не отправляют `CREATE_PARTY`.

## What Changes

- Вернуть `server/game_world.py` и `shared/version.py` в согласованное с
  `b808b69` состояние: `GameWorld` принимает и хранит валидированный
  `tick_rate`, все длительности/скорости выводятся из него, а версия протокола
  снова равна `16` — той, в которой `HELLO_ACK` несёт частоту тиков.
- Заменить в регрессе `smoke_lobby_map_picker.py` нажатие `P` на клик по
  реальной кнопке `create_btn`: путь указателя через `_menu_events` теперь
  проверяет, что `CREATE_PARTY` доходит до сервера и возвращает `JOIN_ACCEPT`.
- Починить устаревшие проверки, которые мешают прогону после восстановления
  контракта: неверный якорь мутации `_reset_items`, вызов `_apply_input` с `None`
  вместо объекта с `tick_interval`, запуск насосов надёжного канала в
  `smoke_projectiles.py`, и зафиксированные номера протокола.

**Non-goals**: не меняются механики, форматы пакетов (кроме возврата уже
существующего номера версии), редакторы и рендер. Change не архивирует
`sector-minimap-editor-fps-cleanup` и не обновляет главные спеки.

## Capabilities

### Modified Capabilities

- `server/tick-rate` — creation path must use the same negotiated rate and
  protocol version as the handshake; otherwise a party cannot be created at all.

## Impact

- **Сервер**: `server/game_world.py`, `shared/version.py`.
- **Тесты**: `smoke_lobby_map_picker.py`, `smoke_items_mutation.py`,
  `smoke_respawn.py`, `smoke_projectiles.py`, `smoke_player_dash.py`,
  `smoke_sector_server.py`.
- **Протокол**: `PROTOCOL_VERSION` возвращается с 15 на 16, чтобы соответствовать
  уже существующему полю `HELLO_ACK.tick_rate`.
- **Риск**: низкий. Файлы возвращаются к состоянию, которое уже проходило
  `smoke_server_fps.py` и `smoke_fire_intent.py`; дополнительно чистятся только
  устаревшие тестовые допущения.
