# Tasks: устранение бесконечного предложения обновления

## 1. Единый компаратор версий

- [x] 1.1 В `shared/releases.py` добавить `parse_version(version) -> tuple[int, ...]` и `is_newer(candidate, base) -> bool`; нечисловые/битые версии — `False`. Проверка: юнит-тест `1.0.10 > 1.0.9`, `1.0.2 == 1.0.2` → не новее, `abc`/пусто → не новее.
- [x] 1.2 Перевести `tools/launch_client.py` (`_components`/`newer`) на `shared.releases.is_newer`. Проверка: существующий тест выбора артефакта (`smoke_launch.py`) проходит.

## 2. Сервер: строго-новее

- [x] 2.1 В `server/server.py` `_on_hello` заменить `client_version != release["version"]` на `is_newer(release["version"], client_version)`. Проверка: юнит-тест — клиент `1.0.2` при релизе `1.0.2` → `has_update=False`; клиент `1.0.0` при релизе `1.0.2` → `has_update=True`; клиент `1.0.3` при релизе `1.0.2` → `has_update=False`.

## 3. Сборка: синхронизация исходной версии

- [x] 3.1 В `tools/build_client.py` при `--set-latest` перезаписать `CLIENT_VERSION` в исходном `shared/version.py` (сохранив `PROTOCOL_VERSION`). Проверка: сборка в temp-каталог с `--version 9.9.9 --set-latest` обновляет исходный файл; без `--set-latest` — не трогает.
- [x] 3.2 Добавить флаг `--no-source-version` для отключения синхронизации (сборка без изменения исходников). Проверка: с флагом исходный `shared/version.py` не меняется.

## 4. Клиент: память об отклонённой версии

- [x] 4.1 В `client/settings.py` добавить ключ `skipped_update_version` (строка, по умолчанию пусто) в `DEFAULTS`. Проверка: сохранение/загрузка через `config.json`.
- [x] 4.2 В `client/client.py` `_maybe_update` не показывать диалог, если `offer.version == settings.get("skipped_update_version")`; при отказе в `_confirm_update` сохранять `offer.version` и `settings.save()`. Проверка: headless-сценарий — отказ, повторный вызов не показывает диалог; релиз новее сохранённого — показывает.

## 5. Проверка

- [x] 5.1 Новый `smoke_update_prompt.py`: компаратор, решение `_on_hello` (мок `ReleaseStore`), память о пропуске, синхронизация исходной версии сборкой. PASS.
- [x] 5.2 Регрессия: `smoke_reliability.py`, `smoke_launch.py` — PASS; `py_compile` изменённых модулей.
- [x] 5.3 `openspec validate client-update-prompt-loop` — PASS.
