"""SOC-286 эксп. 1: сиды несут ГОТОВЫЕ команды старт-гейта.

Baseline приёма (6 сессий): каждая новая сессия искала синтаксис ``tide offload`` /
``tide arc set-goal`` через ``--help``, а первый offload в 4 из 5 передач проваливал
гейт пересказа. Сид передачи и сид запуска нити дают обе команды с подставленными ref
нити и сессии и всеми метками :data:`tide.handoff_queue.RETELL_ITEMS`.
"""

from __future__ import annotations

from tide import fields, handoff_contract as contract, handoff_queue
from tide.arc import stream
from tide.launcher import handoff, seed


def _labels():
    return [label for _name, label, _rx in handoff_queue.RETELL_ITEMS]


def _contract_distil() -> str:
    out = ["# автохендофф — 03-main-3 · нить 01-@main", ""]
    for b in contract.BLOCKS:
        out += ["## " + b.name, "", contract.empty_line(b), ""]
    return "\n".join(out)


def _offload_cmd(text: str, ref: str) -> str:
    start = text.index("tide offload {0} ".format(ref))
    return text[start:text.index("`", start)]


def test_template_carries_every_retell_label_and_passes_the_gate():
    tpl = handoff_queue.retell_template()
    for label in _labels():
        assert "{0}: …".format(label) in tpl
    # шаблон, заполненный как есть, гейт пересказа проходит — метки и гейт не разошлись
    assert handoff_queue.retell_gaps(tpl) == []


def test_launch_seed_has_ready_commands_with_refs():
    out = seed.build_seed(project_name="demo", canon_text="c", arc_ref="pickup",
                          arc_text="goal: x", thread_name="main",
                          gate_thread="04-@main", gate_session="03-pickup")
    assert 'tide arc set-goal 03-pickup -p 04-@main "' in out
    cmd = _offload_cmd(out, "04-@main/03-pickup")
    for label in _labels():
        assert label + ":" in cmd
    assert "--cursor" in cmd and "--next" in cmd
    assert "<сессия>" not in out and "<нить>/<сессия>" not in out


def _resolves(root, out, sess):
    """Обе команды из сида резолвятся в arc.md именно этой сессии (без записи)."""
    import re
    from tide import resolve
    from tide.arc import stream as st
    m = re.search(r"tide arc set-goal (\S+) -p (\S+) ", out)
    assert st._resolve(st._open_goal_substream(root, m.group(2)), m.group(1),
                       closed=False) == sess
    m = re.search(r"tide offload (\S+) ", out)
    assert resolve.find_session(root, m.group(1)) == sess


def test_seed_for_project_uses_nn_dir_names_that_resolve(tmp_project):
    stream.new_thread(tmp_project, "main", goal="главная нить")
    sess = stream.new_session(tmp_project, "main", "main-3", goal="шаг")
    out = seed.seed_for_project(tmp_project, arc_ref="main-3", thread_name="main",
                                arc_text="goal: шаг")
    thread_dir = sess.parent.parent.name
    assert thread_dir[:2].isdigit() and sess.name[:2].isdigit()  # NN-префиксы
    assert "tide offload {0}/{1} ".format(thread_dir, sess.name) in out
    assert "tide arc set-goal {0} -p {1} ".format(sess.name, thread_dir) in out
    _resolves(tmp_project, out, sess)


def test_launch_seed_plan_line_depends_on_role():
    orch = seed.build_seed(project_name="demo", canon_text="c", arc_ref="s",
                           arc_text="goal: x", thread_name="t")
    assert "попроси помощника (Agent) вписать в `t/plan.md`" in orch
    worker = seed.build_seed(project_name="demo", canon_text="c", arc_ref="s",
                             arc_text="goal: x", thread_name="t", role=seed.ROLE_WORKER)
    assert "впиши в `t/plan.md`" in worker and "попроси помощника" not in worker


def test_handoff_seed_has_ready_commands_with_refs(tmp_project, monkeypatch):
    from tide import whois
    monkeypatch.setattr(whois, "manager_address", lambda default="": "management-14")
    monkeypatch.setenv("TIDE_DECISIONS_FILE", str(tmp_project / "no.md"))
    thread = stream.new_thread(tmp_project, "main", goal="главная нить")
    stream.new_session(tmp_project, "main", "main-3", goal="шаг")
    new = stream.new_session(tmp_project, "main", "main-4", goal="шаг")
    fields.set_field(new / "arc.md", "from", "main-3")
    from tide.launcher import seed_budget as sb
    assert sb.is_contract_seed(_contract_distil())
    for distil in ("# сид\n\nсвободный текст\n", _contract_distil()):
        out = handoff._with_throughline(thread, new, distil)
        assert 'tide arc set-goal {0} -p {1} "'.format(new.name, thread.name) in out
        assert new.name[:2].isdigit() and thread.name[:2].isdigit()  # NN-префиксы
        cmd = _offload_cmd(out, "{0}/{1}".format(thread.name, new.name))
        for label in _labels():
            assert label + ":" in cmd
        _resolves(tmp_project, out, new)
