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


def test_empty_and_pure_markdown_return_empty():
    assert sanitize_summary("") == ""
    assert sanitize_summary("   \n\t  ") == ""
    assert sanitize_summary("```\n\n```") == ""
    assert sanitize_summary("*** ___ ###") == ""
