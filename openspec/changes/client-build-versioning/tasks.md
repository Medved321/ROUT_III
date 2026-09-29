## 1. Сборка со штампом версии

- [x] 1.1 В `tools/build_client.py`: после `copytree` записать в `staging/shared/version.py` `CLIENT_VERSION = \"<version>\"` (из `--version`) и `PROTOCOL_VERSION` из исходников; проверка: собрать артефакт и проверить `shared/version.py` внутри zipapp (protocol = исходный, client = версия)
- [x] 1.2 Проверить, что обычная сборка из исходников не затрагивается (`py_compile` для `tools/build_client.py` и `run_client.py`)

## 2. Валидация версий артефактов

- [x] 2.1 Собрать `--version 1.0.2 --set-latest` и убедиться, что `releases/latest.txt` = 1.0.2, а внутри артефакта `CLIENT_VERSION="1.0.2"`, `PROTOCOL_VERSION=3`
- [x] 2.2 HELLO-регрессия: клиент (артефакт 1.0.2) подключается к серверу (protocol 3, latest 1.0.2) и НЕ получает `has_update` — проверить по логу сервера (нет "Доступно обновление")