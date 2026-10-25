"""Server Pulse: сервис для мониторинга состояния сервера."""

from fastapi import FastAPI


APP_VERSION = "0.1.0"

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
