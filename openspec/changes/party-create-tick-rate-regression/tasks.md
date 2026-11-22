# Tasks

## 1. Восстановление сквозного контракта частоты тиков

- [x] 1.1 Вернуть `server/game_world.py` к согласованной версии: конструктор
      принимает и валидирует `tick_rate`, все длительности/скорости считаются из
      него, `handle_shoot` получает частоту-World. Проверить:
      `py -3.12 smoke_server_fps.py` (секции 1.3, 1.6, 1.7 — мир на 20 и 40 tps,
      одинаковая скорость в реальном времени, частота доходит до `handle_shoot`).
- [x] 1.2 Вернуть `PROTOCOL_VERSION = 16` в `shared/version.py` — версию, в которой
      `HELLO_ACK` несёт `tick_rate`. Проверить: `py -3.12 smoke_server_fps.py`
      (секция 1.5 — handshake и rejects старых клиентов),
      `py -3.12 smoke_reliability.py` (incompatible protocol rejected).
- [x] 1.3 Убедиться, что `CREATE_PARTY` и по кнопке, и по `P` доходит до сервера и
      возвращает `JOIN_ACCEPT`. Проверить: `py -3.12 smoke_lobby_map_picker.py`
      (секция `section_state` кликает реальный `create_btn` и требует появления
      партии в `server.lobby.parties`).

## 2. Актуализация регрессий после восстановления контракта

- [x] 2.1 Починить якорь мутации `_reset_items` в `smoke_items_mutation.py`
      (отступ внутри `if self.mode == 'pve'`). Проверить: все 21 мутация
      «caught», ни одного SKIP.
- [x] 2.2 Передать минимальный stand-in с `tick_interval` вместо `None` в
      `smoke_respawn.py`. Проверить: `py -3.12 smoke_respawn.py`.
- [x] 2.3 Запустить насосы надёжного канала в `smoke_projectiles.py`, чтобы FIRE
      не копился в буфере `NetworkClient`. Проверить: `py -3.12 smoke_projectiles.py`.
- [x] 2.4 Обновить зафиксированные номера протокола в `smoke_player_dash.py` и
      `smoke_sector_server.py` до 16. Проверить: оба smoke проходят.
- [x] 2.5 Привести `smoke_sector_server.py` к активному решению
      `floating-platforms-unrestricted`: враг поднимается на плиту в пределах
      `STEP_HEIGHT`, но не на слишком высокую. Проверить: `py -3.12
      smoke_sector_server.py` (секция floating support).

## 3. Финальная проверка

- [x] 3.1 Прогнать полный набор затронутых smoke'ов:
      `smoke_server_fps.py`, `smoke_fire_intent.py`, `smoke_lobby_map_picker.py`,
      `smoke_items.py`, `smoke_items_mutation.py`, `smoke_combat.py`,
      `smoke_respawn.py`, `smoke_infighting.py`, `smoke_projectiles.py`,
      `smoke_doors_keys.py`, `smoke_match_end.py`, `smoke_player_dash.py`,
      `smoke_hitscan.py`, `smoke_custom_map.py`, `smoke_mt1_party.py`,
      `smoke_sector_server.py`, `smoke_reliability.py`, `smoke_config.py`,
      `smoke_window_title.py`.
- [ ] 3.2 `py -3.12 -m py_compile` по изменённым файлам и
      `py -3.12 tools/check_engine_imports.py`.
- [ ] 3.3 `openspec validate party-create-tick-rate-regression --strict`.
- [ ] 3.4 Дождаться пользователя перед архивацией change и обновлением главных
      спецификаций (не делать автоматически).
