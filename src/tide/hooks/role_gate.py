"""tide.hooks.role_gate — PreToolUse role-capability gate.

Enforces the HEAD/worker role split at the tool level: when TIDE_ROLE=orchestrator
the hook PHYSICALLY FORBIDS the tools that belong to worker-sessions (Write, Edit,
NotebookEdit, mutating Bash). The orchestrator only reads, talks, and runs the
tide CLI; all build-work is dispatched via the Agent tool.

When TIDE_ROLE is anything other than ``orchestrator`` (worker, unset, empty) the
hook is a **pure no-op** — workers keep full Write/Edit/Bash capability.

Subagents are also a no-op, regardless of the role they inherited. A subagent is
spawned as a child process and picks up the head's ``TIDE_ROLE=orchestrator``
from the environment, so the env alone cannot tell head from worker. The payload's
``agent_id`` can: Claude Code sends it only for calls made inside a subagent.
Without this, the gate denies the very dispatch path it tells the head to use,
and no one in the session can build.

Protocol (same as ``edit_gate``):

* Reads the Claude Code PreToolUse JSON payload from stdin.
* Exits ``0`` (allow) or ``2`` (block + reason on stderr).
* A garbled / missing payload is treated as "allow" — the gate never wedges a
  session shut on a parse error.

Decision logic lives in :func:`decide` (pure, argparse-free, unit-testable);
:func:`cmd_role_gate` is the thin CLI handler.

Bash allowlist (conservative — unrecognised patterns are DENIED):

The command is first split into segments on the shell operators ``|``, ``||``,
``&&``, ``&``, ``;`` and the newline (respecting quotes), and is allowed only
when EVERY segment is allowed on its own. So ``git status | head -5`` passes
while ``ls | rm -rf .`` does not — pipes and chains cost the head nothing, but
each link still has to earn its way in.

A single segment is allowed when:

* ``tide <anything>`` or bare ``tide`` → ALLOW (orchestration work).
* Read-only git: ``git status``, ``git log``, ``git diff``, ``git show``,
  ``git rev-parse``, ``git branch`` (without ``-D/--delete/-d``),
  ``git worktree list``, ``git remote``, ``git remote -v``.
  NOT: ``git commit``, ``git push``, ``git merge``, ``git branch -D``,
  ``git worktree add/remove``.
* Its first token is a read-only builtin — see ``_READONLY_BUILTINS``.

And DENIED regardless of the above when it contains:

* A file redirect (``>``, ``>>``) — those always write. Stderr housekeeping
  (``2>&1``, ``2>/dev/null``) is exempt: it dups or discards a descriptor, it
  does not touch a file.
* Command substitution (``$(…)``, backticks, ``${…}``, ``<(…)``) — otherwise
  ``echo $(rm -rf .)`` would ride in on the allowed first token ``echo``.

Both scans read only the text the shell would act on: a ``>`` or ``<`` typed
inside an argument (``tide candidate add "стрелка → и <скобки>"``) is literal
text, not syntax. Double quotes are the exception the shell itself makes —
``"$(rm -rf .)"`` still runs, so the substitution scan looks inside them.

Unbalanced quotes make the split unreliable, so they DENY (fail safe).

Probing this gate — the one rule
--------------------------------

Ask the manikin, never the shell::

    tide hook role-gate --explain-file /tmp/probe.txt   # the command lives in a file
    tide hook role-gate --explain 'ls | head -3'        # inline, only when it is tame

It prints the verdict, the link that refused and the rule, and executes nothing.
Do NOT test the gate by running the dangerous string: on 31.07 a worker checking
these rules typed ``$(rm -rf .)`` into a live Bash and the substitution ran for
real — only ``rm``'s refusal to delete ``.`` saved the tree (candidate 166).
Workers are ungated by design, so nothing catches this but the habit. A file is
the safest input: it carries ``$(…)`` as characters, an argument carries it as
something the shell may expand before tide is ever reached.
"""

from __future__ import annotations

import json
import re
import shlex
import sys
from typing import List, NamedTuple, Optional, Tuple

# PreToolUse exit-code protocol (Claude Code): 0 = allow, 2 = block + stderr reason.
ALLOW_EXIT = 0
BLOCK_EXIT = 2

# The cage, in one sentence. Shared with the SessionStart role reminder
# (``hooks.session_start.ROLE_REMINDERS``) so the head learns its limits ON THE
# WAY IN and meets the very same wording on the way out — one list, one place,
# no drift between the promise and the refusal.
ALLOWED_SURFACE = (
    "Read/Grep/Glob, any `tide …`, read-only git (status/log/diff/show/rev-parse, "
    "branch/remote listing, worktree list), and ls/cat/pwd/cd/find/grep/head/tail/"
    "wc/sort/uniq/echo — including `|` pipes, `&&` / `||` chains built only from "
    "those, and `2>&1` / `2>/dev/null`. Denied: file redirects (`>`), command "
    "substitution, and anything not on this list."
)

DENY_MESSAGE = (
    "tide: you are the HEAD (orchestrator) — this is worker-work. "
    "Dispatch it via the Agent tool; the head only reads, talks, and runs the tide CLI. "
    "Subagents you dispatch have full hands — they are not gated.\n"
    "Allowed for you here: " + ALLOWED_SURFACE
)

# Tools unconditionally blocked for the orchestrator role.
_BLOCKED_TOOLS = frozenset({"Write", "Edit", "NotebookEdit"})

# Read-only git subcommands (first non-flag token after ``git``).
_READONLY_GIT_SUBCOMMANDS = frozenset({
    "status", "log", "diff", "show", "rev-parse",
})

# Shell utilities that only read — safe as a whole command or as a pipe link.
_READONLY_BUILTINS = frozenset({
    "ls", "cat", "pwd", "find", "grep", "echo",
    "cd", "head", "tail", "wc", "sort", "uniq", "rg", "which", "file", "stat",
    "diff", "tree",
})

# git branch flags that mean destructive deletion.
_GIT_BRANCH_DELETE_FLAGS = frozenset({"-D", "--delete", "-d"})

# Global git flags whose VALUE is a separate token (``git -C /path status``).
# The subcommand scan has to step over the value too, or it mistakes ``/path``
# for the subcommand and denies a read-only command. Spelled out explicitly
# rather than guessed: an "any flag may eat the next token" heuristic would let
# ``git --foo commit -m x`` swallow ``commit`` and pass the gate. The glued
# forms (``--git-dir=…``, ``-c k=v``) are single tokens and already work.
_GIT_GLOBAL_FLAGS_WITH_VALUE = frozenset({
    "-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path",
})

# Shell operators that separate one command from the next. Longest first —
# ``||`` must win over ``|``, ``&&`` over ``&``. A newline separates commands
# exactly like ``;`` does, and the Bash tool happily takes a multi-line string:
# without it here, ``ls\nrm -rf x`` was ONE segment whose first token (``ls``)
# waved the whole thing through. A backslash-escaped newline is a line
# continuation, not a separator — the escape branch of the splitter eats it
# before this list is consulted.
_SEGMENT_OPERATORS = ("||", "&&", "|", "&", ";", "\n")

# Stderr housekeeping: dup onto stdout, or throw it away. Neither writes a file,
# and both ride along with read-only commands all the time (``tide status
# 2>/dev/null``). Kept whole by the splitter (so the ``&`` in ``2>&1`` never
# separates segments) and dropped before the redirect check.
_STDERR_REDIRECT_RE = re.compile(r"2>\s*(?:&1|/dev/null)")

# Substrings that open a subshell whose contents the allowlist cannot see.
# Double quotes do NOT stop these — ``echo "$(rm -rf .)"`` runs the subshell —
# so they are scanned inside quoted arguments too.
_SUBSTITUTION_MARKERS = ("$(", "`", "${")

# Process substitution, which double quotes DO neutralise (``echo "<(ls)"`` just
# prints the text). Scanned only outside quotes.
_PROCESS_SUBSTITUTION_MARKERS = ("<(",)

# Why a command was refused, in the gate's own words. These strings ARE the
# explainer's output (:func:`explain`), so a probe can never name a rule the
# gate does not apply — there is one implementation and two views of it.
_RULE_SUBSTITUTION = "command substitution — a subshell hides its command from the allowlist"
_RULE_PROCESS_SUBSTITUTION = "process substitution — a subshell hides its command from the allowlist"
_RULE_REDIRECT = "file redirect (`>`) — that writes"
_RULE_UNPARSEABLE = "unparseable quoting inside the link"
_RULE_GIT_NOT_READONLY = "git subcommand is not read-only"
_RULE_NOT_ALLOWED = "not on the read-only allowlist"
_RULE_UNBALANCED_QUOTES = (
    "unbalanced quotes — a command the gate cannot parse is one it cannot vouch for"
)


class Refusal(NamedTuple):
    """The one link that made the gate say no, and the rule that did it.

    *index* / *total* are 1-based for reading aloud; *segment* is empty and
    *index* is 0 when the whole command failed to parse.
    """

    index: int
    total: int
    segment: str
    rule: str


# --- bash allowlist -----------------------------------------------------------

def _is_git_subcommand_allowed(cmd: str) -> bool:
    """Return True when *cmd* (a ``git …`` string) is a read-only git operation."""
    try:
        parts = shlex.split(cmd)
    except ValueError:
        return False

    if len(parts) < 2:
        return True  # bare ``git`` → shows help, read-only

    # Skip global flags (e.g. -C /dir, --git-dir=…) to find the subcommand.
    idx = 1
    while idx < len(parts) and parts[idx].startswith("-"):
        takes_value = parts[idx] in _GIT_GLOBAL_FLAGS_WITH_VALUE
        idx += 2 if takes_value else 1

    if idx >= len(parts):
        return True  # only global flags, no subcommand

    subcmd = parts[idx]
    rest = parts[idx + 1:]

    if subcmd in _READONLY_GIT_SUBCOMMANDS:
        return True

    if subcmd == "branch":
        # Allow listing (``git branch``, ``-a``, ``-r``); deny any delete flag.
        return not any(arg in _GIT_BRANCH_DELETE_FLAGS for arg in rest)

    if subcmd == "remote":
        # Allow bare ``git remote`` and ``git remote -v`` only.
        return rest in ([], ["-v"])

    if subcmd == "worktree":
        # Allow only ``git worktree list``.
        return bool(rest) and rest[0] == "list"

    return False


def _split_segments(command: str) -> Optional[List[str]]:
    """Split *command* on shell operators, ignoring operators inside quotes.

    Returns the list of segments, or ``None`` when the quoting is unbalanced —
    the caller treats that as "deny", since a command we cannot parse is a
    command we cannot vouch for.
    """
    segments: List[str] = []
    buf: List[str] = []
    quote = ""  # "" outside quotes, otherwise the opening quote character
    idx = 0
    end = len(command)

    while idx < end:
        char = command[idx]

        if quote:
            buf.append(char)
            if char == "\\" and quote == '"' and idx + 1 < end:
                buf.append(command[idx + 1])
                idx += 2
                continue
            if char == quote:
                quote = ""
            idx += 1
            continue

        if char in ("'", '"'):
            quote = char
            buf.append(char)
            idx += 1
            continue

        if char == "\\" and idx + 1 < end:
            buf.append(char)
            buf.append(command[idx + 1])
            idx += 2
            continue

        # Keep ``2>&1`` intact so its ``&`` is not read as a separator.
        stderr_redirect = _STDERR_REDIRECT_RE.match(command, idx)
        if stderr_redirect:
            buf.append(stderr_redirect.group())
            idx = stderr_redirect.end()
            continue

        operator = next(
            (op for op in _SEGMENT_OPERATORS if command.startswith(op, idx)), None
        )
        if operator:
            segments.append("".join(buf))
            buf = []
            idx += len(operator)
            continue

        buf.append(char)
        idx += 1

    if quote:
        return None  # unterminated quote

    segments.append("".join(buf))
    return segments


def _unquoted(segment: str, *, expand_double: bool) -> str:
    """Return *segment* with quoted text blanked out, leaving the live shell syntax.

    Scanning the result instead of the raw string is what lets an argument carry
    the characters the gate treats as syntax: ``tide candidate add "гейт видит
    <скобки> и стрелку →"`` is one command with one argument, not a redirect.

    *expand_double* keeps the inside of double quotes visible, for the one scan
    that needs it: the shell still runs ``"$(rm -rf .)"``, so command
    substitution has to be hunted in there, while ``>`` is literal in quotes of
    either kind.
    """
    out: List[str] = []
    quote = ""  # "" outside quotes, otherwise the opening quote character
    idx = 0
    end = len(segment)

    while idx < end:
        char = segment[idx]

        if quote:
            if char == "\\" and quote == '"' and idx + 1 < end:
                out.append("  ")  # escaped inside quotes → literal, whatever it is
                idx += 2
                continue
            if char == quote:
                quote = ""
                out.append(" ")
                idx += 1
                continue
            out.append(char if (expand_double and quote == '"') else " ")
            idx += 1
            continue

        if char in ("'", '"'):
            quote = char
            out.append(" ")
            idx += 1
            continue

        if char == "\\" and idx + 1 < end:
            out.append("  ")  # ``echo \$\(x\)`` is text, not a subshell
            idx += 2
            continue

        out.append(char)
        idx += 1

    return "".join(out)


def _segment_refusal(segment: str) -> Optional[str]:
    """Return the rule refusing one pipeline/chain link, or ``None`` when it passes.

    The single place a link is judged. :func:`_is_segment_allowed` is the boolean
    view the gate runs on, :func:`explain` is the spoken view the probe prints —
    both read this, so they cannot tell different stories.
    """
    seg = segment.strip()
    if not seg:
        return None  # empty link (e.g. a trailing ``;``) does nothing

    # A subshell hides its command from the allowlist — ``echo $(rm -rf .)``
    # would otherwise pass on its first token alone. Quoting decides where to
    # look: double quotes stop process substitution but not ``$(…)``.
    bare = _unquoted(seg, expand_double=False)
    expandable = _unquoted(seg, expand_double=True)
    if any(marker in expandable for marker in _SUBSTITUTION_MARKERS):
        return _RULE_SUBSTITUTION
    if any(marker in bare for marker in _PROCESS_SUBSTITUTION_MARKERS):
        return _RULE_PROCESS_SUBSTITUTION

    # Any ``>`` the shell would act on writes to a file — stderr housekeeping
    # (``2>&1``, ``2>/dev/null``) excepted, and quoted text is an argument.
    if ">" in _STDERR_REDIRECT_RE.sub(" ", bare):
        return _RULE_REDIRECT

    # Drop the stderr redirect from the command too, so the dispatch below reads
    # the plain command it belongs to.
    seg = _STDERR_REDIRECT_RE.sub(" ", seg).strip()
    if not seg:
        return None

    # Tide commands are always allowed (orchestration surface, not worker-work).
    if seg == "tide" or seg.startswith("tide "):
        return None

    # Git: apply the read-only subcommand allowlist.
    if seg == "git" or seg.startswith("git "):
        return None if _is_git_subcommand_allowed(seg) else _RULE_GIT_NOT_READONLY

    try:
        parts = shlex.split(seg)
    except ValueError:
        return _RULE_UNPARSEABLE

    if parts and parts[0] in _READONLY_BUILTINS:
        return None
    return _RULE_NOT_ALLOWED


def _is_segment_allowed(segment: str) -> bool:
    """Return True when one pipeline/chain link is safe for the orchestrator."""
    return _segment_refusal(segment) is None


def _bash_refusal(command: str) -> Optional[Refusal]:
    """Return the first link that refuses *command*, or ``None`` when it all passes.

    Nothing here executes anything — the command is only ever split and read.
    """
    cmd = command.strip()
    if not cmd:
        return None  # empty → allow; nothing happens

    segments = _split_segments(cmd)
    if segments is None:
        return Refusal(0, 0, "", _RULE_UNBALANCED_QUOTES)

    for position, segment in enumerate(segments, start=1):
        rule = _segment_refusal(segment)
        if rule is not None:
            return Refusal(position, len(segments), segment.strip(), rule)
    return None


def _is_bash_allowed(command: str) -> bool:
    """Return True when *command* is safe for an orchestrator to run directly.

    Pipes and ``&&`` / ``||`` chains are fine as long as every link is fine on
    its own; unknown commands, file redirects and command substitution are denied.
    """
    return _bash_refusal(command) is None


# --- pure decision ------------------------------------------------------------

def decide(
    tool_name: str,
    tool_input: dict,
    role: str,
    *,
    is_subagent: bool = False,
) -> Tuple[bool, str]:
    """Decide whether the tool call is permitted for *role*.

    Returns ``(allow, reason)``.  When *allow* is ``True`` *reason* is empty.
    When *allow* is ``False`` *reason* carries the re-teaching denial message.

    Non-orchestrator roles (worker, unset) are always allowed — the gate is a
    pure no-op for them.  So is a subagent, whatever role it inherited — see
    below.
    """
    # A subagent IS the dispatch path this gate points at. It is spawned as a
    # child process and therefore inherits the head's TIDE_ROLE, but it carries
    # worker capability by definition — denying it would wedge the gate against
    # its own instruction ("dispatch it via the Agent tool") and leave nobody
    # able to build.
    if is_subagent:
        return True, ""

    # Workers (and unset / any other role) have full tool capability.
    if role != "orchestrator":
        return True, ""

    # Write / Edit / NotebookEdit are unconditionally worker-work.
    if tool_name in _BLOCKED_TOOLS:
        return False, DENY_MESSAGE

    # Bash: check against the conservative allowlist.
    if tool_name == "Bash":
        command = ""
        if isinstance(tool_input, dict):
            raw = tool_input.get("command", "")
            if isinstance(raw, str):
                command = raw
        if _is_bash_allowed(command):
            return True, ""
        return False, DENY_MESSAGE

    # Read, Grep, Glob, Agent, Task and anything else → always allow.
    return True, ""


# --- the dummy: ask the gate without running anything -------------------------

def _labelled(label: str, text: str) -> List[str]:
    """``label: text`` on one line, or a label with an indented block when *text* wraps."""
    body = text.split("\n")
    if len(body) == 1:
        return ["  {0}: {1}".format(label, body[0])]
    return ["  {0}:".format(label)] + ["    {0}".format(line) for line in body]


def explain(command: str) -> str:
    """Render the gate's verdict on *command* for a HEAD, without running it.

    The manikin behind ``tide hook role-gate --explain``. It answers the one
    question that used to cost a real execution to ask — "how would you cut
    this?" — by parsing and reading the string, never handing it to a shell.

    The verdict comes from :func:`decide` and the detail from
    :func:`_bash_refusal`, i.e. from the very code the live hook runs: a probe
    that re-implemented the rules would drift and then lie, which is worse than
    no probe at all.
    """
    allow, _ = decide("Bash", {"command": command}, "orchestrator")

    lines = [
        "tide: role-gate would {0} this for the orchestrator "
        "(dry parse — nothing ran).".format("ALLOW" if allow else "DENY")
    ]
    lines += _labelled("command", command)

    refusal = _bash_refusal(command)
    if refusal is None:
        links = _split_segments(command.strip()) or []
        kept = [seg.strip() for seg in links if seg.strip()]
        if kept:
            lines.append("  links: " + " · ".join("`{0}`".format(seg) for seg in kept))
        return "\n".join(lines)

    if refusal.segment:
        lines.append("  refused: link {0} of {1} · `{2}`".format(
            refusal.index, refusal.total, refusal.segment
        ))
    lines.append("  rule: {0}".format(refusal.rule))
    lines.append("  allowed: {0}".format(ALLOWED_SURFACE))
    return "\n".join(lines)


def _explain_source(args) -> Tuple[Optional[str], Optional[str]]:
    """Pull the command to explain off *args* → ``(command, error)``.

    ``--explain-file`` exists so the probe string never has to survive a trip
    through a live shell: a file carries ``$(rm -rf .)`` as eleven characters,
    an argument carries it as an instruction the shell may expand before tide is
    even reached. That is the whole point of the manikin — see candidate 166.
    """
    from pathlib import Path

    inline = getattr(args, "explain", None)
    source = getattr(args, "explain_file", None)

    if inline is not None and source is not None:
        return None, "tide: pass --explain OR --explain-file, not both"
    if inline is not None:
        return inline, None
    if source is None:
        return None, None

    path = Path(source)
    try:
        # A trailing newline is how files end, not part of the command; anything
        # else newline-ish IS a separator and the splitter must see it.
        return path.read_text(encoding="utf-8").rstrip("\n"), None
    except OSError as exc:
        return None, "tide: cannot read {0}: {1}".format(path, exc)


# --- payload parsing ----------------------------------------------------------

def _read_payload(stream_in) -> dict:
    """Parse the Claude Code PreToolUse JSON payload from *stream_in* (lenient)."""
    try:
        raw = stream_in.read()
    except (OSError, ValueError):
        return {}
    if not raw or not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


# --- CLI handler --------------------------------------------------------------

def cmd_role_gate(args) -> int:
    """``tide hook role-gate`` — the dispatched PreToolUse handler.

    Reads the tool payload from stdin, decides based on TIDE_ROLE, and exits 0
    (allow) or 2 (block, reason on stderr). A missing/garbled payload is treated
    as "allow" so the gate never wedges a session shut on a parse hiccup.

    With ``--explain`` / ``--explain-file`` it is the manikin instead: it prints
    how the gate WOULD judge that command and exits 0, having run nothing. Never
    probe the gate by executing the dangerous string — see :func:`explain`.
    """
    from ..cli import current_role

    command, error = _explain_source(args)
    if error:
        print(error, file=sys.stderr)
        return 1
    if command is not None:
        print(explain(command))
        return ALLOW_EXIT

    payload = _read_payload(sys.stdin)
    tool_name = payload.get("tool_name", "") if payload else ""
    tool_input = payload.get("tool_input", {}) if payload else {}
    if not isinstance(tool_input, dict):
        tool_input = {}
    if not isinstance(tool_name, str):
        tool_name = ""

    # Claude Code sends ``agent_id`` ONLY for tool calls made inside a subagent;
    # in the main session the key is absent. That makes it the one discriminator
    # available here — TIDE_ROLE cannot tell head from worker, since the worker
    # inherits it.
    agent_id = payload.get("agent_id", "") if payload else ""
    is_subagent = bool(isinstance(agent_id, str) and agent_id.strip())

    role = current_role()
    allow, reason = decide(tool_name, tool_input, role, is_subagent=is_subagent)

    if not allow:
        print(reason, file=sys.stderr)
        return BLOCK_EXIT
    return ALLOW_EXIT
