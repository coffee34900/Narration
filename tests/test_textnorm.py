import pytest

from narravid.textnorm import canon_text, identifiers, number_facts, proper_nouns


@pytest.mark.parametrize("a,b", [
    ("seventy-five percent", "75%"),
    ("seventy five per cent", "75 percent"),
    ("one thousand nine hundred and six", "1,906"),
    ("1906", "1,906"),
    ("two root two", "2√2"),
    ("two times the square root of two", "2 times root 2"),
    ("zero point zero eight", "0.08"),
    ("point zero eight", ".08"),
    ("nineteen ninety-eight", "1998"),
    ("twenty twenty-three", "2023"),
    ("four hundred and fifty thousand", "450k"),
    ("four hundred fifty thousand", "450,000"),
    ("two million", "2 million"),
    ("a hundred and five", "105"),
    ("nineteen oh five", "1905"),
    ("the twenty-first century", "the 21st century"),
])
def test_spoken_and_written_forms_match(a, b):
    assert canon_text(a) == canon_text(b)


def test_number_facts_compounds():
    vals = {f["compound"] or f["value"] for f in number_facts("75% of 1,906 patients; bound 2√2 and √2; p = 0.08")}
    assert vals == {"75%", "1906", "2√2", "√2", "0.08"}


def test_identifiers():
    ids = identifiers("TP53 and rs12345 with ILMN_1651199 in a GWAS of mRNA and eQTL data")
    assert {"TP53", "rs12345", "ILMN_1651199", "GWAS", "mRNA", "eQTL"} <= set(ids)


def test_proper_nouns_skip_first_word_and_initials():
    assert proper_nouns("Classical limit in the Bell test by F. M. Scherer") == ["Bell", "Scherer"]
