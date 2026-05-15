from __future__ import annotations

import json

import run_dashboard


def test_dashboard_launcher_prefers_runtime_port_and_address(monkeypatch, capsys) -> None:
    monkeypatch.setenv("PORT", "8080")
    monkeypatch.setenv("STREAMLIT_SERVER_ADDRESS", "0.0.0.0")
    monkeypatch.setattr(
        run_dashboard,
        "load_config",
        lambda _path: {"dashboard": {"host": "localhost", "port": 8501}},
    )

    assert run_dashboard.main([]) == 0

    command = json.loads(capsys.readouterr().out)["command"]
    assert command[command.index("--server.port") + 1] == "8080"
    assert command[command.index("--server.address") + 1] == "0.0.0.0"
