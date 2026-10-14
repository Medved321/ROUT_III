# Doom-Style FPS Shooter — Спецификация

## Обзор

Серверо-ориентированный шутер в стиле Doom 1 с DDA raycasting рендерером.
Сервер управляет всем игровым состоянием. Клиент только рендерит и отправляет ввод.

## Архитектура

```
┌──────────┐     UDP/WebRTC      ┌──────────┐
│  Client  │ ◄─────────────────► │  Server  │
│ (Pygame) │     State Sync      │ (asyncio)│
│ Raycaster│     Input Commands   │ Game Loop│
└──────────┘                     └──────────┘
```

- **Сервер**: Authoritative — вся логика на сервере. 20 ticks/sec.
- **Клиент**: Меню-лобби + рендеринг (DDA raycasting), ввод.
- **Сеть**: UDP дейтаграммы. WebRTC data channels для NAT traversal.
- **Лобби**: Один сервер содержит множество партий (game sessions). Игрок создаёт партию или входит в существующую.

## Стек технологий

| Компонент | Технология |
|-----------|-----------|
| Сервер | Python 3.11+ / asyncio |
| Клиент | Python 3.11+ / Pygame |
| Raycaster | DDA алгоритм (как в Doom) |
| Сеть | UDP симплекс + WebRTC (aiortc) |
| Сериализация | struct (binary) |
| ECS | Кастомный мини-ECS |

## Сетевой протокол

### Формат пакета (binary, big-endian)

```
[1 byte: packet_type] [2 bytes: sequence] [N bytes: payload]
```

Версия протокола — `PROTOCOL_VERSION` в `shared/version.py` (текущая `8`). Клиент
и сервер обмениваются версиями в `HELLO`/`HELLO_ACK`; при несовпадении сервер
отказывает и состояние партии клиенту не отправляется. Версия растёт при любом
изменении формата пакета: так старый клиент отсекается рукопожатием (и получает
предложение обновиться), а не разбирает снимок в мусор — именно поэтому рост
записи игрока в `WORLD_STATE` с 21 до 27 байт потребовал `7 -> 8`.

Управляющие сообщения (вход в лобби, партии, файлы) идут поверх **надёжного
канала** (`RELIABLE_DATA` + `ACK`, см. `shared/reliability.py`). Игровой трафик
(`PLAYER_INPUT`, `WORLD_STATE`, `PING`/`PONG`) остаётся ненадёжным и не
ретранслируется (кроме `FIRE`, см. ниже). Всё — по одному UDP-сокету.

### Типы пакетов

| Type | Dir | Описание |
|------|-----|----------|
| 0x01 | C→S | Player Input (move, angle, shoot) |
| 0x02 | S→C | World State (entity positions, health) — только членам партии |
| 0x03 | C→S | Join Request (player name) — вход в лобби |
| 0x04 | S→C | Join Accept (player_id + карта, если началась игра) |
| 0x05 | S→C | Player Leave |
| 0x06 | C→S | Ping |
| 0x07 | S→C | Pong |
| 0x08 | C→S | Get Lobby (запросить список партий) |
| 0x09 | S→C | Lobby State (список партий и игроков) |
| 0x0A | C→S | Create Party (название партии, режим, опц. карта) |
| 0x0B | S→C | Party Created (party_id), затем Join Accept |
| 0x0C | C→S | Join Party (party_id) |
| 0x0D | S→C | Join Fail (причина) |
| 0x0E | C→S | Leave Party (выход в лобби) |
| 0x10 | S→C | Bullet Spawn |
| 0x11 | S→C | Damage Event |
| 0x12 | S→C | Death Event |
| 0x13 | S→C | Respawn Event |
| 0x20 | C→S | Hello (версия протокола + версия клиента), по надёжному каналу |
| 0x21 | S→C | Hello Ack (версия сервера, признак обновления, версия/размер/sha256) |
| 0x22 | C↔S | Reliable Data (ack, ack_bits, inner_type, frag_index, frag_count) |
| 0x23 | C↔S | Ack (ack, ack_bits) |
| 0x24 | C→S | File Request (запрос артефакта обновления), внутренний для 0x22 |
| 0x25 | S→C | File Data (артефакт целиком, фрагментируется каналом), внутренний |
| 0x26 | C→S | File Complete (результат применения), внутренний |
| 0x27 | C→S | Fire (fire_seq, weapon_id), внутренний для 0x22 |

Внутренние типы `0x03`, `0x08`, `0x0A`, `0x0C`, `0x0E`, `0x20`, `0x24`, `0x26`,
`0x27` передаются в payload `RELIABLE_DATA` (поле `inner_type`), а не как
отдельные датаграммы.

### Надёжный канал (0x22 / 0x23)

```
[uint16: ack] [uint32: ack_bits] [uint8: inner_type]
[uint16: frag_index] [uint16: frag_count] [payload...]
```

- Отправитель держит окно (`window=16`) неподтверждённых датаграмм, повторяет по
  RTO с экспоненциальным backoff (RTO оценивается по `PING`), после лимита попыток
  сессия считается разорванной.
- Получатель дедуплицирует по `sequence`, собирает фрагменты по `frag_index` и
  доставляет сообщения приложению строго по порядку и ровно один раз.
- Размер фрагмента ≤ `MAX_FRAGMENT_PAYLOAD` (1000 байт) — одна датаграмма под MTU.
- Файл проверяется по `sha256`; при несовпадении не применяется.

### Hello Ack (0x21)

```
[uint8: server_protocol]
[uint8: accepted]            // 1 = версии совместимы
[string: latest_client_version]
[uint8: has_update]
  // только если has_update=1:
  [string: update_version]
  [uint32: update_size]
  [string: update_sha256]
```

### Join Accept (0x04)

```
[uint8: player_id]
[uint16: map_width]    // 0 в лобби; 0 для секторной карты
[uint16: map_height]   // 0 в лобби; 0 для секторной карты
[uint8: has_map]       // 1 = вошли в партию
[uint8: map_format]    // 0 = тайловая (raw-байты), 1 = секторная (JSON)
[map_bytes...]         // карта, только если has_map=1
```

### Форматы карт

| map_format | Тип | `map_bytes` | Размеры w/h |
|------------|-----|-------------|-------------|
| 0 | Тайловая (`GameMap`) | плоский `width*height` байт, row-major | 8..64 |
| 1 | Секторная (`SectorMap`) | JSON (`"format":"sector"`, см. `shared/sector_map.py`) | 0 (JSON несёт геометрию) |

Таблица применяется и к `Create Party` (0x0A), и к `Join Accept` (0x04).

### Lobby State (0x09)

```
[uint8: party_count]
For each party:
  [uint16: party_id]
  [uint8: name_len][name]
  [uint8: player_count]
  For each player:
    [uint8: player_id]
    [uint8: name_len][name]
```

### Player Input (0x01)

```
[int8: move_x] [-1/0/1]
[int8: move_y] [-1/0/1]
[float32: angle]          // absolute, radians
[uint8: shoot]            // 0 or 1 (hold-fire / auto)
[uint8: weapon_id]
[uint8: flags]            // бит 0 = INPUT_FLAG_DASH (0x01)
```

### Fire (0x27) — надёжный, внутренний для 0x22

```
[uint32: fire_seq]        // монотонный счётчик клиента
[uint8: weapon_id]
```

Флаг `shoot` в `PLAYER_INPUT` — это **только состояние удержания кнопки**, а не
автопистолёт: сервер не создаёт из него выстрел. Единственный путь выстрела —
надёжный пакет `FIRE`, который клиент отправляет на каждое открытие своих врат
темпа (клик и удержание идут одним кодом). Клик, попавший в закрытые врата,
запоминается ровно на одно открытие (`_fire_press_buffered`) и отдаёт один
выстрел: очередь нажатий не нужна и была бы хуже, потому что копилась бы со
скоростью кликов. Ширина врат считается по оружию из снимка мира, а не по локально
выбранному номеру: локальный выбор — только запрос на смену, который сервер
применяет для оружия, которым игрок владеет. Отказ по кулдауну не возвращает
нажатие: клиент отодвигает врата на `fire_ready` и ждёт кулдауна. Сервер обрабатывает
`FIRE` идемпотентно по `fire_seq` и по таймеру кулдауна, а решение по интенту едет
в `WORLD_STATE` (поля `fire_seq` / `fire_ready` / `fire_reason`).

### World State (0x02) — отправляется 20 раз в секунду

```
[uint16: tick]
[uint8: mode]            // 0=pve, 1=pvp
[uint8: player_count]
For each player:
  [uint16: player_id]      // счётчик входов без потолка, поэтому uint16:
                           // в байте (uint8) 256-й вошедший ломал бы
                           // весь транслят структурной ошибкой
  [float32: x]
  [float32: y]
  [float32: angle]
  [uint8: health]
  [uint8: state]         // 0=alive, 1=dead, 2=shooting, 3=dashing
                         // state=3 also drives the client-side dash
                         // feedback (dust burst/trail for every player, screen
                         // speed lines for the local one). Effect only: the
                         // packet is unchanged.
  [uint8: weapon_id]
  [uint8: ammo_pistol]
  [uint8: ammo_shotgun]
  [uint8: ammo_rifle]
  [uint8: ammo_rocket]
  [uint8: ammo_bfg]
  [uint32: fire_seq]      // номер последнего ПРИНЯТОГО интента выстрела;
                         // отказ его не двигает
  [uint8: fire_ready]     // тиков до следующего разрешённого выстрела
  [uint8: fire_reason]    // 0=принято, 1=кулдаун, 2=нет патрона,
                         // 3=лимит прожектайлов, 4=нечего стрелять
  [uint8: name_len][name...]
[uint16: enemy_count]
For each enemy:
  [uint16: enemy_id]
  [float32: x]
  [float32: y]
  [float32: angle]
  [uint8: enemy_type]    // индекс ENEMY_TYPES
  [uint16: hp]
  [uint8: state]         // 0 = alive
[uint16: bullet_count]
For each bullet (снаряд врага или шар игрока):
  [float32: x]
  [float32: y]
  [uint8: r][uint8: g][uint8: b]   // цвет
  [uint8: from_player]   // 1 = проджектайл игрока (плазма-шар), 0 = пуля врага
[uint16: corpse_count]
For each corpse:
  [float32: x]
  [float32: y]
  [uint8: enemy_type]
[uint16: impact_count]
For each impact (короткоживущий маркер попадания):
  [float32: x]
  [float32: y]
  [uint8: impact_type]   // 0=wall, 1=enemy
```

Impact-события публикуются хитскан-пушками при попадании в стену/врага; клиент
по ним рисует локальные частицы (искры/дым) в честной точке попадания.

## Оружие и проджектайлы

RIFLE (слот 3, «плазма») — **проектайл-оружие**: при выстреле сервер создаёт
плазма-шар (`from_player=1`), который летит по направлению взгляда, шагается
по серверному тику, останавливается о стену и наносит урон при попадании во
врага (или игрока в PvP). Шар виден всем клиентам партии в `WORLD_STATE` и
рисуется как светящийся орб (многослойный круг, без поворота спрайта).
Остальное оружие — хитскан (мгновенный луч с пеллетами/конусом) без
проджектайла.

## Лобби и партии

- Игрок подключается к серверу и попадает в **лобби** (Join Accept без карты).
- Сервер рассылает **Lobby State** всем игрокам при каждом изменении партий.
- **Create Party**: создаёт партию и автоматически вводит в неё (Join Accept с картой).
  Payload: `[uint8: mode][string: name][uint32: request_id][uint16: map_w][uint16: map_h][uint8: has_map][uint8: map_format][map_bytes...]`.
  Если `has_map=1`, клиентская (отредактированная) карта проходит валидацию по
  своему формату (`map_format`, см. таблицу «Форматы карт»: для тайлов —
  `GameMap.validate_network`: размер 8..64, совпадение длины, есть свободные
  клетки; для сектора — `SectorMap.validate_network`: ссылки, лимиты, сектор и
  вещь `player1`) и используется в партии; иначе сервер берёт карту по
  умолчанию. Повторная команда с тем же `request_id` (или пока игрок уже в
  партии) не создаёт вторую партию — команда идемпотентна.
- **Join Party**: вход в существующую партию; при ошибке — Join Fail.
- **Leave Party**: возврат в лобби (Esc в игре).
- Каждая партия имеет собственный `GameWorld` и изолированный тик; состояние рассылается только членам партии.

## Обновление клиента

- Сервер хранит версионные артефакты в `releases/` (`rout-client-<версия>.pyz`,
  указатель `releases/latest.txt`), собирает их `py -3.12 tools/build_client.py`.
- При `HELLO` сервер сообщает актуальную версию; если версия клиента отличается,
  клиент показывает запрос на обновление. При согласии скачивает артефакт по
  надёжному каналу, проверяет `sha256` и сохраняет **отдельным файлом** новой
  версии (запущенный файл не перезаписывается), затем переключает указатель и
  перезапускается. При отказе или обрыве текущая версия продолжает работать.
- Откат: `client.updater.rollback` возвращает указатель на предыдущую версию.

## Игровой мир

- **Карта**: 2D grid 64x64 тайлов. Каждый тайл — integer (0 = пусто, 1-15 = стены разных типов).
- **Координаты**: float, world units = tiles.
- **FOV**: 66 градусов (как в Doom).
- **Высота стены**: Проекция по расстоянию (DDA).

## ECS (Entity Component System)

### Компоненты
- `Position(x, y)` — позиция в мире
- `Velocity(dx, dy)` — скорость
- `Rotation(angle)` — направление взгляда
- `Health(value)` — здоровье
- `Player(id, name)` — игрок
- `Collider(radius)` — коллизия
- `Sprite(texture_id)` — визуальный представление

### Системы (на сервере)
- `MovementSystem` — применяет velocity к position
- `CollisionSystem` — проверяет коллизии со стенами
- `CombatSystem` — обрабатывает выстрелы и урон
- `NetworkSystem` — рассылает состояние клиентам

## Клиент

### Меню (лобби)
- Поле ввода имени игрока
- Список партий: название, количество игроков, список имён
- Кнопки/хоткеи: **Создать партию [P]**, **Обновить [R]**, **Выход [Esc]**
- Клик по партии — вход; стрелки + Enter — навигация
- Лобби обновляется автоматически при изменениях

### DDA Raycaster
- Классический DDA (Digital Differential Analyzer)
- 1 луч на каждый пиксель столбца экрана
- Текстурированные стены (проекция)
- Пол и потолок (вертикальное размытие / простая заливка)

### Управление
- WASD — движение
- Мышь — поворот
- ЛКМ — стрельба
- 1-9 — оружие (будущее)

### Рендер
- разрешение: 1024x768 (internal resolution для raycaster: 320x200, апскейл x3)
- FPS: 60 (рендер), 20 (сеть)
- Z-Buffer для корректного перекрытия
- Сетка z-буфера задаётся **самим буфером**: тайловый путь строит
  `const.W // SCALE` колонок, секторный (BSP) — `const.W // render_scale`, и
  это разные настройки, которые адаптивный контроллер качества двигает
  независимо. Экранный столбец переводится в колонку только через
  `engine.framebuffer.depth_col()`, которая берёт длину из `len(z_b)`; вывод
  колонки из `SCALE`/`render_scale` неверен и ронял кадр в игровом цикле
  (change `depth-buffer-column-grid`).

### Секторные карты (BSP)
- Линия с двумя сайдедефами — пара сегов-близнецов; колонку двигает только
  близнец, обращённый к камере (`cross((v2-v1),(cam-v1)) < 0`). Обратный
  близнец не даёт полос и не трогает `clip_bot`/`clip_top`, иначе пол нижнего
  сектора отбрасывается. Правило не зависит от намотки линии.
- Нижняя грань перепада рисуется в обе стороны: полоса —
  `[min(yf, yfb), max(yf, yfb)]` по линиям пола смежных секторов. `clip_bot`
  после линии — `min(max(yfb, yl), flrob)` (по полу видимого близнеца, без
  переоткрытия закрытых колонок). Верхняя грань (`upper`) и условие
  `Bc <= Sf` не изменяются (change `sector-step-down-pit`).

## Структура проекта

```
doom-shooter/
├── server/
│   ├── __init__.py
│   ├── __main__.py      # python -m server
│   ├── server.py         # GameServer (main loop + networking)
│   ├── lobby.py          # Lobby + Party (несколько игровых сессий)
│   ├── game_world.py     # World state, map, entities
│   ├── player.py         # PlayerSession
│   └── network.py        # UDP socket, packet handling
├── client/
│   ├── __init__.py
│   ├── __main__.py       # python -m client
│   ├── client.py         # GameClient (state machine lobby→game)
│   ├── lobby_screen.py   # Меню: список партий
│   ├── raycaster.py      # DDA raycasting engine
│   ├── renderer.py       # Pygame rendering
│   ├── input_handler.py  # Mouse + keyboard
│   ├── network_client.py # UDP client
│   └── assets.py         # Texture loading
├── shared/
│   ├── __init__.py
│   ├── protocol.py       # Packet types, serialization
│   ├── components.py     # ECS components
│   └── map_data.py       # Map loading/saving
├── assets/
│   ├── textures/         # Wall textures (placeholder)
│   └── maps/             # Map files (.json)
├── requirements.txt
├── run_server.py
├── run_client.py
└── SPEC.md
```

## Запуск

> На этой машине pygame установлен для Python 3.12, поэтому везде используется `py -3.12`.

```bash
# Сервер
run_server.bat          # или: py -3.12 run_server.py --port 7777

# Клиент
run_client.bat          # или: py -3.12 run_client.py --name "Player1"

# Второй игрок (проверка мультиплеера и меню)
run_client.bat --name "Player2"
```

Зависимости: `py -3.12 -m pip install pygame`
