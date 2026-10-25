"""Server Pulse: сервис для мониторинга состояния сервера."""

import os
import time

import psutil
from fastapi import FastAPI, HTTPException


APP_VERSION = "0.1.0"

# Корень системного диска: "/" на Linux, "C:\" (или другой диск) на Windows
DEFAULT_DISK_PATH = os.path.abspath(os.sep)

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
