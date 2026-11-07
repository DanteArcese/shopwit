from normalize.text import fold


def test_fold_collapses_whitespace_and_nbsp():
    assert fold("  Toilet \u00a0 Paper\t\n ") == "toilet paper"


def test_fold_curly_quotes():
    assert fold("Member\u2019s \u201cMark\u201d") == "member's \"mark\""


def test_fold_casefold_and_nfkc():
    assert fold("STRASSE \uff11") == "strasse 1"
    assert fold("") == "" and fold("   ") == ""
