"""Token-aware matching for the target words "cocktail" and "cocktails".

The rules are deliberately narrow and explicit:

* EXACT   - a standalone word (letters only, delimited by anything that is
            not a letter) equal to "cocktail" or "cocktails", ignoring case.
            "Cocktail(s)", "COCKTAILS!", "non-alcoholic cocktails",
            "cocktail-hour" all contain an EXACT hit.  "Mocktail" and
            "Cocktailling" do not.
* SPACED  - the same word split only by whitespace, as OCR often produces for
            letter-spaced headings ("C O C K T A I L S") or a stray gap
            ("Cock tail").  This is the "harmless OCR whitespace variation"
            normalisation and counts as a match.

Everything below is *never* a match on its own.  When fuzzy candidate
flagging is enabled these are reported so the image is escalated to human
review instead of being silently counted as negative:

* CONFUSION - same length as a target, every character either equal or a
              known OCR confusion ("cocktaiI", "c0cktail", "COCKTA1L").
* NEAR      - edit distance <= 2 from a target ("cocktai", "cocktall",
              "coktails").  Truncated or partially read words land here.
* COMPOUND  - a longer token that contains the target ("CocktailMenu",
              "cocktailling", "cock-tail").  OCR frequently drops the space
              between words, so these need a human decision.

"mocktail"/"mocktails" are recorded as EXCLUDED look-alikes: they never
match and do not on their own trigger review, but they are kept so a
cross-engine disagreement (one engine reads "Cocktail", another "Mocktail"
at the same place) can be detected.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

TARGETS = ("cocktail", "cocktails")
EXCLUDED_LOOKALIKES = ("mocktail", "mocktails")

EXACT = "exact"
SPACED = "spaced"
CONFUSION = "confusion"
NEAR = "near"
COMPOUND = "compound"
EXCLUDED = "excluded"

MATCH_KINDS = (EXACT, SPACED)
CANDIDATE_KINDS = (CONFUSION, NEAR, COMPOUND)

# Characters an OCR engine commonly substitutes for each target letter.
# Comparison is case-insensitive in addition to these.
_CONFUSABLE = {
    "c": set("(<¢"),
    "o": set("0°ºQ"),
    "k": set(),
    "t": set("+†"),
    "a": set("4@"),
    "i": set("1l|!¡íìï"),
    "l": set("1Ii|!¡"),
    "s": set("5$§"),
}

_LETTER_RUN = re.compile(r"[^\W\d_]+", re.UNICODE)
_LOOSE_TOKEN = re.compile(r"\S+")
_EDGE_STRIP = "\"'`‘’“”«».,;:?*#%&=~^_[]{}<>/\\-–—"
_INNER_JOINERS = re.compile(r"[-–—'’.·•_]")


@dataclass(frozen=True)
class Hit:
    kind: str          # one of the *_KIND constants above
    target: str        # "cocktail" or "cocktails" (best matching target)
    raw: str           # text exactly as OCR produced it
    normalized: str    # lower-cased / whitespace-normalised form
    start: int         # char offset in the line text (inclusive)
    end: int           # char offset in the line text (exclusive)

    @property
    def is_match(self) -> bool:
        return self.kind in MATCH_KINDS

    @property
    def is_candidate(self) -> bool:
        return self.kind in CANDIDATE_KINDS


def normalize_text(text: str) -> str:
    """Unicode-normalise (NFKC folds ligatures/full-width forms) and lowercase."""
    return unicodedata.normalize("NFKC", text).casefold()


def _levenshtein(a: str, b: str, cap: int = 3) -> int:
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
        prev = cur
    return prev[-1]


def _confusion_equal(raw: str, target: str) -> bool:
    if len(raw) != len(target):
        return False
    for rc, tc in zip(raw, target):
        if rc.casefold() == tc:
            continue
        if rc in _CONFUSABLE.get(tc, ()):
            continue
        return False
    return True


def _target_for(word: str) -> str | None:
    w = normalize_text(word)
    return w if w in TARGETS else None


def find_hits(line: str, fuzzy: bool = True) -> list[Hit]:
    """Return every match / candidate / excluded look-alike in one OCR line."""
    hits: list[Hit] = []
    covered: list[tuple[int, int]] = []

    def overlaps(s: int, e: int) -> bool:
        return any(s < ce and cs < e for cs, ce in covered)

    # 1. EXACT: standalone letter runs.
    letter_tokens = [(m.group(), m.start(), m.end()) for m in _LETTER_RUN.finditer(line)]
    for tok, s, e in letter_tokens:
        t = _target_for(tok)
        if t:
            hits.append(Hit(EXACT, t, tok, t, s, e))
            covered.append((s, e))
        elif normalize_text(tok) in EXCLUDED_LOOKALIKES:
            hits.append(Hit(EXCLUDED, "cocktails" if normalize_text(tok).endswith("s") else "cocktail",
                            tok, normalize_text(tok), s, e))
            covered.append((s, e))

    # 2. SPACED: consecutive letter runs separated only by whitespace.
    n = len(letter_tokens)
    for i in range(n):
        joined = ""
        for j in range(i, min(n, i + 10)):
            tok, s, e = letter_tokens[j]
            if j > i:
                gap = line[letter_tokens[j - 1][2]:s]
                if not gap or not gap.isspace():
                    break
            joined += normalize_text(tok)
            if len(joined) > len("cocktails"):
                break
            if j > i and joined in TARGETS:
                start, end = letter_tokens[i][1], e
                # Prefer the longest ("cocktails") reading starting here.
                if not overlaps(start, end):
                    nxt = j + 1
                    if (joined == "cocktail" and nxt < n
                            and normalize_text(letter_tokens[nxt][0]) == "s"
                            and line[e:letter_tokens[nxt][1]].isspace()):
                        end = letter_tokens[nxt][2]
                        joined = "cocktails"
                    hits.append(Hit(SPACED, joined, line[start:end], joined, start, end))
                    covered.append((start, end))
                break

    if not fuzzy:
        return sorted(hits, key=lambda h: h.start)

    # 3. Fuzzy candidates on whitespace-delimited tokens.
    for m in _LOOSE_TOKEN.finditer(line):
        raw = m.group()
        s, e = m.start(), m.end()
        # Strip surrounding punctuation but keep confusable characters.
        ls = 0
        while ls < len(raw) and raw[ls] in _EDGE_STRIP:
            ls += 1
        rs = len(raw)
        while rs > ls and raw[rs - 1] in _EDGE_STRIP:
            rs -= 1
        core = raw[ls:rs]
        cs, ce = s + ls, s + rs
        if len(core) < 4:
            continue
        # Skip tokens that are exactly an EXACT/EXCLUDED word (with brackets etc).
        letters_only = "".join(_LETTER_RUN.findall(core))
        if overlaps(cs, ce) and normalize_text(letters_only) in TARGETS + EXCLUDED_LOOKALIKES:
            continue
        low = normalize_text(core)
        if low in TARGETS or low in EXCLUDED_LOOKALIKES:
            continue
        if overlaps(cs, ce) and any(t in low for t in TARGETS) and _LETTER_RUN.fullmatch(core) is None:
            # e.g. "Cocktail(s)" - already an EXACT hit for its letter run.
            continue

        kind = None
        target = None
        for t in TARGETS:
            if _confusion_equal(core, t):
                kind, target = CONFUSION, t
                break
        if kind is None:
            joined = _INNER_JOINERS.sub("", low)
            if joined in TARGETS and joined != low:
                kind, target = COMPOUND, joined
            elif "cocktail" in low and not any(x in low for x in EXCLUDED_LOOKALIKES):
                kind, target = COMPOUND, "cocktails" if "cocktails" in low else "cocktail"
        if kind is None and 6 <= len(low) <= 12:
            best = min(TARGETS, key=lambda t: _levenshtein(low, t))
            d = _levenshtein(low, best)
            if d <= 2 and low not in EXCLUDED_LOOKALIKES and not any(
                    _levenshtein(low, x) < d for x in EXCLUDED_LOOKALIKES):
                kind, target = NEAR, best
        if kind:
            hits.append(Hit(kind, target, core, low, cs, ce))
            covered.append((cs, ce))

    return sorted(hits, key=lambda h: h.start)


def contains_target(line: str) -> bool:
    """Convenience: True when the line contains an EXACT or SPACED match."""
    return any(h.is_match for h in find_hits(line, fuzzy=False))
