"""tide.escalate_rules — пороги «не справляюсь» и правила нити (SOC-157, SOC-181).

Пороги — данные кода (их читает глагол ``tide escalate``). Текст правил нити живёт
ОДНИМ файлом — ``prompts/thread-rules.md`` в репо tide (:func:`rules_file`): сид
передачи и сид запуска ``tide go`` несут на него строку-ссылку (:func:`rules_line`),
``prompts/orchestrator.md``, ``prompts/worker.md`` и ``rules/subagents.md`` ссылаются
туда же. Константы ``*_RULE`` ниже — строки, прочитанные из файла (для подсказки
role-gate и тестов), а не вторая копия текста. Тест сверяет пороги с файлом.

Модуль ничего не импортирует из tide: файл ищется рядом с пакетом (как
:func:`tide.paths.install_root`). Нет файла — правила пустые, ссылка остаётся.
"""

from pathlib import Path
from typing import Dict

FAILED_ATTEMPTS = 2       # неудачных попыток одного шага → эскалируй
STALL_MIN = 30            # минут стопа без прогресса → эскалируй
ACK_TIMEOUT_MIN = 30      # минут без квитанции main → тревога менеджеру

SUBAGENT_RESULT_CHARS = 2000   # ~600 токенов — потолок итога сабагента
AO_SEND_SIGNAL_CHARS = 400     # длиннее — ao send уже не сигнал, а письмо
# Проба менеджера (management-25, обратимо): пауза >5 мин перезаписывает кэш
# (404 $, 16% дня) — сабагент не висит на тестах дольше ~4,5 мин в одном вызове.
TEST_WAIT_SEC = 270

RULES_FILE_NAME = "thread-rules.md"


def rules_file() -> Path:
    """Абсолютный путь файла правил нити: ``<репо tide>/prompts/thread-rules.md``."""
    return Path(__file__).resolve().parents[2] / "prompts" / RULES_FILE_NAME


def rules_line() -> str:
    """Одна строка для сида: где правила нити."""
    return "правила нити: {0} (читать на старте)".format(rules_file())


# Ключ правила → начало его строки в файле («- <начало>…»).
_PREFIXES = (
    ("work", "работа:"),
    ("subagent", "итог сабагента"),
    ("tests", "тесты:"),
    ("mail", "почта:"),
    ("throttle", "троттлинг:"),
    ("models", "модели:"),
    ("experience", "опыт:"),
    ("escalate", "не справляешься"),
    # SOC-242: правила ночи 12.10
    ("questions", "вопрос пользователю:"),
    ("ball", "мяч:"),
    ("limit", "лимит:"),
    ("user_tasks", "дела пользователя:"),
    ("reply", "ответ пользователю:"),
)


def read_rules(path: Path = None) -> Dict[str, str]:
    """``{ключ: строка правила}`` из файла правил нити; нет файла — ``{}``."""
    try:
        text = Path(path or rules_file()).read_text(encoding="utf-8")
    except OSError:
        return {}
    out: Dict[str, str] = {}
    for ln in text.splitlines():
        if not ln.startswith("- "):
            continue
        body = ln[2:].strip()
        for key, head in _PREFIXES:
            if key not in out and body.startswith(head):
                out[key] = body
    return out


_RULES = read_rules()

WORK_RULE = _RULES.get("work", "")
SUBAGENT_RESULT_RULE = _RULES.get("subagent", "")
TEST_WAIT_RULE = _RULES.get("tests", "")
MAIL_RULE = _RULES.get("mail", "")
THROTTLE_RULE = _RULES.get("throttle", "")
MODEL_RULE = _RULES.get("models", "")
EXP_RULE = _RULES.get("experience", "")
ESCALATE_RULE = _RULES.get("escalate", "")
QUESTION_RULE = _RULES.get("questions", "")
BALL_RULE = _RULES.get("ball", "")
LIMIT_RULE = _RULES.get("limit", "")
USER_TASKS_RULE = _RULES.get("user_tasks", "")
REPLY_RULE = _RULES.get("reply", "")


def ao_send_hint(chars: int) -> str:
    """Подсказка role-gate на длинный ao send (не отказ — отправка идёт)."""
    return ("tide · подсказка: ao send {0} знаков (порог ~{1}) — отправка не "
            "остановлена. {2}").format(chars, AO_SEND_SIGNAL_CHARS,
                                       MAIL_RULE or "правила почты — " + str(rules_file()))


def window_rules() -> str:
    """Правила окна полным текстом (итог сабагента, тесты, почта, троттлинг, модели) —
    для тех, кому нужен текст; сид несёт только :func:`rules_line`."""
    return "\n".join(r for r in (SUBAGENT_RESULT_RULE, TEST_WAIT_RULE, MAIL_RULE,
                                 THROTTLE_RULE, MODEL_RULE) if r)


def seed_rules() -> str:
    """Опыт + эскалация полным текстом (``tide escalate --help`` и тесты)."""
    return "\n".join(r for r in (EXP_RULE, ESCALATE_RULE) if r)
