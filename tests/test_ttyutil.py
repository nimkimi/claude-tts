from sonari import ttyutil


def _fake_ps(table):
    # table: {pid: (ppid, tty_raw)}
    def runner(pid):
        return table.get(pid, (0, "??"))  # default: pid absent from table (test convenience, not emulated OS)
    return runner


def test_returns_first_ancestor_with_real_tty_normalized():
    # self(100,??) -> parent(200,??) -> claude(300, ttys005)
    table = {100: (200, "??"), 200: (300, "??"), 300: (1, "ttys005")}
    assert ttyutil.controlling_tty(pid=100, ps_runner=_fake_ps(table)) == "/dev/ttys005"


def test_already_prefixed_tty_not_double_prefixed():
    table = {100: (1, "/dev/ttys007")}
    assert ttyutil.controlling_tty(pid=100, ps_runner=_fake_ps(table)) == "/dev/ttys007"


def test_no_tty_anywhere_returns_empty():
    table = {100: (200, "??"), 200: (1, "??")}
    assert ttyutil.controlling_tty(pid=100, ps_runner=_fake_ps(table)) == ""


def test_walk_stops_at_pid_1_or_0_without_looping():
    table = {100: (1, "??"), 1: (0, "??")}
    assert ttyutil.controlling_tty(pid=100, ps_runner=_fake_ps(table)) == ""


def test_runner_exception_degrades_to_empty():
    def boom(_pid):
        raise OSError("ps failed")
    assert ttyutil.controlling_tty(pid=100, ps_runner=boom) == ""


def test_no_args_uses_default_pid_and_ps():
    # Exercises production path: os.getpid() + _default_ps (real subprocess).
    # Result is "" or starts with "/dev/"; call never raises.
    result = ttyutil.controlling_tty()
    assert isinstance(result, str)
    assert result == "" or result.startswith("/dev/"), f"unexpected tty: {result}"
