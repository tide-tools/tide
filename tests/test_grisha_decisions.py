"""Договорённости ссылкой и решения Гриши по проекту за 3 суток в каждом сиде (SOC-102).

    env -u TIDE_DIALOG_LOG python3.12 -m pytest -q tests/test_grisha_decisions.py
"""

from datetime import datetime
from pathlib import Path

from tide import grisha_decisions as gd
from tide.launcher import handoff as ho
from tide.launcher import seed as seed_mod

JOURNAL = """# Журнал решений

```
## D-NNN · ГГГГ-ММ-ДД ЧЧ:ММ
- проект: tide-stack
## D-900 · 2026-10-11 09:00
- проект: tide-stack
- вопрос: пример из шапки
```

<!-- записи ниже: новые сверху; пульт вставляет сюда -->

## D-005 · 2026-10-11 10:00
- проект: tide-stack
- вопрос: Экран решений на доске нужен?
- ответ: «нужен»
- SOC: SOC-102
- уточнить: —

## D-004 · 2026-10-11 09:00
- проект: music
- вопрос: Педалборд — вариант 2?
- ответ: «да»
- SOC: SOC-77

## D-003 · 2026-10-10 08:00
- проект: tide-stack
- вопрос: Хендофф ночью?
- ответ: Да
- SOC: —
- уточнить: не реплай — ответ ли это?

## D-002 · 2026-10-07 08:00
- проект: tide-stack
- вопрос: Старое решение
- ответ: «давно»
- SOC: —
"""

NOW = datetime(2026, 10, 11, 12, 0)


def _journal(tmp_path: Path, text: str = JOURNAL) -> Path:
    p = tmp_path / "decisions.md"
    p.write_text(text, encoding="utf-8")
    return p


def test_block_only_project_last_3_days_skips_code_block(tmp_path):
    p = _journal(tmp_path)
    block = gd.seed_block("tide-stack", now=NOW, path=p)
    lines = block.splitlines()
    assert lines[0].startswith("решения Гриши по проекту tide-stack за 3 суток")
    assert lines[1] == "- 11.10 10:00 · Экран решений на доске нужен? → «нужен» · SOC-102"
    assert lines[2] == "- 10.10 08:00 · Хендофф ночью? → «Да» (уточнить)"
    assert len(lines) == 3                                   # D-002 старше 3 суток, D-900 — пример в шапке
    assert "Педалборд" not in block and "пример из шапки" not in block


def test_more_than_limit_tail_line(tmp_path):
    rows = "".join("## D-{0:03d} · 2026-10-11 0{1}:{2:02d}\n- проект: music\n- вопрос: q{0}\n- ответ: a{0}\n- SOC: —\n\n"
                   .format(n, n // 6, n % 6 * 10) for n in range(1, 14))
    p = _journal(tmp_path, rows)
    lines = gd.seed_block("music", now=NOW, path=p).splitlines()
    assert len(lines) == 1 + gd.LIMIT + 1
    assert lines[1].startswith("- 11.10 02:10 · q13")          # последние сверху
    assert lines[-1] == "- ещё 3 — {0}".format(p)


def test_no_file_or_no_records_no_block(tmp_path, monkeypatch):
    assert gd.seed_block("tide-stack", now=NOW, path=tmp_path / "nope.md") == ""
    assert gd.seed_block("mitehq", now=NOW, path=_journal(tmp_path)) == ""
    assert gd.seed_block("", now=NOW, path=_journal(tmp_path)) == ""
    monkeypatch.setenv(gd.AGREEMENTS_ENV, str(tmp_path / "agr"))
    monkeypatch.delenv(gd.DECISIONS_ENV, raising=False)
    assert gd.seed_text("tide-stack") == (
        "договорённости: {0}/ (читать на старте)\n"
        "грабли всех проектов: {1} (читать на старте)").format(tmp_path / "agr", gd.gotchas_file())


def test_default_agreements_line_points_to_management():
    assert gd.DEFAULT_AGREEMENTS == Path.home() / "Documents" / "management" / "docs" / "agreements"


def test_seed_text_rides_in_launcher_seed_and_throughline(tmp_path, monkeypatch):
    agr = tmp_path / "agr"
    agr.mkdir()
    (agr / "decisions.md").write_text(JOURNAL.replace("2026-10-11 10:00", datetime.now().strftime("%Y-%m-%d %H:%M")),
                                      encoding="utf-8")
    monkeypatch.setenv(gd.AGREEMENTS_ENV, str(agr))
    monkeypatch.delenv(gd.DECISIONS_ENV, raising=False)
    root = tmp_path / "tide-stack"
    (root / ".tide" / "arcs" / "29-@board").mkdir(parents=True)
    s = seed_mod.seed_for_project(root)
    assert "## Договорённости и решения Гриши" in s
    assert "договорённости: {0}/ (читать на старте)".format(agr) in s
    assert "Экран решений на доске нужен? → «нужен» · SOC-102" in s
    t = ho._grisha_decisions(root / ".tide" / "arcs" / "29-@board")
    assert t.startswith("договорённости: ") and "SOC-102" in t
    # нет журнала — только строка договорённостей, без падения
    (agr / "decisions.md").unlink()
    s2 = seed_mod.seed_for_project(root)
    assert "договорённости: {0}/".format(agr) in s2 and "решения Гриши по проекту" not in s2


def test_throughline_header_carries_decisions(tmp_path, monkeypatch):
    agr = tmp_path / "agr"
    agr.mkdir()
    (agr / "decisions.md").write_text(JOURNAL.replace("2026-10-11 10:00", datetime.now().strftime("%Y-%m-%d %H:%M")),
                                      encoding="utf-8")
    monkeypatch.setenv(gd.AGREEMENTS_ENV, str(agr))
    monkeypatch.delenv(gd.DECISIONS_ENV, raising=False)
    thread = tmp_path / "tide-stack" / ".tide" / "arcs" / "29-@board"
    born = thread / "arcs" / "02-board-2"
    born.mkdir(parents=True)
    (thread / "arc.md").write_text("---\nkind: thread\ngoal: экран решений\n---\n", encoding="utf-8")
    (born / "arc.md").write_text("---\nfrom: board\n---\n", encoding="utf-8")
    out = ho._with_throughline(thread, born, "ДИСТИЛЛ")
    head = out.split("\n---\n", 1)[0]
    assert "договорённости: {0}/ (читать на старте)".format(agr) in head
    assert "Экран решений на доске нужен? → «нужен» · SOC-102" in head and out.endswith("ДИСТИЛЛ")
