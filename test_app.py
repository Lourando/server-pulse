"""Автоматические тесты Server Pulse. Запуск: python -m pytest -v"""

import logging
from types import SimpleNamespace

import psutil
import pytest
from fastapi.testclient import TestClient

import app_final
from app_final import app


# TestClient отправляет запросы прямо в приложение, без запуска сервера
client = TestClient(app)


# --- Базовые маршруты ---

def test_health():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_version():
    response = client.get("/version")

    assert response.status_code == 200
    assert response.json() == {"version": app_final.APP_VERSION}


def test_root_returns_dashboard_page():
    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    # Панель берёт данные из /summary, без него она пустая
    assert "/summary" in response.text


# --- Метрики и диск ---
# Реальные цифры на каждой машине свои, поэтому проверяем форму ответа:
# нужные поля на месте, проценты в диапазоне 0-100

def test_metrics_structure():
    response = client.get("/metrics")
    data = response.json()

    assert response.status_code == 200
    assert 0 <= data["cpu_percent"] <= 100
    assert 0 <= data["memory_percent"] <= 100
    assert data["memory_total_gb"] > 0
    assert data["cpu_cores"] >= 1


def test_disk_default_path():
    response = client.get("/disk")
    data = response.json()

    assert response.status_code == 200
    assert 0 <= data["used_percent"] <= 100
    assert data["total_gb"] > 0


def test_disk_not_found(tmp_path):
    # tmp_path это временная папка pytest; папки missing внутри неё точно нет
    missing = tmp_path / "missing"

    response = client.get("/disk", params={"path": str(missing)})

    assert response.status_code == 404
    assert "Путь не найден" in response.json()["detail"]


# --- Процессы ---

def test_top_processes_respects_limit():
    response = client.get("/processes/top", params={"limit": 3})

    assert response.status_code == 200
    assert len(response.json()) <= 3


def test_top_processes_sorted_by_memory():
    memory = [p["memory_mb"] for p in client.get("/processes/top").json()]

    assert memory == sorted(memory, reverse=True)


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 100}, {"sort": "disk"}])
def test_top_processes_invalid_params(params):
    # Один тест, три запуска: по разу на каждый неверный параметр
    response = client.get("/processes/top", params=params)

    assert response.status_code == 422


def test_process_running():
    # Процесс, который точно запущен: сам pytest, то есть текущий python
    current = psutil.Process()

    response = client.get(f"/process/{current.name()}")
    data = response.json()

    assert data["running"] is True
    assert current.pid in data["pids"]


def test_process_not_running():
    response = client.get("/process/no-such-process-12345")

    assert response.json() == {
        "name": "no-such-process-12345",
        "running": False,
        "pids": [],
    }


def test_watch_reports_missing_process(monkeypatch):
    current_name = psutil.Process().name()
    monkeypatch.setattr(app_final, "WATCHED_PROCESSES", [current_name, "no-such-process-12345"])

    data = client.get("/watch").json()

    assert data["all_running"] is False
    assert data["processes"] == [
        {"name": current_name, "running": True},
        {"name": "no-such-process-12345", "running": False},
    ]


# --- Тревоги ---
# build_alerts ничего не измеряет, поэтому даём ей выдуманные показания

@pytest.fixture
def thresholds(monkeypatch):
    """Фиксированные пороги, чтобы тесты не зависели от чьего-то .env."""
    monkeypatch.setattr(app_final, "CPU_ALERT_PERCENT", 90)
    monkeypatch.setattr(app_final, "RAM_ALERT_PERCENT", 85)
    monkeypatch.setattr(app_final, "DISK_ALERT_PERCENT", 90)


def test_build_alerts_all_ok(thresholds):
    assert app_final.build_alerts(10, 20, 30, []) == []


def test_build_alerts_detects_problems(thresholds):
    alerts = app_final.build_alerts(95, 20, 99, ["xray"])

    assert [alert["type"] for alert in alerts] == ["cpu", "disk", "process"]
    assert "xray" in alerts[2]["message"]


def test_alerts_endpoint_with_fake_metrics(thresholds, monkeypatch):
    # Подменяем psutil внутри приложения: пусть память «забита» на 99%
    monkeypatch.setattr(app_final, "WATCHED_PROCESSES", [])
    monkeypatch.setattr(app_final.psutil, "cpu_percent", lambda interval: 5.0)
    monkeypatch.setattr(app_final.psutil, "virtual_memory", lambda: SimpleNamespace(percent=99.0))
    monkeypatch.setattr(app_final.psutil, "disk_usage", lambda path: SimpleNamespace(percent=40.0))

    data = client.get("/alerts").json()

    assert data["ok"] is False
    assert len(data["alerts"]) == 1
    assert data["alerts"][0]["type"] == "memory"


def test_alert_logged_only_when_it_appears_and_disappears(monkeypatch, caplog):
    # monkeypatch временно подменяет значение и сам вернёт его после теста
    monkeypatch.setattr(app_final, "active_alerts", {})
    caplog.set_level(logging.INFO, logger="server_pulse")
    disk_alert = app_final.build_alerts(10, 10, 99, [])

    app_final.log_alert_changes(disk_alert)
    app_final.log_alert_changes(disk_alert)  # та же проблема ещё раз
    app_final.log_alert_changes([])          # проблема ушла

    messages = [record.getMessage() for record in caplog.records]
    assert sum("ALERT disk" in m for m in messages) == 1
    assert sum("Проблема устранена" in m for m in messages) == 1


def make_access_record(path):
    """Строка лога в том виде, в каком её создаёт uvicorn для каждого запроса."""
    return logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 0,
        '%s - "%s %s HTTP/%s" %d', ("127.0.0.1:5000", "GET", path, "1.1", 200), None,
    )


def test_summary_requests_not_logged():
    log_filter = app_final.SkipSummaryRequests()

    assert log_filter.filter(make_access_record("/summary")) is False
    assert log_filter.filter(make_access_record("/alerts")) is True
    assert log_filter.filter(make_access_record("/health")) is True


# --- Сводка для панели ---

def test_summary_structure():
    response = client.get("/summary")
    data = response.json()

    assert response.status_code == 200
    assert data["version"] == app_final.APP_VERSION
    assert 0 <= data["cpu"]["percent"] <= 100
    assert 0 <= data["memory"]["percent"] <= 100
    assert 0 <= data["disk"]["percent"] <= 100
    assert len(data["processes"]) == len(app_final.WATCHED_PROCESSES)
    assert data["ok"] == (data["alerts"] == [])


def test_summary_with_fake_metrics(thresholds, monkeypatch):
    gb = 1024**3
    monkeypatch.setattr(app_final, "active_alerts", {})
    monkeypatch.setattr(app_final, "WATCHED_PROCESSES", ["no-such-process-12345"])
    monkeypatch.setattr(app_final.psutil, "cpu_percent", lambda interval: 12.0)
    monkeypatch.setattr(
        app_final.psutil, "virtual_memory",
        lambda: SimpleNamespace(percent=50.0, used=4 * gb, total=8 * gb),
    )
    monkeypatch.setattr(
        app_final.psutil, "disk_usage",
        lambda path: SimpleNamespace(percent=95.0, used=95 * gb, total=100 * gb),
    )

    data = client.get("/summary").json()

    assert data["memory"] == {"percent": 50.0, "used_gb": 4.0, "total_gb": 8.0}
    assert data["processes"] == [{"name": "no-such-process-12345", "running": False}]
    assert data["ok"] is False
    assert [alert["type"] for alert in data["alerts"]] == ["disk", "process"]


# --- Настройки из окружения ---

def test_read_percent_from_env(monkeypatch):
    monkeypatch.setenv("TEST_PERCENT", "75")

    assert app_final.read_percent("TEST_PERCENT", 90) == 75


@pytest.mark.parametrize("bad_value", ["abc", "150", "0", "-5"])
def test_read_percent_falls_back_on_bad_value(monkeypatch, bad_value):
    monkeypatch.setenv("TEST_PERCENT", bad_value)

    assert app_final.read_percent("TEST_PERCENT", 90) == 90


def test_read_list_strips_spaces(monkeypatch):
    monkeypatch.setenv("TEST_LIST", " sshd , xray,, ")

    assert app_final.read_list("TEST_LIST", "") == ["sshd", "xray"]


# --- Температура ---

@pytest.mark.parametrize(
    "celsius, expected_status",
    [
        (49, "ok"),            # CPUTIN с реального скриншота: норма
        (85, "hot"),           # горячо, но правдоподобно
        (110, "suspicious"),   # SYSTIN с реального скриншота: неподключённый датчик
        (-128, "suspicious"),  # тоже типичное значение пустого входа
    ],
)
def test_classify_temperature(monkeypatch, celsius, expected_status):
    monkeypatch.setattr(app_final, "TEMP_ALERT_CELSIUS", 80)

    status, comment = app_final.classify_temperature(celsius)

    assert status == expected_status
    assert comment


def test_classify_temperature_nuclear_easter_egg():
    status, comment = app_final.classify_temperature(5000)

    assert status == "suspicious"
    assert "ядерного взрыва" in comment


def test_temperature_unavailable(monkeypatch):
    # Так ведёт себя Windows или виртуальный сервер без датчиков
    monkeypatch.setattr(app_final, "read_temperatures", lambda: None)

    data = client.get("/temperature").json()

    assert data["available"] is False
    assert data["sensors"] == []


def test_temperature_with_fake_sensors(monkeypatch):
    fake = [
        {"sensor": "nct6798/SYSTIN", "celsius": 110},
        {"sensor": "nct6798/CPUTIN", "celsius": 49},
    ]
    monkeypatch.setattr(app_final, "read_temperatures", lambda: fake)
    monkeypatch.setattr(app_final, "TEMP_ALERT_CELSIUS", 80)

    data = client.get("/temperature").json()

    assert data["available"] is True
    assert [s["status"] for s in data["sensors"]] == ["suspicious", "ok"]
