from sonari.cli.verdict import verdict


def test_all_green_reports_healthy_and_the_count():
    rows = [("python3", True, "ok"), ("keymap resolves", True, "ok")]
    assert verdict(rows) == "Sonari is healthy. 2 checks passed."


def test_failures_are_named_with_their_spoken_names():
    rows = [("python3", True, "ok"),
            ("daemon socket", False, "not reachable"),
            ("SONARI_DIR writable", False, "not writable")]
    out = verdict(rows)
    assert out.startswith("Sonari is unhealthy. 2 checks failed:")
    assert "daemon socket" in out
    assert "storage" in out          # spoken name, not the printed one


def test_a_warn_row_neither_fails_the_verdict_nor_is_spoken():
    rows = [("python3", True, "ok"), ("neural voices", False, "venv broken")]
    out = verdict(rows)
    assert out.startswith("Sonari is healthy.")
    assert "neural" not in out


def test_empty_rows_still_produce_a_sentence():
    assert verdict([]) == "Sonari ran no checks."


def test_singular_wording_for_one_failure():
    rows = [("daemon socket", False, "down")]
    assert verdict(rows) == "Sonari is unhealthy. 1 check failed: daemon socket."


def test_the_healthy_count_states_only_the_checks_that_passed():
    """"N checks passed" must be true. It counted len(rows), so a warn-class
    failure — excluded from the spoken failure list BY DESIGN — was still
    counted as a pass: 21 rows with one red fault log said "21 checks passed"
    when 20 did. A spoken statement of fact, false, in a product whose promise
    is that the spoken sentence can be trusted."""
    rows = [("python3", True, "ok"), ("keymap resolves", True, "ok"),
            ("fault log", False, "a native crash was recorded")]
    said = verdict(rows)
    assert said == "Sonari is healthy. 2 checks passed.", said
