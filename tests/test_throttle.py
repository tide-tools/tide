"""tide.throttle — пауза до сброса, потолок агентов, правило 85 % (SOC-166, движок).

Файл доски — только во временном control-home (живой throttle.json не трогаем).
Хук проверяется через ``tide hook role-gate`` целиком: ao spawn / tide-ci / полный
pytest режутся, правки, git commit, tide offload, ao send — никогда; нет файла или
битый файл — пропуск и строка в журнале, хук не падает.
"""

from __future__ import annotations

import io
import json
import shlex
from datetime import datetime, timedelta, timezone

import pytest

from tide import cli, throttle
from tide.hooks import role_gate

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)


def _iso(d):
    return d.isoformat(timespec="seconds")


def _state(**over):
    st = {"v": 1, "paused": {"all": False, "projects": {}}, "until": None, "ceiling": 8,
          "ceiling_auto": None, "auto_pct": 85, "window_pct": None, "window_until": None,
          "by": "board", "at": _iso(datetime.now(timezone.utc)), "auto_at": None}
    st.update(over)
    return st


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    (h / ".tide" / "state").mkdir(parents=True)
    (h / "roster.md").write_text("# tide roster\n", encoding="utf-8")
    monkeypatch.setenv("TIDE_HOME", str(h))
    monkeypatch.setenv("AO_PROJECT_ID", "music")
    monkeypatch.setenv("AO_SESSION_ID", "music-21")
    # живых AO-сессий — подставные, настоящий ao не зовём
    monkeypatch.setattr(throttle, "live_agents", lambda timeout=5: 3)
    return h


def _write(home, data):
    p = home / ".tide" / "state" / "throttle.json"
    p.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return p


def _log(home):
    p = home / ".tide" / "state" / "throttle.log"
    return p.read_text(encoding="utf-8").splitlines() if p.exists() else []


def _hook(monkeypatch, command, *, tool="Bash", role="worker", tool_input=None):
    payload = {"tool_name": tool, "tool_input": tool_input or {"command": command},
               "session_id": "sid-1", "cwd": "/tmp"}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setenv("TIDE_ROLE", role)
    return cli.main(["hook", "role-gate"])


# --- вид запуска ----------------------------------------------------------------

@pytest.mark.parametrize("cmd,kind", [
    ("ao spawn --project music --prompt 'x; y'", "spawn"),
    ("/Applications/Agent\\ Orchestrator.app/Contents/Resources/daemon/ao spawn", "spawn"),
    ("ao session restore music-3", "spawn"),
    ("ao session resume-agent music-3", "spawn"),
    ("ao review trigger", "spawn"),
    ("scripts/tide-ci", "heavy"),
    ("TIDE_CI_REF=HEAD scripts/tide-ci", "heavy"),
    ("bash scripts/tide-ci", "heavy"),
    ("pytest", "heavy"),
    ("pytest -q -x -n auto", "heavy"),
    ("pytest tests", "heavy"),
    ("env -u TIDE_ALERT_CMD .venv/bin/python -m pytest -q", "heavy"),
    ("uv run pytest -q", "heavy"),
    ("pytest -q 2>&1", "heavy"),
    ("pytest tests/test_throttle.py", None),
    ("env -u TIDE_ALERT_CMD .venv/bin/python -m pytest tests/test_a.py -q", None),
    ("pytest -k seam", None),
    ("pytest -p no:cacheprovider tests/test_a.py::test_x", None),
    ("ao send --session music-21 --message hi", None),
    ("ao session ls --json", None),
    ("git commit -m 'pytest ao spawn'", None),
    ("tide offload x --cursor y", None),
    ("tide mail send music/main", None),
])
def test_kind_of(cmd, kind):
    assert throttle.kind_of(shlex.split(cmd)) == kind


# --- правила --------------------------------------------------------------------

def test_pause_until_and_ceiling_rules():
    st, bad = throttle.parse(_state(paused={"all": False, "projects": {"music": True}},
                                    until=_iso(NOW + timedelta(hours=1)), ceiling=8,
                                    ceiling_auto=5))
    assert bad is None
    assert throttle.paused(st, "music", NOW) and not throttle.paused(st, "edge", NOW)
    assert not throttle.paused(st, "music", NOW + timedelta(hours=2))  # until прошёл
    assert throttle.ceiling(st) == 5
    st2, _ = throttle.parse(_state(ceiling=None, ceiling_auto=None))
    assert throttle.ceiling(st2) is None


def test_window_rule_ignores_reset_window():
    st, _ = throttle.parse(_state(window_pct=90, window_until=_iso(NOW + timedelta(hours=1))))
    assert throttle.window_hot(st, NOW)
    assert not throttle.window_hot(st, NOW + timedelta(hours=2))
    cold, _ = throttle.parse(_state(window_pct=84.9))
    assert not throttle.window_hot(cold, NOW)


def test_wake_kind_held_only_by_pause():
    """SOC-161: автопинок после сброса держит только пауза — не потолок и не окно."""
    st, _ = throttle.parse(_state(paused={"all": True, "projects": {}}, until=None,
                                  window_pct=99))
    assert not throttle.verdict(st, "music", throttle.KIND_WAKE, now=NOW).allow
    st, _ = throttle.parse(_state(window_pct=99, ceiling=1))
    assert throttle.verdict(st, "music", throttle.KIND_WAKE, now=NOW, live=lambda: 9).allow


# --- хук: пауза ------------------------------------------------------------------

def test_pause_all_denies_spawn_keeps_commit_and_edits(home, monkeypatch, capsys):
    _write(home, _state(paused={"all": True, "projects": {}},
                        until=_iso(datetime.now(timezone.utc) + timedelta(hours=2))))
    assert _hook(monkeypatch, "ao spawn --project music --prompt go") == role_gate.BLOCK_EXIT
    err = capsys.readouterr().err
    assert "пауза всех проектов до сброса окна (до " in err
    assert "доведи шаг до коммита, новые агенты не стартуют" in err
    for cmd in ("git commit -m 'шаг'", "tide offload a --cursor b", "ao send --session x "
                "--message hi", "pytest tests/test_a.py"):
        assert _hook(monkeypatch, cmd) == role_gate.ALLOW_EXIT, cmd
    assert _hook(monkeypatch, "", tool="Edit",
                 tool_input={"file_path": "/tmp/a.py"}) == role_gate.ALLOW_EXIT
    assert _hook(monkeypatch, "", tool="Write",
                 tool_input={"file_path": "/tmp/a.py", "content": "x"}) == role_gate.ALLOW_EXIT
    # пауза — и тяжёлое не начинается
    assert _hook(monkeypatch, "scripts/tide-ci") == role_gate.BLOCK_EXIT
    log = _log(home)
    assert any(" · music-21 · music · spawn · pause · отказ `ao spawn" in ln for ln in log)


def test_pause_of_project_only_hits_that_project(home, monkeypatch, capsys):
    _write(home, _state(paused={"all": False, "projects": {"music": True}},
                        until=_iso(datetime.now(timezone.utc) + timedelta(hours=2))))
    assert _hook(monkeypatch, "cd /x && ao spawn --project music") == role_gate.BLOCK_EXIT
    assert "пауза проекта music" in capsys.readouterr().err
    monkeypatch.setenv("AO_PROJECT_ID", "edge")
    assert _hook(monkeypatch, "ao spawn --project edge") == role_gate.ALLOW_EXIT


@pytest.mark.parametrize("cmd,target", [
    ("ao spawn --project mitehq --prompt go", "mitehq"),
    ("ao spawn --project=mitehq --name x", "mitehq"),
    ("ao spawn --name 'p -x' --prompt '--project fake' --project tide-stack", "tide-stack"),
    ("ao spawn --prompt go", None),
    ("ao spawn --standalone --harness claude-code", None),
    ("ao session restore mitehq-12", "mitehq"),
    ("ao session restore -p edge mitehq-12", "edge"),
    ("ao session resume-agent tide-stack-40 --json", "tide-stack"),
    ("ao session resume-agent -ptide-stack x", "tide-stack"),
    ("ao session restore some-id", None),
    ("ao review trigger mitehq-3 --model opus", "mitehq"),
    ("ao review trigger --model opus --session=edge-7", "edge"),
    ("ao review trigger --harness codex", None),
    ("ao send --session mitehq-1 --message hi", None),
    ("env AO_X=1 ao spawn --project mitehq 2>&1", "mitehq"),
])
def test_target_project(cmd, target):
    assert throttle.target_project(shlex.split(cmd)) == target


def test_pause_checks_target_project_not_caller(home, monkeypatch, capsys):
    """SOC-166, живой тест паузы: ``ao spawn --project mitehq`` из сессии tide-stack
    при паузе mitehq проходил как tide-stack. Пауза — для проекта-ЦЕЛИ."""
    _write(home, _state(paused={"all": False, "projects": {"mitehq": True}},
                        until=_iso(datetime.now(timezone.utc) + timedelta(hours=2))))
    monkeypatch.setenv("AO_PROJECT_ID", "tide-stack")
    monkeypatch.setenv("AO_SESSION_ID", "tide-stack-40")
    assert _hook(monkeypatch, "ao spawn --project mitehq --prompt go") == role_gate.BLOCK_EXIT
    assert "пауза проекта mitehq" in capsys.readouterr().err
    assert any(" · tide-stack-40 · mitehq · spawn · pause · отказ" in ln for ln in _log(home))
    # restore чужой сессии на паузе — тоже отказ
    assert _hook(monkeypatch, "ao session restore mitehq-12") == role_gate.BLOCK_EXIT
    # без --project — проект вызывающей (tide-stack не на паузе)
    assert _hook(monkeypatch, "ao spawn --prompt go") == role_gate.ALLOW_EXIT
    # из mitehq (на паузе) в tide-stack — пропуск
    monkeypatch.setenv("AO_PROJECT_ID", "mitehq")
    monkeypatch.setenv("AO_SESSION_ID", "mitehq-5")
    assert _hook(monkeypatch, "ao spawn --project tide-stack") == role_gate.ALLOW_EXIT
    # а без цели из mitehq — пауза своего проекта, как прежде
    assert _hook(monkeypatch, "ao spawn --prompt go") == role_gate.BLOCK_EXIT


def test_target_does_not_bypass_pause_all_or_ceiling(home, monkeypatch):
    _write(home, _state(paused={"all": True, "projects": {}}, until=None))
    monkeypatch.setenv("AO_PROJECT_ID", "mitehq")
    assert _hook(monkeypatch, "ao spawn --project tide-stack") == role_gate.BLOCK_EXIT
    _write(home, _state(ceiling=3))
    monkeypatch.setattr(throttle, "live_agents", lambda timeout=5: 3)
    assert _hook(monkeypatch, "ao spawn --project tide-stack") == role_gate.BLOCK_EXIT


def test_heredoc_body_is_data_not_a_command(home, monkeypatch):
    """Живой случай 10.10: строка «  ao review trigger — …» в тексте коммита через
    heredoc резалась как запуск агента. Тело heredoc — данные; команда после — нет."""
    _write(home, _state(paused={"all": True, "projects": {}}, until=None, ceiling=1))
    commit = ("git commit -q -F - <<'EOF'\nfeat: x\n  ao review trigger — пауза\n"
              "scripts/tide-ci\npytest\nEOF\ngit log --oneline -1")
    assert _hook(monkeypatch, commit) == role_gate.ALLOW_EXIT
    assert _hook(monkeypatch, "python3 - <<EOF\nao spawn\nEOF\n") == role_gate.ALLOW_EXIT
    # после терминатора — снова команды
    assert _hook(monkeypatch, "cat <<EOF\nx\nEOF\nao spawn --project music") \
        == role_gate.BLOCK_EXIT
    # here-string <<< — не heredoc
    assert throttle.strip_heredocs("cat <<< x\nao spawn") == "cat <<< x\nao spawn"


def test_until_passed_lets_spawn_through(home, monkeypatch):
    _write(home, _state(paused={"all": True, "projects": {"music": True}},
                        until=_iso(datetime.now(timezone.utc) - timedelta(minutes=1))))
    assert _hook(monkeypatch, "ao spawn --project music") == role_gate.ALLOW_EXIT


def test_subagent_and_orchestrator_are_throttled_too(home, monkeypatch):
    """Гейт троттлинга — для всех ролей: сабагент с полными руками тоже не гоняет tide-ci."""
    _write(home, _state(paused={"all": True, "projects": {}}, until=None))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(
        {"tool_name": "Bash", "tool_input": {"command": "scripts/tide-ci"},
         "agent_id": "sub-1"})))
    monkeypatch.setenv("TIDE_ROLE", "orchestrator")
    assert cli.main(["hook", "role-gate"]) == role_gate.BLOCK_EXIT


# --- хук: потолок -----------------------------------------------------------------

def test_ceiling_denies_when_live_reaches_it(home, monkeypatch, capsys):
    _write(home, _state(ceiling=8, ceiling_auto=3))
    monkeypatch.setattr(throttle, "live_agents", lambda timeout=5: 3)
    assert _hook(monkeypatch, "ao spawn --project music") == role_gate.BLOCK_EXIT
    assert "потолок 3 агентов, сейчас 3 — встань в очередь" in capsys.readouterr().err
    monkeypatch.setattr(throttle, "live_agents", lambda timeout=5: 2)
    assert _hook(monkeypatch, "ao spawn --project music") == role_gate.ALLOW_EXIT
    # потолок не касается тяжёлых прогонов и ao send
    monkeypatch.setattr(throttle, "live_agents", lambda timeout=5: 50)
    assert _hook(monkeypatch, "scripts/tide-ci") == role_gate.ALLOW_EXIT
    assert _hook(monkeypatch, "ao send --session a --message b") == role_gate.ALLOW_EXIT
    log = _log(home)
    assert any(" · spawn · ceiling · отказ" in ln for ln in log)
    assert any(" · spawn · ok · пропуск" in ln and "потолок 3, сейчас 2" in ln for ln in log)


def test_ceiling_with_silent_ao_lets_through(home, monkeypatch):
    _write(home, _state(ceiling=1))
    monkeypatch.setattr(throttle, "live_agents", lambda timeout=5: None)
    assert _hook(monkeypatch, "ao spawn --project music") == role_gate.ALLOW_EXIT
    assert any(" · spawn · unknown · " in ln for ln in _log(home))


def test_live_agents_counts_working_workers(monkeypatch, tmp_path):
    fake = tmp_path / "ao"
    rows = [{"id": "a", "role": "worker", "status": "working", "isTerminated": False},
            {"id": "b", "role": "worker", "status": "idle", "activity": "idle"},
            {"id": "c", "role": "orchestrator", "status": "working"},
            {"id": "d", "role": "worker", "status": "working", "isTerminated": True},
            {"id": "e", "role": "worker", "status": "x", "activity": "active"}]
    fake.write_text("#!/bin/sh\ncat <<'EOF'\n" + json.dumps({"data": rows}) + "\nEOF\n")
    fake.chmod(0o755)
    monkeypatch.setenv("TIDE_AO_BIN", str(fake))
    assert throttle.live_agents() == 2
    monkeypatch.setenv("TIDE_AO_BIN", str(tmp_path / "nope"))
    assert throttle.live_agents() is None


# --- хук: правило 85 % ------------------------------------------------------------

def test_window_85_blocks_heavy_but_not_targeted(home, monkeypatch, capsys):
    _write(home, _state(window_pct=86, auto_pct=85,
                        window_until=_iso(datetime.now(timezone.utc) + timedelta(hours=1))))
    assert _hook(monkeypatch, "cd /x && TIDE_CI_REF=HEAD scripts/tide-ci") == role_gate.BLOCK_EXIT
    assert "окно израсходовано на 86% (порог 85%)" in capsys.readouterr().err
    assert _hook(monkeypatch, "env -u TIDE_ALERT_CMD .venv/bin/python -m pytest -q") \
        == role_gate.BLOCK_EXIT
    assert _hook(monkeypatch, "pytest -q | tail -3") == role_gate.BLOCK_EXIT
    assert _hook(monkeypatch, "env -u TIDE_ALERT_CMD .venv/bin/python -m pytest "
                 "tests/test_throttle.py -q") == role_gate.ALLOW_EXIT
    assert _hook(monkeypatch, "pytest -k seam") == role_gate.ALLOW_EXIT
    assert _hook(monkeypatch, "git commit -m x") == role_gate.ALLOW_EXIT



def test_window_estimate_of_board_counts_with_mark(home, monkeypatch, capsys):
    """Строка состояния мертва — доска пишет оценку (window_pct_est): правило 85 % её
    считает, но и отказ, и журнал говорят «≈N% (оценка доски)» (SOC-166)."""
    _write(home, _state(window_pct=88, window_pct_est=True, auto_pct=85,
                        window_until=_iso(datetime.now(timezone.utc) + timedelta(hours=1))))
    assert _hook(monkeypatch, "scripts/tide-ci") == role_gate.BLOCK_EXIT
    err = capsys.readouterr().err
    assert "окно ≈88% (оценка доски), порог 85%" in err
    assert "израсходовано" not in err
    assert any(" · heavy · window · " in ln and "окно ≈88% (оценка доски)" in ln
               for ln in _log(home))
    assert _hook(monkeypatch, "pytest tests/test_throttle.py") == role_gate.ALLOW_EXIT
    # оценка ниже порога — не держит
    _write(home, _state(window_pct=60, window_pct_est=True))
    assert _hook(monkeypatch, "scripts/tide-ci") == role_gate.ALLOW_EXIT


def test_window_est_flag_only_true_counts():
    st, bad = throttle.parse(_state(window_pct=90, window_pct_est="yes"))
    assert st["window_pct_est"] is False and bad is None
    v = throttle.verdict(st, "music", throttle.KIND_SPAWN, now=NOW)
    assert not v.allow and "израсходовано на 90%" in v.reason and "≈" not in v.reason


# --- хук: файл недоступен ---------------------------------------------------------

@pytest.mark.parametrize("content,why", [
    (None, "нет файла"),
    ("{не json", "битый JSON"),
    ('["x"]', "файл не объект JSON"),
    ('{"v": 1}', "нет полей paused/ceiling"),
])
def test_missing_or_broken_file_allows_and_logs(home, monkeypatch, content, why):
    if content is not None:
        _write(home, content)
    assert _hook(monkeypatch, "ao spawn --project music") == role_gate.ALLOW_EXIT
    assert _hook(monkeypatch, "scripts/tide-ci") == role_gate.ALLOW_EXIT
    log = _log(home)
    assert any(" · nofile · пропуск — без ограничений: {0}".format(why) in ln for ln in log)


def test_journal_does_not_repeat_same_reason(home, monkeypatch):
    for _ in range(3):
        _hook(monkeypatch, "ao spawn --project music")
    assert len([ln for ln in _log(home) if " · nofile · " in ln]) == 1
    monkeypatch.setenv("AO_SESSION_ID", "music-22")      # другая сессия — своя строка
    _hook(monkeypatch, "ao spawn --project music")
    assert len([ln for ln in _log(home) if " · nofile · " in ln]) == 2
    # старше REPEAT_MIN — строка снова пишется
    later = datetime.now(timezone.utc) + timedelta(minutes=throttle.REPEAT_MIN + 1)
    assert throttle.journal(home, "music-22", "music", "spawn", "nofile", "x", now=later)


def test_hook_survives_any_throttle_crash(home, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("сломался")
    real_gate = throttle.gate
    monkeypatch.setattr(throttle, "gate", boom)
    assert _hook(monkeypatch, "ao spawn --project music") == role_gate.ALLOW_EXIT
    monkeypatch.setattr(throttle, "gate", real_gate)
    monkeypatch.setattr(throttle, "load", boom)
    assert _hook(monkeypatch, "ao spawn --project music") == role_gate.ALLOW_EXIT
    monkeypatch.setattr(throttle, "kind_of", boom)
    assert _hook(monkeypatch, "ao spawn --project music") == role_gate.ALLOW_EXIT


def test_load_never_raises_without_home(monkeypatch):
    monkeypatch.delenv("TIDE_HOME", raising=False)
    monkeypatch.chdir("/")
    st, bad, path = throttle.load()
    assert st is None and bad and path is None


# --- tide throttle status ---------------------------------------------------------

def test_status_line_paused_and_stale(home, monkeypatch, capsys):
    now = datetime.now(timezone.utc)
    _write(home, _state(paused={"all": True, "projects": {}}, until=_iso(now + timedelta(hours=1)),
                        ceiling=8, ceiling_auto=5, window_pct=42.4,
                        window_until=_iso(now + timedelta(hours=1)),
                        at=_iso(now - timedelta(minutes=25))))
    assert cli.main(["throttle", "status"]) == 0
    out = capsys.readouterr().out.strip()
    assert len(out.splitlines()) == 1
    hh = (now + timedelta(hours=1)).astimezone().strftime("%H:%M")
    assert "пауза всех до {0}".format(hh) in out
    assert "потолок 5 (ручной 8, авто 5)" in out
    assert "живых сейчас 3" in out
    assert "окно 42% (порог 85%" in out
    assert "throttle.json" in out
    assert "доска не обновляла файл 25 мин" in out


def test_status_line_fresh_running_and_missing(home, monkeypatch, capsys):
    _write(home, _state())
    assert cli.main(["throttle"]) == 0
    out = capsys.readouterr().out
    assert "работа идёт" in out and "не обновляла" not in out
    (home / ".tide" / "state" / "throttle.json").unlink()
    assert cli.main(["throttle", "status"]) == 0
    assert "без ограничений (нет файла)" in capsys.readouterr().out



def test_status_line_marks_board_estimate(home, monkeypatch, capsys):
    now = datetime.now(timezone.utc)
    _write(home, _state(window_pct=86.4, window_pct_est=True,
                        window_until=_iso(now + timedelta(hours=1))))
    assert cli.main(["throttle", "status"]) == 0
    out = capsys.readouterr().out
    assert "окно ≈86% (оценка), порог 85%" in out
    assert "тяжёлые не начинаются" in out


def test_throttle_check_exit_codes(home, monkeypatch, capsys):
    _write(home, _state(paused={"all": False, "projects": {"music": True}}, until=None))
    assert cli.main(["throttle", "check", "--kind", "wake"]) == 1
    assert cli.main(["throttle", "check", "--kind", "wake", "--project", "edge"]) == 0
