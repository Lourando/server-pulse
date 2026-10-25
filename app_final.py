"""Server Pulse: сервис для мониторинга состояния сервера."""

import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Literal

import psutil
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query


APP_VERSION = "0.3.1"

# Подхватываем настройки из файла .env, если он есть.
# Переменные, уже заданные в системе, имеют приоритет над файлом
load_dotenv()

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
# Если задан LOG_FILE, лог пишется ещё и в этот файл (кроме консоли)
LOG_FILE = os.getenv("LOG_FILE")

log_handlers = [logging.StreamHandler()]
if LOG_FILE:
    log_handlers.append(logging.FileHandler(LOG_FILE, encoding="utf-8"))

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=log_handlers,
)
if LOG_FILE:
    # У uvicorn свои логгеры (старт сервера и строки запросов),
    # подключаем файл и к ним, чтобы в файле была полная картина
    for uvicorn_logger in ("uvicorn", "uvicorn.access"):
        logging.getLogger(uvicorn_logger).addHandler(log_handlers[-1])

logger = logging.getLogger("server_pulse")


def read_percent(name, default):
    """Читает порог в процентах из окружения. При ошибке берёт значение по умолчанию."""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        logger.warning("%s=%r не число, используется %s", name, raw, default)
        return default
    if not 0 < value <= 100:
        logger.warning("%s=%s вне диапазона 0-100, используется %s", name, value, default)
        return default
    return value


def read_list(name, default):
    """Читает список через запятую: "sshd, xray" -> ["sshd", "xray"]."""
    raw = os.getenv(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


APP_NAME = os.getenv("APP_NAME", "Server Pulse")
CPU_ALERT_PERCENT = read_percent("CPU_ALERT_PERCENT", 90)
RAM_ALERT_PERCENT = read_percent("RAM_ALERT_PERCENT", 85)
DISK_ALERT_PERCENT = read_percent("DISK_ALERT_PERCENT", 90)
WATCHED_PROCESSES = read_list("WATCHED_PROCESSES", "sshd")

# Выше этой температуры датчик считается горячим
TEMP_ALERT_CELSIUS = float(os.getenv("TEMP_ALERT_CELSIUS", "80"))
# Таких температур у работающего компьютера не бывает: датчик, скорее всего, врёт
SUSPICIOUS_HIGH_CELSIUS = 105
SUSPICIOUS_LOW_CELSIUS = -20
NUCLEAR_CELSIUS = 1000

# Корень системного диска: "/" на Linux, "C:\" (или другой диск) на Windows
DEFAULT_DISK_PATH = os.getenv("DISK_PATH", os.path.abspath(os.sep))


@asynccontextmanager
async def lifespan(app):
    # Код до yield выполняется при старте сервиса, после yield при остановке
    logger.info("%s %s запущен", APP_NAME, APP_VERSION)
    logger.info(
        "Пороги: CPU %s%%, RAM %s%%, диск %s%% (%s)",
        CPU_ALERT_PERCENT, RAM_ALERT_PERCENT, DISK_ALERT_PERCENT, DEFAULT_DISK_PATH,
    )
    logger.info("Отслеживаемые процессы: %s", ", ".join(WATCHED_PROCESSES) or "нет")
    yield
    logger.info("%s остановлен", APP_NAME)


app = FastAPI(
    title=APP_NAME,
    description="Мониторинг CPU, памяти, дисков и важных процессов сервера.",
    version=APP_VERSION,
    lifespan=lifespan,
)


@app.get("/")
def root():
    return {
        "service": APP_NAME,
        "message": "Сервис работает. Документация: /docs",
    }


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/version")
def version():
    return {"version": APP_VERSION}


def to_gb(size_in_bytes):
    """Переводит байты в гигабайты с округлением до сотых."""
    return round(size_in_bytes / 1024**3, 2)


@app.get("/metrics")
def metrics():
    """Общее состояние: процессор, память и время работы системы."""
    memory = psutil.virtual_memory()
    uptime_seconds = int(time.time() - psutil.boot_time())

    return {
        "cpu_percent": psutil.cpu_percent(interval=0.5),
        "cpu_cores": psutil.cpu_count(),
        "memory_percent": memory.percent,
        "memory_used_gb": to_gb(memory.used),
        "memory_total_gb": to_gb(memory.total),
        "uptime_hours": round(uptime_seconds / 3600, 1),
    }


@app.get("/disk")
def disk(path: str = DEFAULT_DISK_PATH):
    """Заполненность диска, на котором лежит указанный путь."""
    try:
        usage = psutil.disk_usage(path)
    except OSError:
        logger.warning("Запрошен несуществующий путь: %s", path)
        raise HTTPException(status_code=404, detail=f"Путь не найден: {path}")

    return {
        "path": path,
        "used_percent": usage.percent,
        "used_gb": to_gb(usage.used),
        "free_gb": to_gb(usage.free),
        "total_gb": to_gb(usage.total),
    }


def normalize_name(name):
    """Приводит имя процесса к общему виду: "Python.exe" -> "python"."""
    name = name.lower()
    if name.endswith(".exe"):
        name = name[:-4]
    return name


def find_processes(name):
    """Возвращает список PID процессов с таким именем."""
    wanted = normalize_name(name)
    pids = []
    for proc in psutil.process_iter(["pid", "name"]):
        if proc.info["name"] and normalize_name(proc.info["name"]) == wanted:
            pids.append(proc.info["pid"])
    return pids


@app.get("/processes/top")
def top_processes(
    limit: int = Query(5, ge=1, le=50),
    sort: Literal["memory", "cpu"] = "memory",
):
    """Самые «тяжёлые» процессы по памяти или по процессору."""
    # PID 0 на Windows это «System Idle Process»: не программа, а простой процессора
    procs = [p for p in psutil.process_iter(["pid", "name", "memory_info"]) if p.pid != 0]
    cores = psutil.cpu_count() or 1

    if sort == "cpu":
        # Загрузку CPU процессом нельзя измерить мгновенно:
        # первый вызов запускает замер, второй через паузу возвращает результат
        for proc in procs:
            try:
                proc.cpu_percent(None)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        time.sleep(0.5)

    result = []
    for proc in procs:
        try:
            memory_info = proc.info["memory_info"]
            result.append({
                "pid": proc.info["pid"],
                "name": proc.info["name"],
                "memory_mb": round(memory_info.rss / 1024**2, 1) if memory_info else 0,
                # psutil считает проценты от одного ядра (бывает 300%),
                # делим на число ядер, чтобы получить долю от всего процессора
                "cpu_percent": round(proc.cpu_percent(None) / cores, 1) if sort == "cpu" else None,
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            # Процесс успел завершиться или система не даёт к нему доступа
            continue

    sort_key = "memory_mb" if sort == "memory" else "cpu_percent"
    result.sort(key=lambda item: item[sort_key], reverse=True)
    return result[:limit]


@app.get("/process/{name}")
def process_status(name: str):
    """Запущен ли процесс с указанным именем."""
    pids = find_processes(name)
    return {"name": name, "running": len(pids) > 0, "pids": pids}


@app.get("/watch")
def watch():
    """Проверяет все важные процессы из настройки WATCHED_PROCESSES."""
    statuses = []
    for name in WATCHED_PROCESSES:
        statuses.append({"name": name, "running": len(find_processes(name)) > 0})

    all_running = all(item["running"] for item in statuses)
    return {"all_running": all_running, "processes": statuses}


def build_alerts(cpu_percent, memory_percent, disk_percent, missing_processes):
    """Сравнивает показания с порогами и возвращает список проблем.

    Функция ничего не измеряет сама, только принимает числа.
    Поэтому её легко проверить тестом на любых, даже выдуманных, значениях.
    """
    alerts = []
    if cpu_percent >= CPU_ALERT_PERCENT:
        alerts.append({
            "type": "cpu",
            "message": f"CPU {cpu_percent}% (порог {CPU_ALERT_PERCENT}%)",
        })
    if memory_percent >= RAM_ALERT_PERCENT:
        alerts.append({
            "type": "memory",
            "message": f"Память {memory_percent}% (порог {RAM_ALERT_PERCENT}%)",
        })
    if disk_percent >= DISK_ALERT_PERCENT:
        alerts.append({
            "type": "disk",
            "message": f"Диск {disk_percent}% (порог {DISK_ALERT_PERCENT}%)",
        })
    for name in missing_processes:
        alerts.append({
            "type": "process",
            "message": f"Процесс {name} не запущен",
        })
    return alerts


@app.get("/alerts")
def alerts():
    """Собирает текущие показания и сообщает, что из них выходит за пороги."""
    missing = [name for name in WATCHED_PROCESSES if not find_processes(name)]
    found = build_alerts(
        cpu_percent=psutil.cpu_percent(interval=0.5),
        memory_percent=psutil.virtual_memory().percent,
        disk_percent=psutil.disk_usage(DEFAULT_DISK_PATH).percent,
        missing_processes=missing,
    )

    for alert in found:
        logger.warning("ALERT %s: %s", alert["type"], alert["message"])
    if not found:
        logger.info("Проверка порогов: всё в норме")

    return {"ok": len(found) == 0, "alerts": found}


def classify_temperature(celsius):
    """Оценивает показание датчика: ok, hot или suspicious, с комментарием.

    Как и build_alerts, функция работает только с числом,
    поэтому проверяется тестами на любых значениях.
    """
    if celsius >= NUCLEAR_CELSIUS:
        return "suspicious", (
            "Горячее лавы. Раз вы читаете этот ответ, ядерного взрыва "
            "не случилось: датчик врёт."
        )
    if celsius >= SUSPICIOUS_HIGH_CELSIUS:
        return "suspicious", (
            "По данным датчика, плата горит. Пожарные не приехали, дыма нет: "
            "скорее всего, датчик не подключён."
        )
    if celsius <= SUSPICIOUS_LOW_CELSIUS:
        return "suspicious", (
            "Холоднее, чем зимой в Якутске. Скорее всего, датчик не подключён."
        )
    if celsius >= TEMP_ALERT_CELSIUS:
        return "hot", "Горячо. Стоит проверить охлаждение и нагрузку."
    return "ok", "Температура в норме."


def read_temperatures():
    """Читает датчики температуры. Возвращает None, если система их не отдаёт.

    psutil умеет читать температуры только на Linux. На Windows и macOS
    этой функции нет, а на виртуальном сервере датчиков обычно нет вовсе.
    """
    if not hasattr(psutil, "sensors_temperatures"):
        return None
    sensors = psutil.sensors_temperatures()
    if not sensors:
        return None

    readings = []
    for chip, entries in sensors.items():
        for index, entry in enumerate(entries):
            readings.append({
                "sensor": f"{chip}/{entry.label or index}",
                "celsius": entry.current,
            })
    return readings


@app.get("/temperature")
def temperature():
    """Температуры датчиков с проверкой на правдоподобность."""
    readings = read_temperatures()
    if readings is None:
        logger.info("Датчики температуры недоступны на этой системе")
        return {
            "available": False,
            "message": "Датчики температуры недоступны на этой системе",
            "sensors": [],
        }

    sensors = []
    for reading in readings:
        status, comment = classify_temperature(reading["celsius"])
        sensors.append({**reading, "status": status, "comment": comment})
        if status != "ok":
            logger.warning(
                "Датчик %s: %s °C (%s)", reading["sensor"], reading["celsius"], status
            )

    return {"available": True, "sensors": sensors}
