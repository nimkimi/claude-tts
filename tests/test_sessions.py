from sonari.sessions import SessionManager, Identity


def test_set_and_get_identity():
    sm = SessionManager()
    sm.set_identity("s1", Identity(term_program="Apple_Terminal", tty="/dev/ttys005"))
    ident = sm.identity("s1")
    assert ident is not None
    assert ident.term_program == "Apple_Terminal"
    assert ident.tty == "/dev/ttys005"
    assert ident.iterm_session_id == ""


def test_identity_absent_is_none():
    assert SessionManager().identity("nope") is None


def test_set_identity_empty_does_not_clobber_populated():
    # FIX 2: a re-fired SESSION_START (resume/clear/compact) can carry an empty,
    # best-effort identity. Overwriting unconditionally would wipe a good identity
    # and silently kill focus-follow for the session. An all-empty incoming
    # identity must leave the existing one untouched.
    sm = SessionManager()
    sm.set_identity("s1", Identity(term_program="Apple_Terminal", tty="/dev/ttys5"))
    sm.set_identity("s1", Identity())          # all-empty re-fire
    ident = sm.identity("s1")
    assert ident.term_program == "Apple_Terminal"
    assert ident.tty == "/dev/ttys5"


def test_set_identity_merges_per_field_keeping_empties_out():
    # Per-field merge: an EMPTY incoming field keeps the existing value; a NON-empty
    # incoming field updates it. A legit terminal switch (all fields non-empty) still
    # fully updates; only empties are ignored.
    sm = SessionManager()
    sm.set_identity("s1", Identity(term_program="Apple_Terminal",
                                   tty="/dev/ttys5", iterm_session_id=""))
    sm.set_identity("s1", Identity(term_program="Apple_Terminal",
                                   tty="", iterm_session_id="w0:ID"))
    ident = sm.identity("s1")
    assert ident.tty == "/dev/ttys5"           # kept (incoming tty was empty)
    assert ident.iterm_session_id == "w0:ID"   # updated (incoming was non-empty)
    assert ident.term_program == "Apple_Terminal"


def test_unregister_clears_identity():
    sm = SessionManager()
    sm.register("s1")
    sm.set_identity("s1", Identity(term_program="iTerm.app", iterm_session_id="X"))
    sm.unregister("s1")
    assert sm.identity("s1") is None


def test_foreground_starts_none():
    sm = SessionManager()
    assert sm.foreground() is None


def test_set_and_get_foreground():
    sm = SessionManager()
    sm.set_foreground("s1")
    assert sm.foreground() == "s1"
    assert sm.is_foreground("s1") is True
    assert sm.is_foreground("s2") is False


def test_set_foreground_replaces_previous():
    sm = SessionManager()
    sm.set_foreground("s1")
    sm.set_foreground("s2")
    assert sm.foreground() == "s2"
    assert sm.is_foreground("s1") is False
    assert sm.is_foreground("s2") is True


def test_should_speak_true_only_for_foreground():
    sm = SessionManager()
    sm.register("s1")
    sm.register("s2")
    sm.set_foreground("s1")
    assert sm.should_speak("s1") is True
    assert sm.should_speak("s2") is False


def test_should_speak_false_when_no_foreground():
    sm = SessionManager()
    sm.register("s1")
    assert sm.should_speak("s1") is False


def test_register_and_unregister():
    sm = SessionManager()
    sm.register("s1")
    sm.set_foreground("s1")
    assert sm.is_foreground("s1") is True
    sm.unregister("s1")
    # unregistering the foreground session clears foreground
    assert sm.foreground() is None
    assert sm.should_speak("s1") is False


def test_unregister_non_foreground_keeps_foreground():
    sm = SessionManager()
    sm.register("s1")
    sm.register("s2")
    sm.set_foreground("s1")
    sm.unregister("s2")
    assert sm.foreground() == "s1"


def test_unregister_unknown_session_is_noop():
    sm = SessionManager()
    sm.set_foreground("s1")
    sm.unregister("ghost")
    assert sm.foreground() == "s1"


# --- cwd capture + folder name -------------------------------------------

def test_register_records_cwd_basename_posix():
    sm = SessionManager()
    sm.register("s1", cwd="/home/me/myapp")
    assert sm.folder("s1") == "myapp"


def test_set_foreground_records_cwd_basename_windows_path():
    sm = SessionManager()
    sm.set_foreground("s1", cwd="C:\\Users\\me\\proj")
    assert sm.folder("s1") == "proj"      # portable: handles backslashes on any host


def test_folder_unknown_session_is_none():
    sm = SessionManager()
    assert sm.folder("nope") is None


def test_empty_cwd_does_not_clobber_known_folder():
    sm = SessionManager()
    sm.register("s1", cwd="/x/myapp")
    sm.set_foreground("s1", cwd="")       # later message with no cwd
    assert sm.folder("s1") == "myapp"     # keep the good name


# --- pin toggle ----------------------------------------------------------

def test_pin_toggle_no_foreground_returns_none():
    sm = SessionManager()
    assert sm.pin_toggle() == ("none", None)
    assert sm.pinned() is None


def test_pin_toggle_pins_current_foreground():
    sm = SessionManager()
    sm.set_foreground("s1", cwd="/x/myapp")
    assert sm.pin_toggle() == ("pinned", "myapp")
    assert sm.pinned() == "s1"


def test_pin_toggle_same_session_again_unpins():
    sm = SessionManager()
    sm.set_foreground("s1", cwd="/x/myapp")
    sm.pin_toggle()                       # pin
    assert sm.pin_toggle() == ("unpinned", "myapp")   # toggle off
    assert sm.pinned() is None


def test_pin_holds_foreground_when_another_session_submits():
    sm = SessionManager()
    sm.set_foreground("s1", cwd="/x/a")
    sm.pin_toggle()                       # pin s1
    sm.set_foreground("s2", cwd="/x/b")   # another session submits a prompt
    assert sm.foreground() == "s1"        # pin holds
    assert sm.is_foreground("s1") is True
    assert sm.is_foreground("s2") is False


def test_pin_moves_when_toggled_while_another_is_foreground():
    sm = SessionManager()
    sm.set_foreground("s1", cwd="/x/a")
    sm.pin_toggle()                       # pin s1
    sm.set_foreground("s2", cwd="/x/b")   # s2 now last-prompt (but s1 pinned)
    # NOTE: pin_toggle acts on the RAW last-prompt foreground (s2), so it moves the pin
    assert sm.pin_toggle() == ("pinned", "b")
    assert sm.pinned() == "s2"


def test_unregister_pinned_session_falls_back_to_auto():
    sm = SessionManager()
    sm.set_foreground("s1", cwd="/x/a")
    sm.pin_toggle()                       # pin s1
    sm.unregister("s1")
    assert sm.pinned() is None
    assert sm.foreground() is None


def test_focus_clears_pin_and_sets_foreground():
    sm = SessionManager()
    sm.set_foreground("a")
    sm.pin_toggle()                       # pin a
    assert sm.pinned() == "a"
    sm.focus("b")
    assert sm.pinned() is None            # explicit jump overrides the pin
    assert sm.foreground() == "b"


def test_focus_records_cwd_folder():
    sm = SessionManager()
    sm.focus("b", cwd="/work/backend")
    assert sm.folder("b") == "backend"


# --- OS focus tracking (focus-aware navigation) ---------------------------


def test_os_focus_starts_none():
    sm = SessionManager()
    assert sm.focused_session() is None


def test_os_focus_resolves_terminal_by_tty():
    sm = SessionManager()
    sm.register("a"); sm.set_identity("a", Identity(term_program="Apple_Terminal", tty="/dev/ttys001"))
    sm.register("b"); sm.set_identity("b", Identity(term_program="Apple_Terminal", tty="/dev/ttys002"))
    sm.set_os_focus(term_program="Apple_Terminal", tty="/dev/ttys002")
    assert sm.focused_session() == "b"


def test_os_focus_empty_tty_matches_nothing():
    sm = SessionManager()
    sm.register("a"); sm.set_identity("a", Identity(term_program="Apple_Terminal", tty=""))
    sm.set_os_focus(term_program="Apple_Terminal", tty="")
    assert sm.focused_session() is None


def test_os_focus_no_match_returns_none():
    sm = SessionManager()
    sm.register("a"); sm.set_identity("a", Identity(term_program="Apple_Terminal", tty="/dev/ttys001"))
    sm.set_os_focus(term_program="Apple_Terminal", tty="/dev/ttys999")
    assert sm.focused_session() is None


def test_os_focus_false_clears():
    sm = SessionManager()
    sm.register("a"); sm.set_identity("a", Identity(term_program="Apple_Terminal", tty="/dev/ttys001"))
    sm.set_os_focus(term_program="Apple_Terminal", tty="/dev/ttys001")
    assert sm.focused_session() == "a"
    sm.set_os_focus(focused=False)
    assert sm.focused_session() is None


def test_os_focus_resolves_iterm_by_bare_guid():
    sm = SessionManager()
    sm.register("a")
    sm.set_identity("a", Identity(term_program="iTerm.app", iterm_session_id="w0t1p2:ABCD-1234"))
    # watcher sends the BARE guid from `id of current session`
    sm.set_os_focus(term_program="iTerm.app", iterm_session_id="ABCD-1234")
    assert sm.focused_session() == "a"


def test_focused_session_none_after_unregister():
    sm = SessionManager()
    sm.register("a"); sm.set_identity("a", Identity(term_program="Apple_Terminal", tty="/dev/ttys001"))
    sm.set_os_focus(term_program="Apple_Terminal", tty="/dev/ttys001")
    assert sm.focused_session() == "a"
    sm.unregister("a")
    assert sm.focused_session() is None
