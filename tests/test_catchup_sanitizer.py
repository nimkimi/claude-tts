from sonari.catchup import sanitize_summary


def test_clean_prose_passes_through():
    text = "Tests passed. The build is green. It asked to deploy."
    assert sanitize_summary(text) == text


def test_strips_markdown_fences_backticks_emphasis_headings():
    raw = "# Result\nRan `pytest`. **All** green.\n```\ncode\n```"
    out = sanitize_summary(raw)
    assert "`" not in out and "*" not in out and "#" not in out
    assert "```" not in out
    assert "Ran pytest." in out and "All green." in out


def test_strips_leading_list_markers_and_collapses_newlines():
    raw = "- first thing.\n- second thing.\n\n1. third thing."
    out = sanitize_summary(raw)
    assert out == "first thing. second thing. third thing."


def test_clamps_to_ceiling_sentences():
    raw = " ".join("Sentence {0}.".format(i) for i in range(1, 13))
    out = sanitize_summary(raw, ceiling=8)
    assert out == " ".join("Sentence {0}.".format(i) for i in range(1, 9))
    assert "Sentence 9." not in out


def test_preserves_marks_inside_identifiers_and_references():
    assert (sanitize_summary("The file my_file_name.py was edited.")
            == "The file my_file_name.py was edited.")
    assert (sanitize_summary("Fixed issue #123 in the tracker.")
            == "Fixed issue #123 in the tracker.")


def test_preserves_dunder_and_sunder_identifiers():
    # Whitespace-delimited __dunder__ / _sunder_ names are syntactically
    # identical to underscore-emphasis markdown — the sanitizer must never
    # rewrite them (underscore is not an emphasis delimiter here).
    assert (sanitize_summary("Called __init__ during setup.")
            == "Called __init__ during setup.")
    assert (sanitize_summary("Renamed __init__ to __new__ in the base class.")
            == "Renamed __init__ to __new__ in the base class.")
    assert (sanitize_summary("The _internal_ variable holds state.")
            == "The _internal_ variable holds state.")
    assert (sanitize_summary("Kept with__no__spaces_around intact.")
            == "Kept with__no__spaces_around intact.")


def test_still_strips_paired_and_nested_emphasis():
    assert sanitize_summary("**bold *nested* bold** works.") == "bold nested bold works."


def test_empty_and_pure_markdown_return_empty():
    assert sanitize_summary("") == ""
    assert sanitize_summary("   \n\t  ") == ""
    assert sanitize_summary("```\n\n```") == ""
    assert sanitize_summary("*** ___ ###") == ""
