from normalize.text import fold


def test_fold_collapses_whitespace_and_nbsp():
    assert fold("  Toilet \u00a0 Paper\t\n ") == "toilet paper"


def test_fold_curly_quotes():
    assert fold("Member\u2019s \u201cMark\u201d") == 'member\'s "mark"'


def test_fold_casefold_and_nfkc():
    assert fold("STRASSE \uff11") == "strasse 1"
    assert fold("") == "" and fold("   ") == ""


def test_fold_vulgar_fraction_uses_ascii_slash():
    assert fold("½ gal") == "1/2 gal"
    assert fold("1½ gal") == "1 1/2 gal"


def test_fold_trademark_symbols_are_spaced_out():
    assert fold("Milk™ 1 gal") == "milk 1 gal"
    assert fold("Milk℠ 1 gal") == "milk 1 gal"


def test_fold_double_prime_becomes_inch_quote():
    assert fold("10.4\u2033") == '10.4"'
    assert fold("5\u2032") == "5'"
