# Tasks

## 1. Заглушка ввода в цикле отправки

- [x] 1.1 `client/performance.py`: `input_send_loop(..., enabled=None)`. При
      `enabled()` == `False` не вызывать `send_input` и даже не читать `snapshot`,
      но продолжать цикл (не выходить).
- [x] 1.2 `client/client.py`: `_input_task` получает
      `enabled=lambda: not self._paused`.

## 2. Тесты

- [x] 2.1 `smoke_pause.py`: заглушённый цикл ничего не шлёт и не сэмплит снапшот;
      возобновляется на прежней частоте (не пересоздаётся); `enabled=None`
      сохраняет прежнее поведение; `should_run=False` по-прежнему завершает цикл;
      провод `_paused` присутствует в клиенте.
- [x] 2.2 Прогнать `smoke_pause.py`, `smoke_ui.py`, `smoke_perf.py`.

## 3. Документация

- [x] 3.1 `AGENTS.md`: отметить, что открытие паузы глушит локальный ввод.
