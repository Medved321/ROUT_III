# Tasks: применение конфигурации клиента

## 1. Применение значений конфига

- [ ] 1.1 `client/engine/bridge.py`: убрать из `RenderBridge.__init__` сброс
  `gs.render_scale = scale` и `gs.view_dist = const.VIEW_DIST_DEFAULT`; значения
  берутся из `apply_runtime`/`game_state`. Если совместимость требует параметр
  `scale`, сделать его `None` и не перетирать уже применённый `gs.render_scale`.
  Проверка: юнит-тест — после `Settings.apply_runtime` и создания
  `RenderBridge` `gs.view_dist`/`gs.render_scale` равны значениям конфига.
- [ ] 1.2 `client/settings.py`: гарантировать согласованность
  `const.SCALE == rendering.SCALE == gs.render_scale` и
  `const.FOV == rendering.FOV` на выходе `apply_runtime`. Проверка: те же
  ассерты в smoke.
- [ ] 1.3 `client/client.py`: вызывать `apply_runtime` так, чтобы настройки не
  перекрывались при `_init_from_server` (после создания бриджа либо без
  перетирания). Проверка: headless — при `config.json` с `view_dist=1600`,
  `render_scale=2`, `fov=110` значения видны в `gs`/`const` в игре.

## 2. Сохранение адреса сервера и имени

- [ ] 2.1 `client/client.py` `_attempt_connect`: после успешного подключения
  записать `server_host`, `server_port`, `player_name` в `Settings` и вызвать
  `save()`. CLI-override не сохранять. Проверка: юнит-тест на временном
  `config.json` — после «подключения» файл содержит введённые значения.
- [ ] 2.2 Проверить, что `config.json` после первого запуска содержит все ключи
  `DEFAULTS`, включая серверные. Проверка: `Settings().load(); set(...);
  save()` → содержимое файла содержит ключи.

## 3. Проверка

- [ ] 3.1 Новый/расширенный smoke: применение FOV/`view_dist`/`render_scale`/
  `fps_cap`, согласованность масштаба, persist адреса, приоритет CLI над
  конфигом с отсутствием записи в файл. Проверка: PASS.
- [ ] 3.2 Регрессия: `smoke_ui.py`, `smoke_launch.py`, `smoke_combat.py` — PASS;
  `py_compile` изменённых модулей.
- [ ] 3.3 `openspec validate client-config-application` — PASS.
