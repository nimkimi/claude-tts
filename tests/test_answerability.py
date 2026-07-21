"""D7b (§5): answerability is the ADMISSION CRITERION for the directive tier.
Only the blocking PERMISSION_REQUEST registers _pending_decisions and announces
answerable (serviceable by the keyboard); CHOICE/PLAN/legacy PERMISSION are
structurally unanswerable (upstream-blocked — see
docs/upstream/claude-code-feature-request-answer-hook.md) and every one of
their announces carries the advisory frame. Behavioral pins plus AST drift
guards in the test_cue_contract idiom, so the rule cannot drift."""
import ast
import pathlib

from sonari.protocol import PROTOCOL_VERSION, MsgType
from sonari.daemon.features.decisions import ADVISORY_SUFFIX
from tests.daemon_helpers import make_daemon

DECISIONS_SRC = (pathlib.Path(__file__).resolve().parents[1]
                 / "src" / "sonari" / "daemon" / "features" / "decisions.py")


def _msg(t, session, **kw):
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


def test_choice_plan_and_legacy_permission_carry_the_advisory_frame():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="fg")
    daemon.handle_message(_msg(MsgType.CHOICE, "fg", questions=[
        {"question": "Pick?", "options": [{"label": "A"}]}]))
    daemon.handle_message(_msg(MsgType.PLAN, "fg", text="Step one."))
    daemon.handle_message(_msg(MsgType.PERMISSION, "fg", action="run ls"))
    texts = [queue.pop_next().text for _ in range(3)]
    assert all(t.endswith(ADVISORY_SUFFIX) for t in texts), texts
    assert daemon._pending_decisions == {}          # none of them registers


def test_blocking_permission_is_answerable_and_unframed():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="fg")
    daemon.handle_message(_msg(MsgType.PERMISSION_REQUEST, "fg",
                               tool="Bash", summary="ls"))
    item = queue.pop_next()
    assert not item.text.endswith(ADVISORY_SUFFIX)  # unchanged directive signature
    assert "fg" in daemon._pending_decisions        # registered == answerable
    daemon.handle_message(_msg(MsgType.ANSWER_PERMISSION, "fg", behavior="allow"))
    assert daemon._pending_decisions["fg"]["behavior"] == "allow"   # keyboard-serviceable


def test_reread_carries_the_advisory_frame_too():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="fg")
    daemon.handle_message(_msg(MsgType.CHOICE, "fg", questions=[
        {"question": "Pick?", "options": [{"label": "A"}]}]))
    queue.pop_next()
    daemon.handle_message(_msg(MsgType.REREAD_OPTIONS, "fg"))
    assert queue.pop_next().text.endswith(ADVISORY_SUFFIX)   # options carries the frame


def _decision_functions():
    tree = ast.parse(DECISIONS_SRC.read_text(encoding="utf-8"))
    return {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}


def test_the_only_decision_enqueue_lives_in_the_announce_chokepoint():
    for name, fn in _decision_functions().items():
        for node in ast.walk(fn):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "_enqueue"
                    and len(node.args) >= 4
                    and isinstance(node.args[3], ast.Constant)
                    and node.args[3].value is True):
                assert name == "_announce_decision", \
                    "is_decision=True enqueue outside the chokepoint: {0}".format(name)


def test_answerable_true_is_passed_only_by_the_blocking_permission():
    passers = []
    for name, fn in _decision_functions().items():
        if name == "_announce_decision":
            continue
        for node in ast.walk(fn):
            if isinstance(node, ast.Call):
                for kw in node.keywords:
                    if kw.arg == "answerable" and isinstance(kw.value, ast.Constant):
                        passers.append((name, kw.value.value))
    assert ("on_permission_request", True) in passers
    assert all(v is False for n, v in passers
               if n != "on_permission_request"), passers
    # every producer passes it EXPLICITLY (no defaulting drift)
    assert {n for n, _ in passers} == {"on_choice", "on_plan", "on_permission",
                                      "on_permission_request"}


def test_pending_registration_is_owned_by_the_blocking_permission_alone():
    for name, fn in _decision_functions().items():
        for node in ast.walk(fn):
            if (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Subscript)
                    and isinstance(node.targets[0].value, ast.Attribute)
                    and node.targets[0].value.attr == "_pending_decisions"):
                assert name == "on_permission_request", name
