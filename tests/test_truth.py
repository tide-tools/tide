"""tide truth — «что сказал человек» по темам: список, тема, grep, корень из ростера, read-only.

Всё на tmp-проектах: свой дом с roster.md, свои корни; живые файлы не трогаем.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tide import cli, truth

FACTS = """# Факты: передача работы — что сказал человек

тема:       alpha · 02-peredacha
сверено:    2026-10-09
фактов:     3

Как читать: `- факт · дата · ссылки`; «(заменено → …)» — позже сказал иначе.

## Сейчас

- Хендофф должен случаться сам · `tg:1`

## Открыто

- Порог хендоффа — 200к или 300к · `tg:2`

## Факты (новые сверху)

- «хендофф на 300к» — порог по выгоде · 09.10.2026 · `tg:3`
- Менеджер разносит поручения · 09.10.2026 · `tg:4`
- «лимит 200к» (заменено → `tg:3`) · 07.10.2026 · `tg:5`
"""

TOPIC = """# {slug}

title: {title}
kind: topic

где мы:     идёт обкатка
сверено:    2026-10-08
"""


def _topic(root: Path, slug: str, title: str, facts: str | None = None) -> Path:
    d = root / ".tide" / "topics" / slug
    d.mkdir(parents=True)
    (d / "topic.md").write_text(TOPIC.format(slug=slug, title=title), encoding="utf-8")
    if facts is not None:
        (d / "facts.md").write_text(facts, encoding="utf-8")
    return d


@pytest.fixture
def world(tmp_path, monkeypatch):
    """Дом с ростером на два проекта + архивный; cwd — посторонний каталог."""
    home = tmp_path / "home"
    (home / ".tide").mkdir(parents=True)
    alpha = tmp_path / "alpha"
    beta = tmp_path / "beta"
    old = tmp_path / "old"
    for r in (alpha, beta, old):
        (r / ".tide").mkdir(parents=True)
    _topic(alpha, "02-peredacha", "передача работы", FACTS)
    _topic(beta, "01-kontrakt", "контракт агентов")  # без facts.md
    _topic(old, "01-staroe", "архивная тема", FACTS)
    (home / "roster.md").write_text(
        "# tide roster\n"
        "alpha | {0}\nbeta | {1}\nold | {2} | status=archived\n".format(alpha, beta, old),
        encoding="utf-8",
    )
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.setenv("TIDE_HOME", str(home))
    monkeypatch.chdir(elsewhere)
    return {"home": home, "alpha": alpha, "beta": beta, "old": old, "tmp": tmp_path}


def _run(capsys, *argv) -> tuple[int, str]:
    rc = cli.main(["truth", *argv])
    return rc, capsys.readouterr().out


def _snapshot(root: Path) -> dict:
    return {str(p): (p.stat().st_mtime_ns, p.read_bytes())
            for p in sorted(root.rglob("*")) if p.is_file()}


def test_list_covers_active_roster_projects(world, capsys):
    rc, out = _run(capsys)
    assert rc == 0
    assert "alpha · 02-peredacha · передача работы · фактов 5 · сверено 2026-10-09" in out  # Сейчас+Открыто+Факты
    assert "beta · 01-kontrakt · контракт агентов · фактов нет · сверено 2026-10-08" in out
    assert "01-staroe" not in out  # архивный проект не читается


def test_list_json_and_project_filter(world, capsys):
    rc, out = _run(capsys, "--project", "beta", "--json")
    data = json.loads(out)
    assert rc == 0 and [t["slug"] for t in data] == ["01-kontrakt"]
    assert data[0]["facts_file"] == ""


def test_unknown_project_is_error(world, capsys):
    assert _run(capsys, "--project", "nope")[0] != 0


@pytest.mark.parametrize("query", ["02-peredacha", "2", "pered", "передача", "alpha/02-peredacha"])
def test_select_topic_by_slug_number_part_title(world, capsys, query):
    rc, out = _run(capsys, query)
    assert rc == 0
    assert out.startswith("alpha · 02-peredacha — передача работы · сверено 2026-10-09")
    assert "Сейчас:\nfacts.md:11 - Хендофф должен случаться сам" in out
    assert "Открыто:\nfacts.md:15 - Порог хендоффа" in out


def test_topic_hides_replaced_and_limits(world, capsys):
    _, out = _run(capsys, "pered", "--limit", "1")
    assert "Факты (1 из 2, новые сверху; заменено скрыто: 1):" in out
    assert "«хендофф на 300к»" in out and "Менеджер разносит" not in out
    assert "лимит 200к" not in out
    assert str(world["alpha"] / ".tide/topics/02-peredacha") + "\n" in out  # папка один раз сверху
    assert "facts.md:19 - «хендофф на 300к»" in out
    assert "topic.md:6 где мы:     идёт обкатка" in out
    _, out_all = _run(capsys, "pered", "--all")
    assert "лимит 200к" in out_all and "Факты (3 из 3" in out_all


def test_topic_without_facts(world, capsys):
    rc, out = _run(capsys, "kontrakt")
    assert rc == 0
    assert "фактов нет: facts.md не заведён" in out
    assert "topic.md:6 где мы:     идёт обкатка" in out


def test_grep_all_words_with_address(world, capsys):
    rc, out = _run(capsys, "--grep", "ХЕНДОФФ", "300")
    assert rc == 0
    facts = world["alpha"] / ".tide/topics/02-peredacha/facts.md"
    lines = facts.read_text(encoding="utf-8").splitlines()
    want = [i for i, ln in enumerate(lines, 1) if "хендофф" in ln.lower() and "300" in ln]
    assert want  # мерка: строки с обоими словами есть
    assert "alpha · 02-peredacha  {0}".format(facts.parent) in out
    for i in want:
        assert "facts.md:{0} ".format(i) in out
    assert "Менеджер" not in out


def test_grep_inside_topic_and_json(world, capsys):
    rc, out = _run(capsys, "pered", "--grep", "менеджер", "--json")
    data = json.loads(out)
    assert rc == 0 and data["total"] == 1
    hit = data["hits"][0]
    assert hit["path"].endswith("02-peredacha/facts.md") and hit["line"] > 0


def test_grep_nothing_says_so(world, capsys):
    rc, out = _run(capsys, "--grep", "несуществующее")
    assert rc == 0 and "в фактах нет" in out


def test_unknown_topic_falls_back_to_grep(world, capsys):
    rc, out = _run(capsys, "поручения")
    assert rc == 0 and "facts.md:" in out and "Менеджер разносит поручения" in out


def test_ambiguous_topic_lists_candidates(world, capsys):
    rc = cli.main(["truth", "01"])
    err = capsys.readouterr()
    # beta/01 — единственная активная «01»: архив не в выборке → выбрана сразу
    assert rc == 0 and "beta · 01-kontrakt" in err.out
    _topic(world["alpha"], "01-drugoe", "другое")
    rc = cli.main(["truth", "01"])
    err = capsys.readouterr()
    assert rc != 0 and "alpha/01-drugoe" in err.err + err.out


def test_searches_roster_root_not_cwd_worktree(world, capsys, monkeypatch):
    """cwd — копия проекта без .tide/topics (как AO-worktree): ищем в корне ростера."""
    wt = world["tmp"] / "worktree-of-alpha"
    (wt / ".tide").mkdir(parents=True)  # worktree: .tide есть, topics — нет
    stale = wt / ".tide/topics/02-peredacha"
    stale.mkdir(parents=True)
    (stale / "facts.md").write_text("## Факты\n\n- УСТАРЕЛО · `x`\n", encoding="utf-8")
    monkeypatch.chdir(wt)
    rc, out = _run(capsys, "pered")
    assert rc == 0 and "УСТАРЕЛО" not in out
    assert str(world["alpha"] / ".tide/topics/02-peredacha") in out


def test_no_home_maps_ao_worktree_to_main(tmp_path, monkeypatch, capsys):
    """Без дома: cwd в AO-worktree сводится к главному дереву (paths.find_tide_root)."""
    main = tmp_path / "main"
    (main / ".tide").mkdir(parents=True)
    _topic(main, "01-tema", "тема", FACTS)
    subprocess.run(["git", "init", "-q", str(main)], check=True)
    subprocess.run(["git", "-C", str(main), "commit", "-q", "--allow-empty", "-m", "x"],
                   check=True, env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                                    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
                                    "HOME": str(tmp_path), "PATH": "/usr/bin:/bin:/opt/homebrew/bin"})
    data = tmp_path / "ao"
    wt = data / "worktrees" / "proj" / "s-1"
    wt.parent.mkdir(parents=True)
    subprocess.run(["git", "-C", str(main), "worktree", "add", "-q", "--detach", str(wt)], check=True)
    monkeypatch.delenv("TIDE_HOME", raising=False)
    monkeypatch.setenv("AO_DATA_DIR", str(data))
    monkeypatch.chdir(wt)
    assert truth.resolve_projects() == [("main", main.resolve())]
    rc, out = _run(capsys, "tema")
    assert rc == 0 and "Хендофф должен случаться сам" in out


def test_read_only(world, capsys):
    before = _snapshot(world["tmp"])
    for argv in ([], ["pered"], ["kontrakt"], ["--grep", "хендофф"], ["pered", "--all", "--json"]):
        cli.main(["truth", *argv])
    capsys.readouterr()
    assert _snapshot(world["tmp"]) == before


def test_grep_quoted_phrase_splits_into_words(world, capsys):
    """`--grep "хендофф 300"` одним аргументом ищет как два слова, не как фразу."""
    _, quoted = _run(capsys, "--grep", "Хендофф 300")
    _, split = _run(capsys, "--grep", "хендофф", "300")
    assert "в фактах нет" not in quoted and quoted == split
    data = json.loads(_run(capsys, "--grep", "300  ХЕНДОФФ", "--json")[1])
    assert data["words"] == ["300", "ХЕНДОФФ"] and data["total"] >= 1


# --- мерка (б): поиск живыми словами, фильтры, служебные строки, свежесть ---

LIVE_FACTS = """# Факты: передача работы — что сказал Гриша

> Пилот context-keeper. Передача работы — пометка файла, не ответ.

тема:       live · 02-peredacha-raboty
сверено:    2026-10-09
фактов:     4
откуда:     срез про передачу работы и хендофф
текст:      текст контракта агентов — документ Linear

| ссылка | передача работы хендофф |
|---|---|

## Сейчас

- Старая сессия передаёт работу новой и молчит · `tg:1`

## Открыто

- Порог хендоффа не решён · `tg:2`

## Факты (новые сверху)

- «хендофф на 300к» — после передачи работы · 09.10.2026 · `tg:3`
- Контракт агентов v1 подписан Гришей · 09.10.2026 · `tg:4`
- «хендофф на 200к» (заменено → `tg:3`) · 07.10.2026 · `tg:5`
"""

LIVE_TOPIC = """# 02-peredacha-raboty

title: передача работы между сессиями
kind: topic
status: active

где мы:     хендофф обкатывается, передача работы идёт
горит:      —
не читать:  топик 01-staryi (12.09) — устарел
сверено:    2026-10-09
"""

OLD_TOPIC = """# 01-staryi

title: старый накопитель
kind: topic
status: active

где мы: ждём слова по хендофф и передаче работы
сверяли: — (ещё не сверяли)
"""

ARCH_TOPIC = """# 03-arhiv

title: архивная тема
kind: topic
status: archived

где мы: хендофф архивный
сверено: 2026-09-01
"""

NOCHECK_TOPIC = """# 04-bez-sverki

title: без сверки
kind: topic

где мы: хендофф без сверки
"""

MUSIC_TOPIC = """# 01-telefon

title: телефон — волна
kind: topic

горит:      проход Гриши по волне
сверено:    2026-10-09
"""


def _write(root: Path, slug: str, topic: str, facts: str | None = None) -> Path:
    d = root / ".tide" / "topics" / slug
    d.mkdir(parents=True)
    (d / "topic.md").write_text(topic, encoding="utf-8")
    if facts is not None:
        (d / "facts.md").write_text(facts, encoding="utf-8")
    return d


@pytest.fixture
def live(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".tide").mkdir(parents=True)
    stack, music = tmp_path / "stack", tmp_path / "music"
    for r in (stack, music):
        (r / ".tide").mkdir(parents=True)
    _write(stack, "01-staryi", OLD_TOPIC)
    _write(stack, "02-peredacha-raboty", LIVE_TOPIC, LIVE_FACTS)
    _write(stack, "03-arhiv", ARCH_TOPIC)
    _write(stack, "04-bez-sverki", NOCHECK_TOPIC)
    _write(music, "01-telefon", MUSIC_TOPIC)
    (home / "roster.md").write_text(
        "# tide roster\nstack | {0}\nmusic | {1}\n".format(stack, music), encoding="utf-8")
    monkeypatch.setenv("TIDE_HOME", str(home))
    monkeypatch.chdir(tmp_path)
    return {"stack": stack, "music": music}


def _lines(out: str) -> list:
    return [ln for ln in out.splitlines() if ":" in ln.split(" ")[0]]


def test_stem_and_stopwords():
    assert truth.stem("передача") == "переда" and truth.stem("Контракта") == "контрак"
    assert truth.stem("ждёт") == "ждет" and truth.stem("SOC-102") == "soc-102"
    terms = truth.make_terms(["что решили по", "контракту агентов?"])
    assert [t["word"] for t in terms] == ["решили", "контракту", "агентов"]
    assert truth._hit("переда", "старая сессия передаёт".replace("ё", "е"))
    assert not truth._hit("текс", "контекстные правила")  # короткая — с начала слова


def test_grep_live_words_morphology(live, capsys):
    """п.1: «передача» находит «передаёт» и «передачи», вопрос живыми словами."""
    _, out = _run(capsys, "--grep", "чего ждёт старая сессия от передачи")
    assert "facts.md:16 - Старая сессия передаёт работу" in out
    _, out = _run(capsys, "--grep", "контракта")
    assert "Контракт агентов v1 подписан" in out


def test_grep_partial_ranking_and_empty_hint(live, capsys):
    """п.2: нет строк со всеми основами — большинство с пометкой; пусто — подсказка."""
    _, out = _run(capsys, "--grep", "контракт агентов подписан вчера ночью")
    assert out.splitlines()[0] == "не все слова: совпало 3 из 5 (основы: контра · агент · подпис · вчер · ночь)"
    assert "facts.md:25 - Контракт агентов v1 подписан" in out
    _, out = _run(capsys, "--grep", "кто ведёт SOC-102")  # идентификатор обязателен
    assert "в фактах нет" in out
    assert truth.EMPTY_HINT in out
    data = json.loads(_run(capsys, "--grep", "контракт агентов подписан вчера ночью", "--json")[1])
    assert data["mode"] == "partial" and data["matched"] == 3 and data["n"] == 5


def test_project_word_is_filter(live, capsys):
    """п.3: имя проекта в запросе — фильтр; позиционно — открывает тему проекта."""
    rc, out = _run(capsys, "--grep", "что горит в music")
    assert rc == 0 and out.splitlines()[0] == "проект: music"
    assert "topic.md:6 горит:      проход Гриши" in out and "stack" not in out
    _, out = _run(capsys, "music")
    assert out.startswith("music · 01-telefon — телефон — волна")
    _, out = _run(capsys, "stack")  # несколько тем — список
    assert "stack · 02-peredacha-raboty" in out and "stack · 04-bez-sverki" in out


def test_topic_title_words_narrow(live, capsys):
    """п.3: слова заголовка темы сужают поиск до неё."""
    _, out = _run(capsys, "--grep", "хендофф передача работы")
    assert out.splitlines()[0].startswith("тема: stack/02-peredacha-raboty")
    assert "04-bez-sverki" not in out


def test_grep_skips_service_lines(live, capsys):
    """п.4: заголовки, цитата-пометка, шапка, таблицы, фронтматтер — не ответ."""
    _, out = _run(capsys, "--grep", "передача", "--all")
    nums = {ln.split(" ")[0] for ln in _lines(out)}
    for bad in ("facts.md:1", "facts.md:3", "facts.md:5", "facts.md:8", "facts.md:11",
                "topic.md:1", "topic.md:3"):
        assert bad not in nums
    _, out = _run(capsys, "--grep", "текст контракта")
    assert "facts.md:9 текст:" in out  # «текст:» — содержание, не служебное


def test_grep_hides_replaced(live, capsys):
    """п.5: заменённые факты скрыты в --grep, внизу их число; --all показывает."""
    _, out = _run(capsys, "--grep", "хендофф на")
    assert "200к" not in out and "заменено скрыто: 1 — --all" in out
    _, out = _run(capsys, "--grep", "хендофф на", "--all")
    assert "facts.md:26 - «хендофф на 200к»" in out


def test_grep_freshness_and_dont_read(live, capsys):
    """п.7: без сверки — после свежих с меткой; «не читать» — скрыта, есть с --all."""
    _, out = _run(capsys, "--grep", "хендофф")
    rows = _lines(out)
    assert "01-staryi" not in out.split("скрыта тема")[0]
    assert "скрыта тема stack/01-staryi («не читать» в stack/02-peredacha-raboty/topic.md:9)" in out
    nocheck = [i for i, ln in enumerate(rows) if "[не сверено]" in ln]
    fresh = [i for i, ln in enumerate(rows) if "[" not in ln]
    assert nocheck and fresh and max(fresh) < min(nocheck)
    _, out = _run(capsys, "--grep", "хендофф", "--all")
    assert "topic.md:7 [устарело] где мы: ждём слова" in out


def test_archived_hidden_everywhere(live, capsys):
    """status: archived — нет в списке, в виде темы, в --grep; с --all — с меткой [архив]."""
    _, out = _run(capsys)
    assert "03-arhiv" not in out
    _, out = _run(capsys, "--all")
    assert "stack · 03-arhiv · архивная тема" in out and "[архив]" in out
    _, out = _run(capsys, "arhiv")
    assert "тема скрыта" in out and "Сейчас" not in out
    _, out = _run(capsys, "arhiv", "--all")
    assert out.startswith("stack · 03-arhiv — архивная тема · сверено 2026-09-01 · [архив]")
    _, out = _run(capsys, "--grep", "хендофф")
    assert "архивный" not in out and "скрыта тема stack/03-arhiv (архив)" in out
    _, out = _run(capsys, "--grep", "хендофф", "--all")
    assert "topic.md:7 [архив] где мы: хендофф архивный" in out


def test_grep_order_topic_then_now_open_then_facts(live, capsys):
    """Внутри темы при равном счёте: topic.md → «Сейчас»/«Открыто» → факты."""
    _, out = _run(capsys, "stack/02-peredacha-raboty", "--grep", "хендофф")
    rows = [ln.split(" ")[0] for ln in _lines(out)]
    assert rows == ["topic.md:7", "facts.md:20", "facts.md:24"]


# --- доделки (main · 03): частичные строки, вид темы, вид проекта, держатель нити ---

def test_need_words_threshold():
    """Порог: ≥ половины основ и не дальше одного слова от лучшей строки."""
    assert truth.need_words(1, 1) == 1
    assert truth.need_words(2, 2) == 1  # «кто ведёт SOC-102»: и строка с одним SOC-102
    assert truth.need_words(3, 3) == 2
    assert truth.need_words(4, 4) == 3  # полное совпадение есть — 2 из 4 уже шум
    assert truth.need_words(5, 3) == 3
    assert truth.need_words(6, 2) == 3  # лучшая ниже половины — пусто


CONTRACT_TOPIC = """# 01-kontrakt

title: контракт агентов
kind: topic

где мы:     контракт подписан
твёрдо:     текст контракта агентов — документ Linear
            копия контракта агентов на диске — /disk/agent-contract.md
            контракт агентов v2 — потом
            SOC-102 коммиты в main
            SOC-102 доска — ведёт нить board
сверено:    2026-10-09
"""


@pytest.fixture
def mgmt(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".tide").mkdir(parents=True)
    root = tmp_path / "mgmt"
    (root / ".tide").mkdir(parents=True)
    _write(root, "01-kontrakt", CONTRACT_TOPIC)
    (home / "roster.md").write_text("# tide roster\nmgmt | {0}\n".format(root), encoding="utf-8")
    monkeypatch.setenv("TIDE_HOME", str(home))
    monkeypatch.chdir(tmp_path)
    return root


def test_grep_extra_word_keeps_line(mgmt, capsys):
    """п.1: лишнее слово («лежит», «текст») не отсекает строку с путём на диске."""
    _, out = _run(capsys, "--grep", "где лежит текст контракта агентов на диске")
    assert "topic.md:8 копия контракта агентов на диске — /disk/agent-contract.md" in out
    assert "topic.md:7 твёрдо:     текст контракта агентов" in out
    assert "контракт агентов v2" not in out  # 2 из 5 — ниже порога
    assert out.splitlines()[0].startswith("не все слова: совпало 3 из 5")


def test_grep_ranks_full_then_partial_with_mark(mgmt, capsys):
    """п.1: строки со всеми словами выше, неполные — ниже под «совпало k из n»."""
    _, out = _run(capsys, "--grep", "кто ведёт SOC-102")
    rows = out.splitlines()
    full = rows.index("topic.md:11 SOC-102 доска — ведёт нить board")
    mark = rows.index("совпало 1 из 2:")
    part = rows.index("topic.md:10 SOC-102 коммиты в main")
    assert full < mark < part
    assert "не все слова" not in out  # лучшая строка — со всеми словами
    data = json.loads(_run(capsys, "--grep", "кто ведёт SOC-102", "--json")[1])
    assert data["mode"] == "all" and [h["k"] for h in data["hits"]] == [2, 1]


def test_topic_view_page_first_then_facts(live, capsys):
    """п.2: вид темы — topic.md целиком с номерами, потом «Сейчас»/«Открыто», потом факты."""
    _, out = _run(capsys, "stack/02-peredacha-raboty")
    rows = out.splitlines()
    page = [i for i, ln in enumerate(rows) if ln.startswith("topic.md:")]
    assert [rows[i].split(" ")[0] for i in page] == [
        "topic.md:1", "topic.md:3", "topic.md:4", "topic.md:5", "topic.md:7",
        "topic.md:8", "topic.md:9", "topic.md:10"]
    assert "topic.md:9 не читать:  топик 01-staryi (12.09) — устарел" in rows
    now, opened, facts = rows.index("Сейчас:"), rows.index("Открыто:"), \
        next(i for i, ln in enumerate(rows) if ln.startswith("Факты ("))
    assert max(page) < now < opened < facts
    assert "200к" not in out  # заменённое скрыто
    assert "facts.md:24 - «хендофф на 300к»" in out


PROJECT_TOPIC = """# 01-volna

title: волна
kind: topic

вопрос:     довезти волну
где мы:     хотфиксы на проде
            порядок: Q → L2
твёрдо:     v0.28.3.3 — music@bdc2157
в полёте:   Q #160 — нить main (01-@main)
            J3 #162 ждёт сигнала
горит:      проход Гриши по v0.28.3.4
нити и работы: нить main (01-@main)
дальше:     мерж Q
сверено:    2026-10-09
"""

SECOND_TOPIC = """# 02-lenta

title: лента
kind: topic

где мы:     макет сводят
горит:      —
сверено:    2026-10-09
"""


def test_project_view_where_flying_burning(live, capsys):
    """п.3: `tide truth <проект>` — у каждой темы «где мы», «в полёте», «горит» (+ «дальше»)."""
    _write(live["music"], "02-volna", PROJECT_TOPIC)
    _write(live["music"], "03-lenta", SECOND_TOPIC)
    rc, out = _run(capsys, "music")
    assert rc == 0
    for want in ("topic.md:7 где мы:     хотфиксы на проде",
                 "topic.md:8             порядок: Q → L2",
                 "topic.md:10 в полёте:   Q #160 — нить main (01-@main)",
                 "topic.md:11             J3 #162 ждёт сигнала",
                 "topic.md:12 горит:      проход Гриши по v0.28.3.4",
                 "topic.md:14 дальше:     мерж Q",
                 "topic.md:6 где мы:     макет сводят",
                 "topic.md:7 горит:      —",
                 "вся тема: tide truth music/02-volna (фактов нет)"):
        assert want in out
    assert "твёрдо" not in out and "вопрос" not in out and "нити и работы" not in out
    assert out.index("music · 01-telefon") < out.index("music · 02-volna") < out.index("music · 03-lenta")
    _, same = _run(capsys, "--grep", "music")  # проект словом в --grep — тот же вид
    assert same == out
    data = json.loads(_run(capsys, "stack", "--json")[1])
    assert "03-arhiv" not in [d["slug"] for d in data]  # архив скрыт
    assert any(r["line"] == 7 for d in data if d["slug"] == "02-peredacha-raboty" for r in d["state"])


def _thread(root: Path, thread: str, sessions: dict) -> None:
    for name, sid in sessions.items():
        d = root / ".tide" / "arcs" / thread / "arcs" / name
        d.mkdir(parents=True)
        (d / "arc.md").write_text("# {0}\n\nclaude-session: {1}\n".format(name, sid),
                                  encoding="utf-8")


HOLDER_TOPIC = """# 05-holder

title: держатели
kind: topic

где мы:     SOC-102 доска — ведёт нить board (29-@board), In Review
в полёте:   правила воркеров — нить board (stack, 29-@board), с 2026-10-09
            пакеты — ведёт board · 02
            мысли — нить keeper (34-@keeper)
            призрак — ведёт нить ghost (40-@ghost)
            журнал нити — /x/.tide/arcs/29-@board/arc.md
            читать: нити board план
нити и работы: нить main (31-@main) · нить board (29-@board)
сверено:    2026-10-09
"""


@pytest.fixture
def held(live, tmp_path, monkeypatch):
    """Нить board в stack: новейшая сессия 03 в AO stack-23; keeper — в music, без AO."""
    _thread(live["stack"], "29-@board", {"02-board-2": "sid-2", "03-board-3": "sid-3"})
    _thread(live["music"], "34-@keeper", {"01-keeper": "sid-k"})
    (tmp_path / "home" / "terminals.json").write_text(
        json.dumps({"sid-3": {"handle": "stack-23"}}), encoding="utf-8")
    calls = []

    def probe():
        def alive(handle):
            calls.append(handle)
            return handle == "stack-23"
        return alive
    monkeypatch.setattr(truth, "_alive_probe", probe)
    _write(live["music"], "05-holder", HOLDER_TOPIC)
    return calls


def test_holder_note_on_leads_lines(held, capsys):
    """п.4: строка «ведёт нить …» получает держателя нити; нить в соседнем проекте — тоже."""
    _, out = _run(capsys, "music/05-holder")
    now = "→ сейчас: board · 03, stack-23 (жива)"
    assert "topic.md:6 где мы:     SOC-102 доска — ведёт нить board (29-@board), In Review  " + now in out
    assert "в полёте:   правила воркеров — нить board (stack, 29-@board), с 2026-10-09  " + now in out
    assert "пакеты — ведёт board · 02  " + now in out  # имя сессии → её нить
    assert "мысли — нить keeper (34-@keeper)  → сейчас: keeper · 01\n" in out  # без AO — только сессия
    assert "призрак — ведёт нить ghost (40-@ghost)\n" in out  # нити нет — ничего, не падаем
    assert "журнал нити — /x/.tide/arcs/29-@board/arc.md\n" in out  # адрес в пути — не нить
    assert "нити и работы: нить main (31-@main) · нить board (29-@board)\n" in out  # несколько нитей без «ведёт»
    assert held == ["stack-23"]  # живость спрошена один раз за прогон (кэш)


def test_holder_note_in_grep_and_json(held, capsys):
    """п.4: в --grep и --json держатель рядом со строкой."""
    _, out = _run(capsys, "--grep", "кто ведёт SOC-102 доска")
    assert "ведёт нить board (29-@board), In Review  → сейчас: board · 03, stack-23 (жива)" in out
    data = json.loads(_run(capsys, "--grep", "SOC-102", "--json")[1])
    assert data["hits"][0]["holder"] == "→ сейчас: board · 03, stack-23 (жива)"


def test_holder_missing_is_silent(live, capsys, monkeypatch):
    """п.4: держатель не находится (нет нитей, проба падает) — строка как есть."""
    def boom():
        raise RuntimeError("ao нет")
    monkeypatch.setattr(truth, "_alive_probe", boom)
    _write(live["music"], "05-holder", HOLDER_TOPIC)
    rc, out = _run(capsys, "music/05-holder")
    assert rc == 0 and "→ сейчас" not in out
    assert "ведёт нить board (29-@board), In Review\n" in out


def test_holder_probe_failure_keeps_session(held, capsys, monkeypatch):
    """п.4: AO не достать — держатель всё равно назван, живость «не проверял»."""
    def boom():
        raise RuntimeError("ao нет")
    monkeypatch.setattr(truth, "_alive_probe", boom)
    _, out = _run(capsys, "music/05-holder")
    assert "In Review  → сейчас: board · 03, stack-23 (не проверял)" in out
