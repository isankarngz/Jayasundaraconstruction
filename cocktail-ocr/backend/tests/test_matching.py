import pytest

from app.matching import find_hits

MATCH, NONE = "match", "none"


def kinds(text, fuzzy=True):
    return [(h.kind, h.target) for h in find_hits(text, fuzzy)]


def is_match(text):
    return any(h.is_match for h in find_hits(text))


@pytest.mark.parametrize("text,expected", [
    ("Cocktail Menu", True), ("COCKTAILS", True), ("Signature Cocktails", True), ("cocktail specials", True),
    ("Cocktail(s)", True), ("Mocktail", False), ("Cocktailling", False), ("Non-alcoholic cocktails", True),
    ("cOcKtAiL", True), ("CoCkTaIlS", True),
    ('"Cocktails!"', True), ("(cocktail)", True), ("cocktails.", True), ("cocktail,", True), ("cocktail's", True),
    ("cocktail-hour", True), ("¡Cocktails!", True), ("Cocktails…", True), ("«cocktail»", True),
    ("Mocktails", False), ("cocktailbar", False), ("happy hour", False), ("", False), ("cocktai", False),
    ("C O C K T A I L S", True), ("Cock tail lounge", True), ("cocktail s", True),
    ("ｃｏｃｋｔａｉｌ", True),  # full-width characters (NFKC)
])
def test_exact_rules(text, expected):
    assert is_match(text) is expected


def test_target_word_identified():
    assert kinds("Signature Cocktails") == [("exact", "cocktails")]
    assert kinds("Cocktail Menu") == [("exact", "cocktail")]
    assert kinds("C O C K T A I L S") == [("spaced", "cocktails")]


def test_multiple_occurrences_in_one_line():
    hs = [h for h in find_hits("Cocktails, cocktail and more COCKTAILS") if h.is_match]
    assert [h.target for h in hs] == ["cocktails", "cocktail", "cocktails"]
    assert [h.raw for h in hs] == ["Cocktails", "cocktail", "COCKTAILS"]


def test_offsets_point_at_the_word():
    line = 'Our "Cocktails!" list'
    h = find_hits(line)[0]
    assert line[h.start:h.end] == "Cocktails"


@pytest.mark.parametrize("text,kind", [
    ("cocktaiI", "confusion"), ("c0cktail", "confusion"), ("COCKTA1L", "confusion"), ("cockta|ls", "confusion"),
    ("cocktai", "near"), ("coktails", "near"), ("cocktals", "near"), ("cocktaills", "compound"),
    ("CocktailMenu", "compound"), ("Cocktailling", "compound"), ("cock-tail", "compound"),
])
def test_candidates_are_flagged_not_matched(text, kind):
    hs = find_hits(text)
    assert not any(h.is_match for h in hs)
    assert any(h.kind == kind for h in hs), hs


def test_candidates_disabled():
    assert find_hits("cocktaiI", fuzzy=False) == []


def test_mocktail_is_recorded_as_excluded_lookalike_only():
    hs = find_hits("Mocktail menu")
    assert [h.kind for h in hs] == ["excluded"]
    assert not any(h.is_candidate for h in hs)


def test_mixed_line_keeps_both():
    assert kinds("Mocktails & Cocktails") == [("excluded", "cocktails"), ("exact", "cocktails")]


def test_unrelated_words_and_translations_do_not_match():
    for t in ["Cócteles", "カクテル", "drinks", "cock", "tail", "rooster tail", "Cocktease"]:
        assert not is_match(t), t
