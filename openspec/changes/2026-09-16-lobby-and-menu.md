---
id: 2026-09-16-lobby-and-menu
status: proposed
date: 2026-09-16
author: assistant
schema: spec-driven
---

# Лобби и меню клиента

## Что сделано

- Сервер: `Lobby` + `Party` (`server/lobby.py`), несколько игровых сессий
  на одном сервере, изолированные `GameWorld`, тик 20 Гц.
- Протокол: новые пакеты 0x08–0x0E (Get Lobby, Lobby State, Create Party,
  Join Party, Join Fail, Leave Party); `Join Accept` получил флаг `has_map`.
- Клиент: `client/lobby_screen.py` (меню: имя, список партий, P/R/Esc),
  state machine `lobby ⇄ game`.
- Исправлено: импорт `PROJ_COEFF` в `client/renderer.py` (падал рендер),
  `shared/__init__.py` импортировал несуществующие `encode/decode`.
- Проект перемещён и оформлен по openspec в `PycharmProjects/ROUT III`.

## Проверено

- Интеграционный тест сети: create/join/leave party, отказ по неверному id,
  движение отражается в World State.
- Рендер-дымок: лобби и DDA кадры отрисовываются.
- E2E жизнь клиента: окно → лобби → партия → игра.

## Открытые вопросы

- Установка pygame: только для Python 3.12 (под 3.14 нет wheel).