#!/usr/bin/env python3
"""Cold-session benchmark — one command.

Меряет, может ли сессия, которая ничего не знает о нити, разобраться в её
состоянии сама — имея только CLI, файлы нити и правила проекта.

    run.py --ref <профиль.json> facts > /tmp/facts.json   # заморозить эталон
    run.py --ref <профиль.json> questions                 # промпт для сессии
    run.py --facts-file /tmp/facts.json expected          # эталоны + ловушки
    run.py --facts-file /tmp/facts.json score answers.json --verdicts v.json

ПРОФИЛЬ (`--ref`, либо $TIDE_BENCH_REF) говорит, ЧТО меряем: путь к проекту,
нить, сессию, и ту горсть ответов, которую машина вывести не может. Профиль
живёт рядом со своей нитью, НЕ в этом репозитории: он несёт тексты решений,
курсоры и имена работ. Здесь лежит только образец — ref/example-thread.json.

answers.json — {"сессия-1": {"q1-goal-and-step": "...", ...}, ...}
verdicts.json — ручные вердикты там, где машина судить не может:
    {"сессия-1": {"q3-decisions-executed": "wrong-confident"}}
    допустимые: right | wrong-confident | dont-know | asks-human

Метрики: доля верных · не смог · позвал бы человека · УВЕРЕННО НЕВЕРНО.
Последняя — худшая: молчание чинится вопросом, уверенное враньё не чинится
ничем. Каждую её клетку смотри глазами, прежде чем ставить в отчёт: дважды
подряд первая цифра оказывалась хуже правды из-за судьи, а не из-за сессий.
"""

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import facts as F                                    # noqa: E402
from questions import QUESTIONS, ask_text, prompt    # noqa: E402

HERE = Path(__file__).parent
EXAMPLE_REF = HERE / "ref" / "example-thread.json"

DONT_KNOW = ("не знаю", "не нашёл", "не нашел", "не смог", "не удалось",
             "нет данных", "не определ", "unknown", "not found")
ASKS_HUMAN = ("спросить у человека", "спросить человека", "уточнить у владельца",
              "нужно уточнить у", "ask the human", "спросить владельца")


def _profile(args):
    ref = args.ref or os.environ.get("TIDE_BENCH_REF")
    if not ref:
        sys.exit(
            "нужен профиль нити: --ref <файл.json> или $TIDE_BENCH_REF\n"
            "образец полей: {0}\n"
            "профиль держи рядом со своей нитью, не в этом репозитории — "
            "он несёт её тексты.".format(EXAMPLE_REF))
    path = Path(ref).expanduser()
    if not path.exists():
        sys.exit("профиль не найден: {0}".format(path))
    return path, json.loads(path.read_text(encoding="utf-8"))


def build(args):
    """Свежие факты — или снимок, сделанный перед раздачей вопросов.

    Нить движется, пока идёт прогон: за два замера её правили пять раз —
    дописывали решения, переписывали план, дважды меняли курсор. Поэтому эталон
    замораживается ДО раздачи (`facts > snapshot.json`) и сверка идёт с ним.
    """
    if getattr(args, "facts_file", None):
        return json.loads(Path(args.facts_file).read_text(encoding="utf-8"))

    ref_path, prof = _profile(args)
    project = Path(args.project or prof["project"]).expanduser()
    thread = project / (args.thread or prof["thread"])
    session = project / (args.session or prof["session"])
    f = F.derive(project, thread, session)
    f.update(prof)
    f["project"] = str(project)
    f["_ref_path"] = str(ref_path)
    return f


def classify(answer, question, f):
    """right | wrong-confident | dont-know | asks-human | manual

    Порядок важен. Сначала машина проверяет содержание, и только потом ищет
    «не знаю»: живые ответы часто верны целиком и несут узкую честную оговорку
    («какие ещё два из трёх — не знаю»). Первый прогон засчитал такой ответ
    как несмог, хотя всё существенное в нём было названо верно.
    """
    low = (answer or "").lower()
    verdict = question["auto"](answer or "", f)
    if verdict is True:
        return "right"
    if any(s in low for s in ASKS_HUMAN):
        return "asks-human"
    if any(s in low for s in DONT_KNOW):
        return "dont-know"
    return "manual" if verdict is None else "wrong-confident"


def cmd_facts(args):
    print(json.dumps(build(args), ensure_ascii=False, indent=2))


def cmd_questions(args):
    print(prompt(build(args)))


def cmd_expected(args):
    f = build(args)
    print("# эталоны — сняты с живой нити {0}".format(f["thread_name"]))
    print("# профиль: {0} ({1})\n".format(
        f.get("_ref_path", "—"), f.get("_curated_at", "дата не указана")))
    for i, q in enumerate(QUESTIONS, 1):
        print("Q{0}. {1}".format(i, ask_text(q, f)))
        print("    ЭТАЛОН: {0}".format(q["ref"](f)))
        print("    ЛОВУШКА: {0}".format(q["trap"]))
        print("    СУДЬЯ: {0}\n".format(
            "машина" if q["auto"]("", f) is not None else "человек"))


def cmd_score(args):
    f = build(args)
    answers = json.loads(Path(args.answers).read_text(encoding="utf-8"))
    overrides = (json.loads(Path(args.verdicts).read_text(encoding="utf-8"))
                 if args.verdicts else {})

    tally, rows = {}, []
    for agent, replies in answers.items():
        for i, q in enumerate(QUESTIONS, 1):
            a = replies.get(q["id"], "")
            v = overrides.get(agent, {}).get(q["id"]) or classify(a, q, f)
            tally[v] = tally.get(v, 0) + 1
            rows.append((agent, "Q{0}".format(i), v, (a or "").replace("\n", " ")[:160]))

    total = sum(tally.values()) or 1
    print("# прогон: {0} сессий × {1} вопросов = {2} ответов\n".format(
        len(answers), len(QUESTIONS), total))
    for agent, qn, v, a in rows:
        print("{0:12} {1:4} {2:16} {3}".format(agent, qn, v, a))
    print()
    for k, ru in [("right", "верно"), ("wrong-confident", "УВЕРЕННО НЕВЕРНО"),
                  ("dont-know", "не смог"), ("asks-human", "позвал человека"),
                  ("manual", "ждёт вердикта человека")]:
        n = tally.get(k, 0)
        if n or k != "manual":
            print("{0:24} {1:3}  {2:5.1f}%".format(ru, n, 100.0 * n / total))
    if tally.get("wrong-confident"):
        print("\n^ каждую клетку «уверенно неверно» проверь глазами: судья "
              "ошибался в эту сторону дважды")
    if tally.get("manual"):
        print("^ manual: прогони с --verdicts, иначе доля верных неполная")


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--ref", help="профиль нити (.json); либо $TIDE_BENCH_REF")
    p.add_argument("--project", help="перекрыть путь к проекту из профиля")
    p.add_argument("--thread", help="перекрыть нить из профиля")
    p.add_argument("--session", help="перекрыть сессию из профиля")
    p.add_argument("--facts-file", help="снимок эталона, сделанный до раздачи вопросов")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("facts")
    sub.add_parser("questions")
    sub.add_parser("expected")
    s = sub.add_parser("score")
    s.add_argument("answers")
    s.add_argument("--verdicts")
    args = p.parse_args()
    {"facts": cmd_facts, "questions": cmd_questions,
     "expected": cmd_expected, "score": cmd_score}[args.cmd](args)


if __name__ == "__main__":
    main()
