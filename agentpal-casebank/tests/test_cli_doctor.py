from types import SimpleNamespace

from casebank import cli


async def _fake_doctor_fail(*_args, **_kwargs):
    return [
        SimpleNamespace(name="rest_sessions", ok=True, detail="ok"),
        SimpleNamespace(name="ws_notifications", ok=False, detail="boom"),
    ]


def test_cli_collect_doctor_returns_nonzero(monkeypatch) -> None:
    monkeypatch.setattr("casebank.collectors.doctor.run_doctor", _fake_doctor_fail)
    code = cli.main(["collect", "doctor", "--base-url", "http://localhost:8099/api/v1"])
    assert code == 2
