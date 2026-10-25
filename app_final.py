"""Server Pulse: сервис для мониторинга состояния сервера."""

import os
import time
from typing import Literal

import psutil
from fastapi import FastAPI, HTTPException, Query


APP_VERSION = "0.1.0"

# Корень системного диска: "/" на Linux, "C:\" (или другой диск) на Windows
DEFAULT_DISK_PATH = os.path.abspath(os.sep)

# Процессы, за которыми следит /watch. На шаге 5 список переедет в .env
WATCHED_PROCESSES = ["python", "sshd"]

app = FastAPI(
    title="Server Pulse",
    description="Мониторинг CPU, памяти, дисков и важных процессов сервера.",
    version=APP_VERSION,
)


@app.get("/")
def root():
    return {
        "service": "Server Pulse",
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
    """Проверяет все важные процессы из списка WATCHED_PROCESSES."""
    statuses = []
    for name in WATCHED_PROCESSES:
        statuses.append({"name": name, "running": len(find_processes(name)) > 0})

    all_running = all(item["running"] for item in statuses)
    return {"all_running": all_running, "processes": statuses}
