# Tasks

## 1. Единый источник истины для сетки глубин

- [x] 1.1 `engine/framebuffer.py`: `depth_col(z_buf, sx, screen_w)` — номер
      колонки буфера по экранному столбцу, `-1` если колонки нет (пустой
      буфер, `sx` вне экрана, вырожденная ширина). Номер выводится из
      `len(z_buf)`, поэтому за границу выйти не может по построению.
      Модуль — общий для `rendering` и `particles` и не тянет зависимостей
      от них (иначе цикл: `rendering` импортирует `particles`).
- [x] 1.2 Проверка: на сетке длин `1..2000` и всех `sx` из `[-8, screen_w + 8]`
      результат всегда в `[-1, n-1]`, при `n > 0` и `sx` в экране — всегда
      `>= 0`, монотонен по `sx`.

## 2. Перевод потребителей на `depth_col`

- [x] 2.1 `engine/rendering.py:1087-1088` — основной проход спрайтов
      (враги, предметы, трупы, порталы, бочки, пули, снаряды). Крэш из
      трейсбака пользователя.
- [x] 2.2 `engine/rendering.py:1245-1246` — свечение глаз врага.
- [x] 2.3 `engine/rendering.py:1278-1279` — боты (deathmatch).
- [x] 2.4 `engine/rendering.py:1338-1339` — другие игроки (кооп).
- [x] 2.5 `engine/particles.py:242-243` — `draw_particles()`. Параметр `NC`
      (второй источник истины) убран; дефолт `vh` читает `const.H` в момент
      вызова, а не замораживается импортом.
- [x] 2.6 `engine/rendering.py:1442` и `smoke_perf.py:252` — вызовы под новую
      сигнатуру `draw_particles()`.

## 3. Геометрия колонки тумана

- [x] 3.1 `engine/rendering.py:1459` — полоса тумана строится через
      `_frame_column_span(i, len(z_b), const.W)`, а не как `i * SCALE` /
      `SCALE`. Тот же дефект в третьем месте: при секторном шаге полосы
      съезжали, а последние колонки уезжали за край поверхности.
- [x] 3.2 `engine/rendering.py:847` — `NC` переезжает внутрь тайловой ветки:
      после правки это исключительно тайловая величина.

## 4. Размороженные размеры в секторном рендере

- [x] 4.1 `engine/sector_render.py:25` — `W`/`H`/`FOV` больше не импортируются
      по значению, читаются как `const.W`/`const.H`/`const.FOV` в момент
      использования. По значению остаются `TILE`/`TEX_W`/`CAM_Z` — их никто
      не переприсваивает.
- [x] 4.2 Точки использования: `bsp_clipped_frame` (`iw = W // scale`, фон,
      `render_scene` (`iw = W // scale`, фон), `project_object` (`sx = ... * W`),
      `math.tan(FOV * 0.5)` в двух местах построения лучей.
- [x] 4.3 Проверка: ресайз 1000×700 → 1600×900 → 640×480 и смена FOV на
      секторной сцене не меняют длину буфера относительно `const.W` и не
      дают спрайт за правым краем; суженное окно залито целиком.

## 5. Тесты

- [x] 5.1 Новый `smoke_depth_buffer.py`:
      - регрессия крэша — прогон `render()` с секторной сценой по сетке
        `const.W × SCALE × gs.render_scale` (4×2×7 = 56 кадров), раньше падало
        20 строк, теперь все зелёные;
      - сетка: `depth_col` точен, безопасен и монотонен;
      - окклюзия попадает в свою колонку: спрайт у дальней стены скрыт, у
        ближней — виден, при `gs.render_scale != const.SCALE`;
      - ресайз окна и смена FOV на секторной сцене;
      - частицы: окклюзия по той же сетке, что и спрайты.
- [x] 5.2 `smoke_perf.py` — обновлён вызов `draw_particles()` под новую
      сигнатуру.

## 6. Документация

- [x] 6.1 `AGENTS.md` — в «Граница движка» / рендер: z-буфер как контракт,
      `depth_col()` как единственная точка перевода экранного столбца в
      колонку, и почему `W`/`H`/`FOV` в `sector_render` читаются динамически.
- [x] 6.2 `SPEC.md` — в разделе рендера отмечено, что длина колоночной сетки
      задаётся буфером, а не выводится из `SCALE`.

## 7. Регресс и валидация

- [x] 7.1 `py -3.12 smoke_depth_buffer.py`, `smoke_sector_render.py`,
      `smoke_framebuffer.py`, `smoke_framebuffer_tile.py`, `smoke_perf.py`,
      `smoke_dash_fx.py`, `smoke_sector_perf.py`, `smoke_sector_model.py`,
      `smoke_resolution_controls.py`, `smoke_content_providers.py`,
      `smoke_sprites.py`, `smoke_tiers.py`, `smoke_combat.py`,
      `smoke_hitscan.py`, `smoke_player_dash.py`, `smoke_projectiles.py`,
      `smoke_sector_server.py`, `smoke_integration_combat.py`,
      `smoke_infighting.py`, `smoke_weapon_anim.py`, `smoke_mt1_format.py`,
      `smoke_mt1_server.py`, `smoke_reliability.py`, `smoke_ui.py`,
      `smoke_menu_layout.py`, `smoke_update_prompt.py`, `smoke_config.py`,
      `smoke_custom_map.py`, `smoke_text_map.py`, `smoke_wallcache.py`,
      `smoke_np_flats.py`, `smoke_sector_flats.py`, `smoke_sector_editor.py`,
      `smoke_sector_editor_screen.py`, `smoke_engine_standalone.py`,
      `smoke_launch.py`, `smoke_launchers.py`, `smoke_numpy_bootstrap.py`.
      Проверка: зелёные.
- [x] 7.2 `py -3.12 tools/check_engine_imports.py` — новый импорт
      `particles` → `framebuffer` внутри границы движка. Проверка: valid.
- [x] 7.3 `openspec validate depth-buffer-column-grid --strict`. Проверка:
      valid.
- [x] 7.4 Отдельно: `smoke_projectiles.py` флейкует на живом UDP (известно) —
      зелёный со второго прогона.
