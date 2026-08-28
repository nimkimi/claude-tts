# tests/test_privacy_doc.py
"""PRIVACY.md teeth (T6).

D4 record: a reviewer inverted the single most safety-critical claim in
PRIVACY.md -- "defaults to keeping" became "defaults to deleting" -- and all
three original tests here still passed, because they only checked that a
word appeared somewhere in the file. That is not coverage.

Every test below binds a specific PRIVACY.md claim to the CODE fact that
makes it true:
  - the DOCUMENT side is a tight, targeted regex against the doc's actual
    wording (never "the word 'keep' is in the file somewhere") -- reword or
    invert the claim and the regex stops matching;
  - the CODE side either runs the real code path (uninstall(), the argparse
    parser, SessionHistory) or reads an actual runtime fact, never the
    source text of the implementation -- change the behaviour without
    touching the doc and the assertion diverges from what the doc claims.

FLAT collapses PRIVACY.md's whitespace before matching so a harmless
markdown rewrap (moving where a paragraph breaks a line) cannot fail these
tests for a reason that has nothing to do with the claim itself.
"""
import json
import pathlib
import re
from unittest import mock

from sonari import cli
from sonari.cli import install as install_cmd
from sonari.config import DEFAULTS
from sonari.history import SessionHistory

DOC = (pathlib.Path(__file__).resolve().parents[1] / "PRIVACY.md").read_text(
    encoding="utf-8")
FLAT = re.sub(r"\s+", " ", DOC)


def _state_with_transcripts(tmp_path, sessions=1, per=2):
    """state.json in the REAL shape the daemon writes it: the transcript pile
    lives under `history[session]["entries"]` (history.py:216-238, SessionHistory
    .to_state()); `sessions` is only the live roster and carries no text. A
    fixture that puts entries under `sessions` instead exercises a shape
    transcript_summary() does not read, and makes uninstall()'s ask-gate
    (`if sessions and purge is None`) look tested when it is not."""
    p = tmp_path / "state.json"
    p.write_text(json.dumps({
        "version": 1,
        "sessions": {},
        "history": {f"s{i}": {"entries": [{"text": "spoken text"}] * per}
                    for i in range(sessions)},
    }), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Claim 1 (brief item 1): the uninstall transcript prompt defaults to KEEP.
# ---------------------------------------------------------------------------

def test_uninstall_default_is_keep_in_doc_and_via_the_interactive_prompt(tmp_path):
    """The exact claim D4 found inverted, with the old tests still green.
    Asserts the document's wording AND runs the real ask-flow: an interactive
    answer that is anything but y/yes ("" here) must keep the file."""
    assert re.search(r"defaults to \*\*keeping\*\* that file", DOC), (
        "PRIVACY.md must say the uninstall default is to KEEP the file")
    state = _state_with_transcripts(tmp_path)
    with mock.patch("sonari.paths.STATE_PATH", state), \
         mock.patch("sys.stdout.isatty", return_value=True), \
         mock.patch("builtins.input", return_value=""), \
         mock.patch("sonari.cli.teardown.stop_daemon", return_value="stopped"), \
         mock.patch("sonari.cli._platform"), \
         mock.patch("sonari.cli.voiceout.speak"):
        install_cmd.uninstall()
    assert state.exists(), (
        "an interactive prompt answered with anything but y/yes must KEEP "
        "the transcripts -- the code half of the doc's default-keep claim")


def test_silent_uninstall_with_no_tty_also_keeps_per_the_doc(tmp_path):
    """The doc's other default-keep path: 'no terminal attached'. A script
    running uninstall unattended, with no --purge/--keep flag, must not lose
    transcript data nobody agreed to lose."""
    assert re.search(r"no terminal attached, or you decline", FLAT), (
        "PRIVACY.md must describe the no-terminal-attached case explicitly")
    state = _state_with_transcripts(tmp_path)
    with mock.patch("sonari.paths.STATE_PATH", state), \
         mock.patch("sys.stdout.isatty", return_value=False), \
         mock.patch("sonari.cli.teardown.stop_daemon", return_value="stopped"), \
         mock.patch("sonari.cli._platform"), \
         mock.patch("sonari.cli.voiceout.speak"):
        install_cmd.uninstall()
    assert state.exists()


# ---------------------------------------------------------------------------
# Claim 2 (brief item 2): the --purge-transcripts / --keep-transcripts route.
# ---------------------------------------------------------------------------

def test_purge_and_keep_flags_are_documented_and_wired_to_the_cli():
    """Doc claim: the two flags skip the prompt, one deletes immediately, one
    keeps. Code fact: argparse actually wires both flags to uninstall()'s
    `purge` argument with the documented polarity -- not just present as
    text somewhere in PRIVACY.md."""
    assert re.search(
        r"--purge-transcripts` deletes `state\.json` immediately", FLAT), (
        "PRIVACY.md must document --purge-transcripts as an immediate delete")
    assert re.search(r"--keep-transcripts` keeps it", FLAT), (
        "PRIVACY.md must document --keep-transcripts as keeping the file")

    parser = cli._build_parser()
    purge_ns = parser.parse_args(["uninstall", "--purge-transcripts"])
    keep_ns = parser.parse_args(["uninstall", "--keep-transcripts"])
    assert purge_ns.purge is True
    assert keep_ns.purge is False


def test_purge_transcripts_flag_actually_deletes_state_json(tmp_path):
    """The behavioural half of the claim above, run through the same
    `uninstall(purge=...)` entry point the CLI flag resolves to."""
    state = _state_with_transcripts(tmp_path)
    with mock.patch("sonari.paths.STATE_PATH", state), \
         mock.patch("sonari.cli.teardown.stop_daemon", return_value="stopped"), \
         mock.patch("sonari.cli._platform"), \
         mock.patch("sonari.cli.voiceout.speak"):
        install_cmd.uninstall(purge=True)
    assert not state.exists()


def test_keep_transcripts_flag_actually_preserves_state_json(tmp_path):
    state = _state_with_transcripts(tmp_path)
    with mock.patch("sonari.paths.STATE_PATH", state), \
         mock.patch("sonari.cli.teardown.stop_daemon", return_value="stopped"), \
         mock.patch("sonari.cli._platform"), \
         mock.patch("sonari.cli.voiceout.speak"):
        install_cmd.uninstall(purge=False)
    assert state.exists()


# ---------------------------------------------------------------------------
# Claim 3 (brief item 3): the kept-data inventory names state.json AND
# ~/.sonari/spearcons/.
# ---------------------------------------------------------------------------

def test_the_kept_data_inventory_names_state_json_and_spearcons():
    """PRIVACY.md's 'What Sonari stores on your machine' section must name
    both files that survive an ordinary `sonari uninstall`."""
    assert re.search(r"`state\.json` — \*\*session content\*\*", FLAT), (
        "PRIVACY.md's kept-data inventory must name state.json")
    assert re.search(
        r"`spearcons/` — a cache of short rendered audio clips", FLAT), (
        "PRIVACY.md's kept-data inventory must name the spearcons/ cache")


def test_uninstall_does_not_remove_the_spearcons_cache(tmp_path):
    """Code fact behind the doc's claim that `sonari uninstall` does **not**
    remove spearcons/: create real files under it and run the real
    uninstall() -- they must still be there afterward. If a future uninstall
    change starts rmtree-ing SONARI_DIR wholesale, or someone adds
    spearcons/ to the artifact-removal list, this fails without anyone
    having to notice PRIVACY.md went silently wrong."""
    assert re.search(r"does \*\*not\*\* remove this folder", FLAT), (
        "PRIVACY.md must state uninstall does not remove spearcons/")
    from sonari import paths
    spearcons_dir = paths.SONARI_DIR / "spearcons"
    spearcons_dir.mkdir(parents=True)
    clip = spearcons_dir / "abc123.aiff"
    clip.write_bytes(b"fake-audio")
    with mock.patch("sonari.cli.teardown.stop_daemon", return_value="stopped"), \
         mock.patch("sonari.cli._platform"), \
         mock.patch("sonari.cli.voiceout.speak"), \
         mock.patch("sys.stdout.isatty", return_value=False):
        install_cmd.uninstall()
    assert clip.exists(), "uninstall must not remove ~/.sonari/spearcons/"


# ---------------------------------------------------------------------------
# Claim 4 (brief item 4, "made real"): state.json genuinely holds verbatim
# session content -- replaces the old test that only checked a banned phrase
# was absent, which cannot catch a claim that goes false in a NEW way.
# ---------------------------------------------------------------------------

def test_the_session_content_claim_matches_what_the_code_actually_persists():
    """D4: PRIVACY.md once claimed Sonari was 'not designed to record
    session content' while state.json recorded it verbatim -- a direct
    contradiction of the code, found twice. This binds the doc's current
    POSITIVE claim (it keeps verbatim text, capped at history_cap) to the
    code facts that make it true: SessionHistory.to_state() actually
    persists each entry's verbatim text, and the default cap it names (200)
    matches config.py. The banned phrase is kept as a cheap regression
    guard, not the whole test."""
    assert re.search(
        r"\*\*session content\*\*: the verbatim text of what Sonari has\s+"
        r"spoken or has yet to speak", FLAT), (
        "PRIVACY.md must claim state.json holds verbatim spoken/unspoken text")
    assert re.search(r"`history_cap` \(200 by default\)", FLAT), (
        "PRIVACY.md's stated default history_cap must match config.py")
    assert DEFAULTS["history_cap"] == 200, (
        "config.py's history_cap default no longer matches PRIVACY.md's "
        "stated '200 by default'")
    assert "not designed to record session content" not in DOC

    hist = SessionHistory()
    hist.record("s1", "prose", "the exact words spoken")
    state = hist.to_state()
    assert state["s1"]["entries"][0]["text"] == "the exact words spoken", (
        "SessionHistory must actually persist verbatim text -- the code "
        "fact PRIVACY.md's 'session content' claim depends on")
