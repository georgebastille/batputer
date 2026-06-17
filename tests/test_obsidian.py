import subprocess

import connectors.obsidian as ob


def test_ensure_running_noop_when_already_running(monkeypatch):
    monkeypatch.setattr(ob, "is_running", lambda: True)
    calls = []
    monkeypatch.setattr(ob.subprocess, "run", lambda *a, **k: calls.append(a))
    ob.ensure_running("MyVault")
    assert calls == []  # nothing launched


def test_ensure_running_launches_via_official_cli(monkeypatch):
    monkeypatch.setattr(ob, "is_running", lambda: False)
    monkeypatch.setattr(ob.shutil, "which", lambda _: "/usr/local/bin/obsidian")
    cmds = []
    monkeypatch.setattr(
        ob.subprocess, "run",
        lambda cmd, **k: cmds.append(cmd) or subprocess.CompletedProcess(cmd, 0),
    )
    ob.ensure_running("MyVault")
    assert cmds[0][0] == "obsidian"
    assert "vault=MyVault" in cmds[0]


def test_ensure_running_falls_back_to_open_without_cli(monkeypatch):
    monkeypatch.setattr(ob, "is_running", lambda: False)
    monkeypatch.setattr(ob.shutil, "which", lambda _: None)
    cmds = []
    monkeypatch.setattr(
        ob.subprocess, "run",
        lambda cmd, **k: cmds.append(cmd) or subprocess.CompletedProcess(cmd, 0),
    )
    ob.ensure_running("MyVault")
    assert cmds[-1][:3] == ["open", "-ga", "Obsidian"]
