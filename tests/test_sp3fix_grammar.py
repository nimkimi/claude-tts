from sonari.protocol import MsgType
from tests.daemon_helpers import make_daemon


def _msg(t, session, **kw):
    from sonari.protocol import PROTOCOL_VERSION
    return {"v": PROTOCOL_VERSION, "type": t, "session": session, **kw}


# --- divergence: BOTH folders named + both counts, in one composed sentence ---
def test_where_am_i_names_both_folders_and_counts_under_divergence():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="A")
    sessions.set_foreground("A", cwd="/x/web")          # keyboard/workspace folder = web
    sessions.register("B", cwd="/x/api")
    sessions.set_speaker("B")                            # voice=B (api); keyboard=A (web) -> diverged
    daemon._enqueue("C", "prose", "c backlog", False)   # a waiting background
    daemon._stream("D").stopped = True                  # a muted background
    daemon.handle_message(_msg(MsgType.WHERE_AM_I, ""))
    daemon._speak_loop_once()
    assert speaker.spoken == ["Voice: api, Playing. Keyboard: web. 1 waiting, 1 muted."]


# --- no divergence -> NO Keyboard clause ---
def test_where_am_i_omits_keyboard_clause_when_not_diverged():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="fg")
    sessions.set_foreground("fg", cwd="/x/work")        # voice == keyboard == fg
    daemon.handle_message(_msg(MsgType.WHERE_AM_I, "fg"))
    daemon._speak_loop_once()
    assert speaker.spoken == ["Voice: work, Playing. 0 waiting, 0 muted."]
    assert not any(s and "Keyboard:" in s for s in speaker.spoken)


# --- muted counts BACKGROUND stopped streams (fg excluded, F3); independent of voice_state ---
def test_where_am_i_muted_count_is_background_stopped_streams():
    daemon, queue, speaker, sessions, config = make_daemon(foreground="fg")
    sessions.set_foreground("fg", cwd="/x/work")
    daemon._stream("b1").stopped = True
    daemon._stream("b2").stopped = True                 # two individually-muted backgrounds
    daemon.handle_message(_msg(MsgType.WHERE_AM_I, "fg"))
    daemon._speak_loop_once()
    assert speaker.spoken == ["Voice: work, Playing. 0 waiting, 2 muted."]
