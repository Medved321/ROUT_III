# Tasks

## 1. `engine/viewport.py` — владелец отображения

- [x] 1.1 Новый модуль. `REF_W, REF_H = 1000, 700`, `ui_scale()`
      (`min(const.W/REF_W, const.H/REF_H)`), `ui_px(units)` (квантование до
      целых, `max(1, ...)`). `W`/`H`/`FOV` читаются в момент вызова.
- [x] 1.2 `ensure_hud_fonts()`: `gs.font`/`gs.sm_font` строятся как
      `SysFont(round(34*s), bold)` / `SysFont(round(18*s))`, кэш из **одной**
      пары с пересборкой только при смене квантованного размера.
- [x] 1.3 `project_point(x, y, world_h, vh=None, vy=None) -> (sx, sh, ground_y, perp) | None`.
      Секторный режим делегирует `sector_render.project_object` (ленивый импорт,
      как уже сделано в `rendering.py:1070`); тайловый путь считает фокусом
      `750` и высотой глаза `CAM_Z` и возвращает `perp = dst*cos(ang)`. Конус
      отбора `abs(ang) < 1.0` — общий со спрайтами.
- [x] 1.4 Проверка: `ui_scale()` даёт 1.0 на 1000×700, 3.086 на 3840×2160,
      0.286 на 320×200; `project_point` на 1000×700 для точки в двух тайлах
      прямо по курсу даёт `ground_y`, отличающийся от линии пола спрайтного
      прохода (`_RH//2 + CAM_Z*sh/TILE - sh/4`) не более чем на 1 px.

## 2. HUD и оверлеи масштабируются

- [x] 2.1 `engine/rendering.py`: вызов `viewport.ensure_hud_fonts()` первым в
      `render()`.
- [x] 2.2 Блок `# ── HUD ──` (1660–1919): все литералы (`hud_bh = 100`,
      `face_sz = 52`, `lx = 12`, `rx = const.W - 120`, `abw, abh = 90, 4`,
      `slot_w = 30`, `kx += 28`, `face_rect.centerx - 15 / - 45`,
      строки лица 1701–1794) переводятся в единицы вёрстки через `ui_px()`.
      Поля и привязки остаются к `const.W` / `_RY + _RH`.
- [x] 2.3 Центрирование подписи под лицом измеряет саму метку
      (`rect.width // 2` вместо `- 15` / `- 45`).
- [x] 2.4 Прицел: `ch_x, ch_y` остаются центром экрана, длина луча и толщина
      идут через `ui_px()`.
- [x] 2.5 Мини-карта (`ms = 5`, точка игрока), таймер спидран
      (`(const.W - 130, 85)`), таблица DM (`yy += 14`, якоря `410` / `20, 10`),
      индикатор реплея и полосы дэша — тем же коэффициентом.
- [x] 2.6 Попутно: `engine/rendering.py:1645` печатает игроку
      `const.W:{...}` вместо `WPN:` (артефакт замены) — исправить.
- [x] 2.7 `client/bridge.py`: убрать создание «сырых» `SysFont(34)`/`(18)` —
      размер шрифта принадлежит движку.
- [x] 2.8 Проверка: доля `hud_bh` в высоте окна в пределах 13–15 % при
      640×480, 1000×700, 1920×1080, 3840×2160; при 320×200 полоса не
      перекрывает `const.H // 2`; шрифты не пересоздаются при изменении окна в
      пределах одного кванта.

## 3. Модель оружия

- [x] 3.1 `engine/rendering.py`: `_load_pistol`, `_load_rifle`, `_load_shotgun`,
      `_load_chainsaw`, `_get_ssg` кладут в кэш **родной** размер спрайта
      (без `transform.scale`).
- [x] 3.2 `WPN_TARGET_H` — целевая высота в единицах вёрстки на вид оружия
      (CHAINSAW/PISTOL/SHOTGUN/RIFLE/BFG/SSG).
- [x] 3.3 `draw_wpn()`: приведение к `ui_px(WPN_TARGET_H[wn])` с сохранением
      родных пропорций; кэш `_wpn_fitted` по
      `(wpn, кадр, квантованная целевая высота)` с ограничением размера кэша.
- [x] 3.4 Якорь: общий нижний отступ `ui_px(30)` для всех видов оружия, включая
      SSG (сейчас у него 0 px — переключение подпрыгивает). `rcl` и `bob`
      домножаются на коэффициент; `shake` бензопилы — тоже.
- [x] 3.5 BFG: примитивы (`200×220`, радиусы `50 / 28 / 12`, смещения) через
      масштаб; `wx`/`wy` — общий якорь.
- [x] 3.6 Кастомное оружие: ветка спрайта руки (своя схема
      `min(const.W*0.5/w, const.H*0.4/h, 4)`), вокселя (`scale_f = 6`) и
      заглушки (`sz = 80`) переводятся на `ui_px()`.
- [x] 3.7 Проверка: для каждого вида оружия видимая доля высоты экрана в
      диапазоне при 640×480 / 1000×700 / 3840×2160; модель не выходит за
      границы экрана ни в одном окне; повторный вызов в том же окне попадает в
      кэш (счётчик `transform.scale` не растёт); отступ от нижнего края одинаков
      у пистолета и SSG.

## 4. Частицы и декали

- [x] 4.1 `engine/particles.py`: `draw_particles()` и `draw_decals()` теряют
      собственные `focal`, `pitch_off`, `TILE*focal/dst` и `EYE_H`, зовут
      `viewport.project_point()`.
- [x] 4.2 Вертикаль: `base_y = int(ground_y - z * sh / TILE)` — одна формула
      для тайлов и секторов, знак исправлен (сейчас эффекты уходят на 350 px
      ниже пола).
- [x] 4.3 Тест глубины сравнивает `z_b[idx] > perp` (перпендикулярная метрика)
      вместо радиального `dst`.
- [x] 4.4 Конус отбора — общий `1.0`; частицы получают отсев по
      `gs.view_dist`, который уже есть у декалей.
- [x] 4.5 `gs.pitch` уходит из проекции эффектов: мир его не учитывает
      (`bridge.py:254` жёстко ставит `0.0`, в строках 949/1088 члена с
      `pitch` нет), поэтому частицы были единственным его потребителем.
- [x] 4.6 Размер частицы `sz` считается от `sh` общей проекции, поэтому
      согласован с размером спрайта на том же расстоянии.
- [x] 4.7 Проверка: частица в 2 тайлах на `z = 1.5` совпадает с полом,
      посчитанным миром, в пределах 1 px; при стене на расстоянии больше
      перпендикулярного (но меньше радиального) частица видна; частица дальше
      `gs.view_dist` не рисуется; кейс `test_particles_use_the_same_grid` в
      `smoke_depth_buffer.py` зелёный. Секторный пол не дублируется здесь:
      `project_point` в секторном режиме **делегирует** `project_object`, чей
      пол уже проверяет `smoke_sector_render.py`.

## 5. Тесты и регресс

- [x] 5.1 Новый `smoke_viewport.py`: доля HUD в окне, квантование и
      пересборка шрифтов, доля и вписывание модели оружия, попадание в кэш
      оружия, совпадение проекции частицы с миром, перпендикулярная метрика,
      ближняя плоскость, отсев по `view_dist`, отсутствие `transform.scale` в
      устоявшемся окне и ограниченность кэшей после свипа ресайзов.
- [x] 5.2 `py -3.12 smoke_viewport.py`, `smoke_depth_buffer.py`,
      `smoke_weapon_anim.py`, `smoke_dash_fx.py`, `smoke_player_dash.py`,
      `smoke_resolution_controls.py`, `smoke_menu_layout.py`,
      `smoke_sector_render.py`, `smoke_sprites.py`, `smoke_tiers.py`,
      `smoke_perf.py`, `smoke_combat.py`, `smoke_integration_combat.py`,
      `smoke_projectiles.py`, `smoke_hitscan.py`, `smoke_content_providers.py`,
      `smoke_framebuffer_tile.py`, `smoke_np_flats.py`, `smoke_sector_flats.py`,
      `smoke_wallcache.py`, `smoke_enemy_models.py`, `smoke_framebuffer.py`,
      `smoke_bsp_render.py`, `smoke_engine_standalone.py`, `smoke_ui.py`,
      `smoke_config.py`, `smoke_mt1_format.py`, `smoke_mt1_party.py`,
      `smoke_mt1_server.py`, `smoke_sector_server.py`, `smoke_sector_model.py`,
      `smoke_sector_perf.py`, `smoke_custom_map.py`, `smoke_text_map.py`,
      `smoke_infighting.py`, `smoke_reliability.py`, `smoke_update_prompt.py`,
      `smoke_sector_editor.py`, `smoke_sector_editor_screen.py`,
      `smoke_linux_launchers.py`, `smoke_launchers.py`, `smoke_numpy_bootstrap.py`,
      `tools/check_engine_imports.py`. Проверка: `TOTAL: 44  FAILED: 1`, где
      единственное падение — `smoke_projectiles.py`; остальные зелёные.
      Запускать с `PYTHONIOENCODING=utf-8`: cp1251-консоль роняет тесты с
      `×`/`→` в выводе.
- [x] 5.3 `openspec validate viewport-scaling --strict`. Проверка: valid.

### Известное падение, не связанное с этим изменением

`smoke_projectiles.py` падает и на чистом `HEAD` (`56a2abc`) — проверено в
отдельном worktree, где от меня не было ни одного файла:

- `test_bridge_particles_and_render`: «frame with player ball identical to frame
  without» — шар рисуется в спрайтном проходе, которого изменение не касалось;
- `run_live`: «server/client A never saw a player ball» — нестабильный живой
  UDP-прогон; при первом падении порт остаётся занят, и все следующие прогоны в
  том же процессе падают на «connect failed».

Ни то, ни другое не воспроизводится как регрессия `viewport-scaling`, поэтому
чинить здесь нечего.

## 6. Документация

- [x] 6.1 `AGENTS.md`: новый раздел «Экранный слой и проекция мира» — владелец
      `engine/viewport.py`, почему HUD и оружие объявляются в единицах
      вёрстки, почему частицы обязаны брать проекцию спрайтов, и правило
      «`perp`, а не радиальное расстояние, в тесте глубины».
- [x] 6.2 Отметить, что `tools/check_engine_imports.py` теперь видит
      `engine/viewport.py` (новый модуль в границе движка).

## 7. Открытые вопросы

- [ ] 7.1 Tile sprite pass по-прежнему измеряет глубину **радиальным** `dst`,
      тогда как `project_point()` отдаёт перпендикулярный `perp`. На прямо по
      курсу это одно и то же (проверено), вне оси — расхождение, и эффект под
      ногами проверяется по другой метрике, чем спрайт в той же точке. В
      скоуп это не входило; нужно либо перевести sprite pass на `perp` (и
      обновить спеку), либо зафиксировать как явный non-goal в `design.md`.
