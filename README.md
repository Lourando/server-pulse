# Server Pulse

[![CI](https://github.com/Lourando/server-pulse/actions/workflows/ci.yml/badge.svg)](https://github.com/Lourando/server-pulse/actions/workflows/ci.yml)

Небольшой сервис на FastAPI для мониторинга сервера. Он показывает загрузку
процессора, памяти и дисков, находит самые «тяжёлые» процессы, проверяет, что
важные программы (например, VPN или SSH) запущены, и пишет в лог предупреждения,
когда показатели выходят за пороги.

**Для кого:** для владельца небольшого VPS или домашнего компьютера, которому
нужно одним запросом узнать, всё ли в порядке: не кончается ли место на диске,
не забита ли память, жив ли VPN. Заходить по SSH и запускать `top` или `df`
для этого не нужно.

Сервис работает на Linux и Windows. CI проверяет его на обеих системах.

## Возможности

| Адрес | Что возвращает |
|---|---|
| `GET /` | название сервиса и ссылку на документацию |
| `GET /health` | `{"status": "ok"}`, если сервис жив |
| `GET /version` | версию приложения |
| `GET /metrics` | загрузку CPU, использование памяти, время работы системы |
| `GET /disk?path=/` | заполненность диска; для несуществующего пути `404` |
| `GET /processes/top?limit=5&sort=memory` | самые тяжёлые процессы по памяти (`memory`) или процессору (`cpu`) |
| `GET /process/{name}` | запущен ли процесс с таким именем и его PID |
| `GET /watch` | состояние всех процессов из настройки `WATCHED_PROCESSES` |
| `GET /alerts` | список проблем: превышены пороги или не запущены важные процессы |
| `GET /temperature` | температуры датчиков со статусом `ok` / `hot` / `suspicious` |

Интерактивная документация со всеми адресами открывается на `/docs`.

### Проверка датчиков на правдоподобность

Материнские платы часто показывают на неподключённых входах датчиков
«температуру» около 110 °C или −128 °C. Server Pulse не поднимает из-за этого
панику: такие значения получают статус `suspicious` и пояснение, что датчик,
скорее всего, не подключён. Значения для тестов взяты с реального компьютера:
`SYSTIN = 110 °C` (неподключённый вход) и `CPUTIN = 49 °C` (настоящий датчик).

psutil читает температуры только на Linux. На Windows и на виртуальных
серверах без датчиков `/temperature` отвечает `"available": false` и не падает.

## Установка

Нужен Python 3.11 или новее.

```bash
git clone https://github.com/Lourando/server-pulse.git
cd server-pulse
```

Создайте виртуальное окружение и установите зависимости.

**Linux / macOS:**

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

**Windows (PowerShell):**

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Для запуска тестов дополнительно установите зависимости разработки:

```bash
python -m pip install -r requirements-dev.txt
```

## Настройки

Настройки читаются из переменных окружения или из файла `.env` в папке
проекта. Скопируйте шаблон и отредактируйте его:

```bash
cp .env.example .env            # Linux / macOS
Copy-Item .env.example .env     # Windows PowerShell
```

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `APP_NAME` | `Server Pulse` | название сервиса в `/` и `/docs` |
| `LOG_LEVEL` | `INFO` | подробность логов: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `LOG_FILE` | не задан | файл, куда лог пишется дополнительно к консоли |
| `CPU_ALERT_PERCENT` | `90` | порог загрузки CPU для `/alerts` |
| `RAM_ALERT_PERCENT` | `85` | порог использования памяти |
| `DISK_ALERT_PERCENT` | `90` | порог заполненности диска |
| `DISK_PATH` | корень системы | диск для `/alerts` и значение по умолчанию для `/disk` |
| `WATCHED_PROCESSES` | `sshd` | процессы через запятую, которые должны быть запущены |
| `TEMP_ALERT_CELSIUS` | `80` | температура, выше которой датчик считается горячим |

Если порог задан с ошибкой (не число или вне диапазона 0–100), сервис не
падает: он пишет предупреждение в лог и использует значение по умолчанию.

Файл `.env` указан в `.gitignore` и в репозиторий не попадает. Секреты и
настройки конкретной машины хранятся только в нём.

## Запуск

Для обычной работы:

```bash
python -m uvicorn app_final:app --host 127.0.0.1 --port 8000
```

Для разработки, с автоматическим перезапуском при изменении кода:

```bash
python -m uvicorn app_final:app --reload
```

После запуска:

- сервис: http://127.0.0.1:8000
- документация: http://127.0.0.1:8000/docs

Изменения в `.env` применяются только после перезапуска сервиса.

## Тесты

```bash
python -m pytest -v
```

Тесты не зависят от конкретной машины. Реальные показатели проверяются по форме
(поля на месте, проценты в диапазоне 0–100). Тревоги и температуры проверяются
на подставленных данных через `monkeypatch`, поэтому сценарии «память 99%» или
«датчик показывает 110 °C» воспроизводятся на любом компьютере.

## CI

GitHub Actions ([.github/workflows/ci.yml](.github/workflows/ci.yml)) запускается
при каждом `push` и `pull request`:

1. **Tests** — устанавливает зависимости и запускает `pytest` параллельно на
   `ubuntu-latest` и `windows-latest`.
2. **Package application** — если тесты прошли на обеих системах, собирает
   artifact `server-pulse` с файлами приложения. Его можно скачать на странице
   запуска в разделе **Artifacts**.

## Диагностика

### Проверить, что сервис работает

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/version
```

Ожидаемые ответы: `{"status":"ok"}` и `{"version":"0.3.1"}`. В PowerShell
вместо `curl` можно использовать `Invoke-RestMethod http://127.0.0.1:8000/health`
или просто открыть адрес в браузере.

Если `/health` отвечает, а сервер ведёт себя странно, откройте `/alerts`: там
перечислены все текущие проблемы.

### Где смотреть логи

Логи выводятся в консоль, где запущен сервис. При старте видно, с какими
настройками он работает:

```
2026-10-25 03:44:36,450 INFO server_pulse: Server Pulse 0.3.1 запущен
2026-10-25 03:44:36,450 INFO server_pulse: Пороги: CPU 90.0%, RAM 85.0%, диск 90.0% (/)
2026-10-25 03:44:36,450 INFO server_pulse: Отслеживаемые процессы: sshd
```

Дальше в лог попадают:

- каждый запрос (строки `uvicorn`): адрес и код ответа;
- `WARNING ... ALERT` — превышен порог или не запущен важный процесс;
- `WARNING ... Запрошен несуществующий путь` — неверный запрос к `/disk`;
- `WARNING ... Датчик ...` — горячий или подозрительный датчик;
- `WARNING ... не число / вне диапазона` — ошибка в настройках.

Чтобы лог сохранялся ещё и в файл, задайте в `.env` строку `LOG_FILE=server.log`
и перезапустите сервис. Файл пишется в UTF-8, у каждой строки есть время,
включая строки запросов:

```
2026-10-25 03:44:40,672 WARNING server_pulse: Запрошен несуществующий путь: Q:/nope
2026-10-25 03:44:40,674 INFO uvicorn.access: 127.0.0.1:13919 - "GET /disk?path=Q:/nope HTTP/1.1" 404
```

Следить за файлом в реальном времени:

```bash
tail -f server.log                                  # Linux
Get-Content server.log -Wait -Encoding UTF8         # Windows PowerShell
```

Служебные сообщения uvicorn подписаны как `uvicorn.error`, даже если это не
ошибки: так uvicorn называет свой основной логгер.

Для подробного лога задайте `LOG_LEVEL=DEBUG`.

### Частые проблемы

| Симптом | Причина и решение |
|---|---|
| Браузер: «не удаётся установить соединение» | сервис не запущен или окно с ним закрыто; запустите и не закрывайте окно |
| `No module named uvicorn` / `pytest` | не активировано виртуальное окружение (нет `(.venv)` в начале строки) |
| PowerShell: «выполнение сценариев отключено» | `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned` |
| Порт 8000 занят | запустите на другом: `--port 8001` |
| Изменения в `.env` не применились | перезапустите сервис: `--reload` следит только за `.py` |
| `/watch` не видит процесс | на Windows имя пишется без `.exe`; проверьте точное имя через `/processes/top` |

## Развёртывание на сервере (Ubuntu + systemd)

Так сервис работает постоянно на VPS: запускается при загрузке, перезапускается
при сбое и пишет логи в системный журнал. Проверено на Ubuntu 22.04.

Принципы:

- сервис работает от отдельного пользователя `serverpulse` без прав root
  и без возможности входа в систему;
- код принадлежит root, сервис может его только читать;
- сервис слушает только `127.0.0.1` и из интернета не виден, а доступ
  к нему идёт через SSH-туннель. Файрвол менять не нужно.

### Установка

На Ubuntu 22.04 системный Python 3.10, а зависимостям нужен 3.11. Ставим 3.11
из официального репозитория рядом с системным, не заменяя его:

```bash
sudo apt update
sudo apt install -y python3.11 python3.11-venv git
```

Пользователь, код и окружение:

```bash
sudo useradd --system --no-create-home --shell /usr/sbin/nologin serverpulse
sudo git clone https://github.com/Lourando/server-pulse.git /opt/server-pulse
sudo python3.11 -m venv /opt/server-pulse/.venv
sudo /opt/server-pulse/.venv/bin/python -m pip install -r /opt/server-pulse/requirements.txt
```

Настройки (`/opt/server-pulse/.env`), пример для сервера с VPN Amnezia:

```bash
sudo cp /opt/server-pulse/.env.example /opt/server-pulse/.env
sudo nano /opt/server-pulse/.env      # WATCHED_PROCESSES=sshd,dockerd,amneziawg-go
sudo chown root:serverpulse /opt/server-pulse/.env
sudo chmod 640 /opt/server-pulse/.env
```

Служба:

```bash
sudo cp /opt/server-pulse/deploy/server-pulse.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now server-pulse
```

### Проверка и логи на сервере

```bash
systemctl status server-pulse              # состояние службы
curl http://127.0.0.1:8000/health          # сервис отвечает
curl http://127.0.0.1:8000/watch           # важные процессы запущены
journalctl -u server-pulse -n 50           # последние 50 строк лога
journalctl -u server-pulse -f              # лог в реальном времени
```

### Доступ с компьютера через SSH-туннель

```bash
ssh -L 8001:127.0.0.1:8000 root@IP_СЕРВЕРА
```

Пока это SSH-подключение открыто, сервис доступен на компьютере по адресу
http://127.0.0.1:8001/docs. Используется локальный порт 8001, чтобы не
конфликтовать с сервисом, запущенным локально на 8000.

### Обновление

```bash
cd /opt/server-pulse
sudo git pull
sudo .venv/bin/python -m pip install -r requirements.txt
sudo systemctl restart server-pulse
```

## Структура проекта

```
server-pulse/
├── app_final.py              # приложение
├── test_app.py               # автоматические тесты
├── requirements.txt          # зависимости приложения
├── requirements-dev.txt      # зависимости для тестов
├── .env.example              # шаблон настроек
├── .gitignore
├── README.md
├── deploy/server-pulse.service  # служба systemd для сервера
└── .github/workflows/ci.yml  # GitHub Actions
```

## Источник

За основу взят учебный пример `app_final.py` с занятия 4 курса «Инфраструктура
и администрирование» (маршруты `/`, `/health` и чтение настроек из окружения).
Тема, остальные маршруты, логика тревог и проверки датчиков, логирование,
тесты и CI написаны для этого проекта.
