## 1. Порядконезависимая окклюзия сплошных стен

- [ ] 1.1 Завести в `render_scene()` (`engine/sector_render.py`) массив `solid_depth = [INF] * iw` и прокинуть его в `_seg_wall_bands()` рядом с `clip_top`/`clip_bot`. Проверить, что кадр строится: `py -3.12 smoke_sector_render.py` зелёный.
- [ ] 1.2 Переписать ветку сплошной стены (`not two`): колонка пропускается при `t >= solid_depth[col]`; иначе полоса рисуется в `[max(yc, clip_top[col]), min(yf, clip_bot[col])]`, затем `solid_depth[col] = t`; клип-массивы `clip_top`/`clip_bot` сплошной стеной больше НЕ закрываются. Проверить: `py -3.12 smoke_sector_render.py` зелёный, `test_solid_wall_and_step` проходит.
- [ ] 1.3 Ограничить публикацию флатов и `view_buf` условием `t < solid_depth[col]`; при установке `solid_depth[col]` выставлять `view_buf[col] = min(view_buf[col], t)`. Проверить: `py -3.12 smoke_sector_render.py` и `py -3.12 smoke_sector_flats.py` зелёные.
- [ ] 1.4 Заменить ранний выход «все колонки закрыты» на проверку `solid_depth[col] <= t`; обновить комментарий-инвариант в `_seg_wall_bands` (убрать неверное «walls drawn before the current seg are always nearer»). Проверить: `py -3.12 smoke_sector_render.py` зелёный.
- [ ] 1.5 Добавить гейт `t >= solid_depth[col]` в ветку двусторонней стены (ближняя сплошная стена скрывает проём и его кромки). Проверить: `py -3.12 smoke_sector_render.py` зелёный, проёмы на `_two_rooms` по-прежнему видны.

## 2. Клиппинг у ближней плоскости

- [ ] 2.1 В `_seg_wall_bands()` заменить отсечение по ближней плоскости `if t1 < 0.0` / `elif t2 < 0.0` на `<= 0.0`, сохранив ранний выход при обоих неположительных концах. Проверить отсутствие `ZeroDivisionError` при наблюдателе ровно в вершине (сценарий 3.2).

## 3. Регрессионные тесты

- [ ] 3.1 В `smoke_sector_render.py` добавить карту из нескольких subsector'ов (сетка комнат) и дифференциальный тест: свип углов и точек, зондирование перехваченных полос, сравнение минимальной нарисованной глубины колонки с эталонным ближайшим сплошным луча. Проверить: тест зелёный на исправленном коде; на `git stash` версии `engine/sector_render.py` он падает (свидетельство, что тест ловит дефект).
- [ ] 3.2 Добавить сценарий «наблюдатель ровно на вершине» (целые координаты, в т.ч. угол, при котором раньше был `ZeroDivisionError`): кадр строится без исключения. Проверить: `py -3.12 smoke_sector_render.py` зелёный.

## 4. Верификация

- [ ] 4.1 Прогнать весь smoke-набор (`Get-ChildItem smoke_*.py | ForEach-Object { py -3.12 $_.Name }`) и `py -3.12 tools/check_engine_imports.py`; все падения, кроме уже известных двух нарушений `check_engine_imports`, отсутствуют.
- [ ] 4.2 `openspec validate sector-near-wall-occlusion --strict` проходит; `py -3.12 smoke_sector_perf.py` не показывает регрессии бюджета кадра.
