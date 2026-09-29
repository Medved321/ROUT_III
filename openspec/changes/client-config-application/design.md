# Design: применение конфигурации клиента

## Context

`client/settings.py` — единственный источник значений; `apply_runtime`
(`client/settings.py:162`) раздаёт их в три места: `client.engine.constants`,
`client.engine.rendering` и `client.engine.game_state`. Рендерер использует
имена `FOV`/`SCALE`, импортированные из `constants` (`rendering.py:2`), поэтому
`apply_runtime` переприсваивает и `rendering.FOV`/`rendering.SCALE`
(`settings.py:171,179`). Частицы и декали читают `const.FOV` и `gs.render_scale`
(`particles.py:150,162,210`).

Проблема в том, что `RenderBridge` (`client/engine/bridge.py:46`) создаётся
позже (при входе в игру) и переписывает часть этих значений:

```
def __init__(self, surface, scale: int = const.SCALE):   # default вычислен при импорте
    gs.render_scale = scale                              # откат к «замороженному» SCALE
    gs.view_dist = const.VIEW_DIST_DEFAULT               # откат view_dist
```

Дефолт аргумента захватывается при определении класса, поэтому даже если
`apply_runtime` поменял `const.SCALE`, в `bridge` придёт старое `4`.

## Goals / Non-Goals

- Goal: любое значение из `config.json` видно в игре после перезапуска и после
  изменения в меню настроек.
- Goal: хост/порт/имя, введённые на экране подключения, переживают перезапуск.
- Non-Goal: менять формат/порядок ключей в `config.json` (обратная
  совместимость: старый файл без ключей сервера остаётся валидным).
- Non-Goal: трогать `client/raycaster.py` (у него свои `SCALE`/`FOV` для окна).

## Decisions

1. **Единственный владелец значений — `Settings`.** `RenderBridge` получает
   `scale`/`view_dist` из `Settings` в момент создания, либо вообще не трогает
   `gs.view_dist`/`gs.render_scale`, полагаясь на `apply_runtime`. Выбираем
   второе: убрать присваивания из `__init__` и читать актуальные значения из
   `game_state`.
   - Альтернатива: сделать `scale=None` и читать `const.SCALE` внутри — тоже
     работает, но дублирует источник.
2. **Порядок применения.** `apply_runtime` вызывается после создания
   `RenderBridge` (или бридж не перетирает значения) — тогда настройки
   применяются к игре один раз и остаются согласованными.
3. **Сохранение адреса.** `GameClient._attempt_connect` после успешного
   `network.connect` пишет `server_host`/`server_port`/`player_name` через
   `Settings.set` + `save`. CLI-override `--host/--port/--name` в конфиг не
   пишется (сохраняем семантику `launch_config`: CLI только на текущий запуск).
   Чтобы не затирать пользовательский конфиг «пустым» вводом, сохраняем уже
   нормализованные значения (fallback `localhost`/`7777`/`Player`).
4. **Согласованность масштаба.** Гарантируем `const.SCALE == rendering.SCALE
   == gs.render_scale` на выходе `apply_runtime` и после создания бриджа;
   добавить проверку в smoke-тест.

## Risks / Trade-offs

- Если `RenderBridge` перестанет задавать `view_dist`, а `apply_runtime` не был
  вызван (например, юнит-тест), останется дефолт `gs.view_dist` — он совпадает с
  `const.VIEW_DIST_DEFAULT`, поведение сохраняется.
- Запись в `config.json` при каждом подключении — дешёвая операция (маленький
  JSON), риск повреждения закрыт `try/except` в `Settings.save`.

## Migration

Существующий `config.json` без ключей сервера читается `Settings.load`
(отсутствующие ключи остаются в `DEFAULTS`). Первое успешное подключение
дозапишет ключи.
