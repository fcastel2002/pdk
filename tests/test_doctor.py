"""pdk.config and the offline checks of pdk.doctor (no gateway, no Ignition install)."""

from __future__ import annotations

from pathlib import Path

import pytest

from pdk import config, doctor


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for variable in config.KEYS.values():
        monkeypatch.delenv(variable, raising=False)
    return monkeypatch


def test_config_fills_only_unset_variables(tmp_path: Path, clean_env: pytest.MonkeyPatch) -> None:
    file = tmp_path / "config.toml"
    file.write_text('gateway_url = "http://127.0.0.1:9088"\nignition_home = "D:/Ignition"\n', encoding="utf-8")
    clean_env.setenv(config.CONFIG_ENV, str(file))
    clean_env.setenv("IGNITION_HOME", "E:/Mine")
    path, applied, problem = config.load()
    assert path == file and problem is None
    assert applied == {"PDK_GATEWAY_URL": "gateway_url"}
    import os

    assert os.environ["PDK_GATEWAY_URL"] == "http://127.0.0.1:9088" and os.environ["IGNITION_HOME"] == "E:/Mine"


def test_config_reports_bad_files(tmp_path: Path, clean_env: pytest.MonkeyPatch) -> None:
    file = tmp_path / "config.toml"
    clean_env.setenv(config.CONFIG_ENV, str(file))
    assert config.load()[1:] == ({}, None)  # no file: nothing to do
    file.write_text("gateway_url = [", encoding="utf-8")
    assert "not valid TOML" in config.load()[2]
    file.write_text('sandbox = "x"\n', encoding="utf-8")
    assert "unknown keys sandbox" in config.load()[2]


def _conf(tmp_path: Path, text: str) -> doctor.State:
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "ignition.conf").write_text(text, encoding="utf-8")
    return doctor.State(ignition_home=tmp_path)


def test_ignition_conf_with_both_flags(tmp_path: Path) -> None:
    state = _conf(tmp_path, "wrapper.java.additional.1=-Ddata.dir=data\n"
                            "wrapper.java.additional.10 = -Dignition.allowunsignedmodules=true\n"
                            "wrapper.java.additional.11=-Dpdk.sandbox=my_sandbox\n")
    check = doctor._ignition_conf(state, False)
    assert check.status == doctor.OK and state.sandbox_flag == "my_sandbox"


def test_ignition_conf_says_which_lines_to_add(tmp_path: Path) -> None:
    state = _conf(tmp_path, "wrapper.java.additional.1=-Ddata.dir=data\n"
                            "#wrapper.java.additional.8=-Dpdk.sandbox=commented\n"
                            "wrapper.java.additional.6=--add-opens=x\n")
    check = doctor._ignition_conf(state, False)
    assert check.status == doctor.FAIL and state.sandbox_flag is None
    assert "wrapper.java.additional.7=-Dignition.allowunsignedmodules=true" in check.fix
    assert "wrapper.java.additional.8=-Dpdk.sandbox=<sandbox project>" in check.fix


def test_checks_after_a_failure_are_skipped() -> None:
    state = doctor.State()
    assert doctor._schemas(state, False).status == doctor.SKIP
    assert doctor._api_key(state, False).status == doctor.SKIP
    assert doctor._sandbox(state, False).status == doctor.SKIP


def test_sandbox_and_eyes_from_health() -> None:
    state = doctor.State(health={"sandbox": "s", "sandboxExists": False, "runtime": True, "logs": False})
    assert doctor._sandbox(state, False).status == doctor.FAIL
    assert "log tap" in doctor._eyes(state, False).detail
    state.health = {"sandbox": None}
    assert "read-only" in doctor._sandbox(state, False).detail
    state.health = {"sandbox": "s", "sandboxExists": True, "writeEnabled": True, "target": "t", "targetExists": True}
    assert doctor._sandbox(state, False).status == doctor.OK and "target t" in doctor._sandbox(state, False).detail
    state.health = {**state.health, "targetExists": False}
    assert doctor._sandbox(state, False).status == doctor.WARN
    state.health = {"sandbox": "s", "sandboxExists": True, "writeEnabled": True, "targetIgnored": "s"}
    assert "ignored" in doctor._sandbox(state, False).detail


def test_report() -> None:
    text = doctor.report([doctor.Check("gateway", doctor.FAIL, "down", "start it\nthen retry"),
                          doctor.Check("jdk", doctor.OK, "found", "unused")])
    lines = text.splitlines()
    assert lines[0] == "FAIL  gateway  down" and lines[1].strip() == "-> start it" and lines[2].strip() == "then retry"
    assert "unused" not in text and lines[-1].startswith("1 check(s) failed")
