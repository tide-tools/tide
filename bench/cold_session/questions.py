"""Cold-session benchmark — the question set and its checks.

What is measured: can a session that knows nothing about a thread work out that
thread's state on its own, using only the CLI, the files and the project rules?
Not code speed, not test coverage.

Nothing here names a particular thread. Everything thread-specific — which
project, which report is the main one, what the thread accepts a change by —
comes from a PROFILE (ref/*.json, see README). The questions themselves never
change: that is what makes two runs comparable.

A question earns its place here only if its answer is checkable: a number, a
name, a path, a yes/no. Essays are out.
"""

import re


def _has(text, *needles):
    low = text.lower()
    return all(n.lower() in low for n in needles)


def _any(text, *needles):
    low = text.lower()
    return any(n.lower() in low for n in needles)


# Живые сессии пишут числа словами не реже, чем цифрами («десять работ в review»).
# Первый прогон засчитал два верных ответа как уверенно неверные ровно из-за этого.
_WORDS = {1: "один", 2: "два", 3: "три", 4: "четыр", 5: "пят", 6: "шест",
          7: "сем", 8: "восем", 9: "девят", 10: "десят", 11: "одиннадцат",
          12: "двенадцат", 26: "двадцать шест", 27: "двадцать седьм",
          31: "тридцать один"}

_STOP = {"этой", "нити", "того", "чтобы", "после", "перед", "через", "прямо",
         "сейчас", "ещё", "плюс", "идёт", "идет", "тоже", "пути", "себе"}


def _cursor_words(cursor):
    """Содержательные слова живого курсора — по ним сверяется ответ на q4."""
    ws = re.findall(r"[А-Яа-яЁёA-Za-z0-9.]{5,}", cursor.lower())
    return [w for w in ws if w not in _STOP][:12]


def _num(text, n):
    try:
        n = int(n)
    except (TypeError, ValueError):
        return False
    return re.search(r"\b{0}\b".format(n), text) is not None or (
        _WORDS.get(n, "\0\0") in text.lower())


QUESTIONS = [
    dict(
        id="q1-goal-and-step",
        ask="Какая цель у нити {thread} и на каком шаге плана она стоит прямо "
            "сейчас? Назови номер шага и его название.",
        ref=lambda f: (
            "цель — {0}; текущий шаг — {1} «{2}»".format(
                f["thread_goal"], f["current_step_no"], f["current_step_name"])
        ),
        auto=lambda a, f: _num(a, f["current_step_no"]) and _any(a, *f["current_step_words"]),
        trap="назвать текущим шаг, который уже закрыт, или сказать «шаг не указан»",
    ),
    dict(
        id="q2-open-steps",
        ask="Сколько всего шагов в плане нити и какие из них ещё НЕ закрыты?",
        ref=lambda f: (
            "{0} шагов; не закрыты: ".format(f["steps_total"])
            + ", ".join("{0} ({1})".format(n, t) for n, t in f["open_steps"])
            + ("; снят: " + ", ".join(n for n, _ in f["withdrawn_steps"])
               if f["withdrawn_steps"] else "")
        ),
        auto=lambda a, f: (_num(a, f["steps_total"])
                           and all(_num(a, n) for n, _ in f["open_steps"])),
        trap="перечислить как открытые шаги, отмеченные [x]; принять снятый [~] за открытый",
    ),
    dict(
        id="q3-decisions-executed",
        ask="Сколько решений принято в этой нити и какие из них подписаны, но "
            "НЕ выполнены? Перечисли номера.",
        ref=lambda f: (
            "{0} решений. Выполненность читается полем `done:` (плюс `proof:`), "
            "отдельным от `status:`. Подписаны и НЕ выполнены — {1}: {2}. "
            "Выполнено — {3}: {4}. Стоящих правил (выполненности не бывает) — "
            "{5}: {6}".format(
                f["decisions_total"],
                len(f["decisions_not_done"]), ", ".join(f["decisions_not_done"]),
                len(f["decisions_done"]), ", ".join(f["decisions_done"]),
                len(f["decisions_rules"]), ", ".join(f["decisions_rules"]))
        ),
        auto=lambda a, f: None,  # список решений — вердикт человека
        trap="сказать «все accepted, значит все выполнены»; спутать `status:` с "
             "`done:`; посчитать стоящие правила невыполненными обещаниями",
    ),
    dict(
        id="q4-cursor",
        ask="Что делается в нити прямо сейчас? Отвечай тем, что записано "
            "в машине, а не догадкой.",
        ref=lambda f: f["cursor"],
        # Не подшивать сюда слова конкретного курсора: он переписывается по
        # нескольку раз в день. Судим совпадением со СВЕЖИМ курсором — два
        # содержательных слова из него в ответе.
        auto=lambda a, f: sum(
            1 for w in _cursor_words(f["cursor"]) if w in a.lower()) >= 2,
        trap="пересказать вчерашний курсор из сида вместо живого",
    ),
    dict(
        id="q5-waiting-on-human",
        ask="Сколько работ ждёт руки человека (стоят в review) и кто именно "
            "переводит работу в done?",
        ref=lambda f: (
            "{0} работ в review по машине ({1}), из них {2} принадлежат этой "
            "нити — верен любой из двух счётов; done ставит только человек — "
            "кнопкой на доске или `tide work close NN --word \"…\"`; "
            "агент не может".format(
                f["review_count"], ", ".join(f["review_ids"]),
                f["review_count_thread"])
        ),
        auto=lambda a, f: (
            (_num(a, f["review_count"]) or _num(a, f["review_count_thread"]))
            and _any(a, "человек", "рук", "--word", *f.get("human_words", []))
        ),
        trap="назвать число всех незакрытых работ вместо тех, что в review, "
             "или сказать, что done ставит агент",
    ),
    dict(
        id="q6-day-history",
        ask="Где лежит история сегодняшнего дня этой нити — отчёты и разборы? "
            "Дай путь и назови главный файл.",
        ref=lambda f: (
            "{0} — {1} отчётов, главный {2}; плюс строки пульса в разделе "
            "`## context` паспорта {3}".format(
                f["workspace_rel"], len(f["workspace_files"]),
                f["main_report"], f["arc_rel"])
        ),
        auto=lambda a, f: _has(a, "workspace") and _has(a, f["main_report"]),
        trap="указать на workspace нити уровнем выше вместо workspace сессии, "
             "или на git log как единственную историю",
    ),
    dict(
        id="q7-rejected",
        ask="Назови минимум три варианта, которые эта нить уже отвергла и "
            "перерешивать нельзя.",
        ref=lambda f: "любые три из {0} (поле closes): ".format(len(f["rejected"]))
                      + " · ".join(f["rejected"][:8]) + " …",
        auto=lambda a, f: None,  # вердикт человека: ≥3 верных и ни одного выдуманного
        trap="выдать за отвергнутое то, что просто не сделано",
    ),
    dict(
        id="q8-version",
        ask="Какая версия tide выкачена последней и что печатает `tide --version` "
            "на этой машине? Совпадают ли они?",
        ref=lambda f: (
            "последний ВЫКАЧЕННЫЙ релиз — {0} (тег); `tide --version` печатает "
            "{1} (исходник pyproject {2}); {3}".format(
                f["released_version"], f["cli_version"], f["source_version"],
                "НЕ совпадают" if f["released_version"] != f["cli_version"]
                else "совпадают")
        ),
        auto=lambda a, f: (
            _has(a, f["released_version"], f["cli_version"])
            and _any(a, "не совпад", "расход", "врёт", "врет", "разн", "опереж",
                     "обгон", "впереди", "ещё не выкач", "не выкачен")
        ) if f["released_version"] != f["cli_version"] else _has(a, f["cli_version"]),
        trap="назвать одну версию как факт — самый частый уверенный неверный "
             "ответ; принять исходник pyproject за выкаченный релиз",
    ),
    dict(
        id="q9-acceptance-criterion",
        ask="Чем в этой нити доказывается, что правка под релиз принята — "
            "зелёными тестами или чем-то другим?",
        ref=lambda f: f["acceptance_answer"],
        auto=lambda a, f: _any(a, *f["acceptance_keywords"]),
        trap="ответить «тесты зелёные» там, где нить подписала другой критерий",
    ),
    dict(
        id="q10-remaining",
        ask="Что буквально осталось между сегодняшним днём и финалом нити? "
            "Перечисли пункты.",
        ref=lambda f: "; ".join(f["remaining"]),
        auto=lambda a, f: None,  # вердикт человека: ≥3 из 4 без выдумок
        trap="пересказать открытые шаги плана вместо реального остатка",
    ),
]


PROMPT_HEADER = """Ты поднят в проекте {project}.

В проекте стоит оркестрационная машина tide — есть CLI `tide` и слой ниток
в .tide/arcs/. Мне надо въехать в нить {thread}, но пересказывать
её я не буду: разберись сам.

Пользуйся чем угодно — CLI, файлами, скиллами. Отвечай коротко и по делу.
Если чего-то не нашёл или не уверен — так и пиши «не знаю» / «не нашёл»,
не додумывай: неверный уверенный ответ хуже честного «не знаю».

Формат ответа — ровно по номерам:

{numbered}

Верни ответ блоком:
Q1: …
Q2: …
…
"""


def ask_text(q, facts):
    return q["ask"].format(thread=facts["thread_name"])


def prompt(facts):
    numbered = "\n".join("Q{0}. {1}".format(i, ask_text(q, facts))
                         for i, q in enumerate(QUESTIONS, 1))
    return PROMPT_HEADER.format(project=facts["project"],
                                thread=facts["thread_name"],
                                numbered=numbered)
