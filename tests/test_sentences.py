from narravid.sentences import all_sentences, parse_script, part_number, split_sentences


def test_abbreviations_and_initials_do_not_split():
    s = split_sentences("As F. M. Scherer noted, e.g. in 1982, the rate was 0.08 per cent. Next point follows.")
    assert s == ["As F. M. Scherer noted, e.g. in 1982, the rate was 0.08 per cent.", "Next point follows."]


def test_decimals_suffixes_and_et_al():
    s = split_sentences("We used 450k reads and p = 0.08. Smith et al. found more. Really? Yes!")
    assert s == ["We used 450k reads and p = 0.08.", "Smith et al. found more.", "Really?", "Yes!"]


def test_quotes_after_terminator():
    s = split_sentences('He said "it works." Then he left.')
    assert s == ['He said "it works."', "Then he left."]


def test_lowercase_after_period_is_not_a_boundary():
    assert len(split_sentences("The value vs. the bound differs. Second.")) == 2


def test_part_numbers():
    assert part_number("one") == 1 and part_number("3") == 3 and part_number("twenty-one") == 21
    assert part_number("second") == 2 and part_number("banana") is None


def test_parse_parts_and_heading_sentences():
    text = "Part one. Intro.\nFirst sentence. Second one!\n\nPart 2: Methods\nThird sentence?"
    sc = parse_script(text)
    assert [p["part"] for p in sc["parts"]] == [1, 2]
    assert sc["parts"][0]["title"] == "Intro"
    assert sc["parts"][1]["title"] == "Methods"
    sents = all_sentences(sc)
    assert [s["text"] for s in sents] == ["Part one. Intro.", "First sentence.", "Second one!", "Part 2: Methods",
                                          "Third sentence?"]
    assert [s["heading"] for s in sents] == [True, False, False, True, False]
    assert [s["id"] for s in sents] == [1, 2, 3, 4, 5]


def test_heading_with_body_on_same_line():
    sc = parse_script("Part one. Classical limit. The bound is two. It holds.")
    sents = all_sentences(sc)
    assert sents[0]["text"] == "Part one. Classical limit." and sents[0]["heading"]
    assert [s["text"] for s in sents[1:]] == ["The bound is two.", "It holds."]


def test_exact_text_preserved(fixtures):
    text = (fixtures / "sample_script.txt").read_text(encoding="utf-8")
    sents = all_sentences(parse_script(text))
    joined = " ".join(s["text"] for s in sents)
    assert joined.split() == text.split()            # nothing lost, nothing added
    assert any("F. M. Scherer" in s["text"] for s in sents)
    assert len(sents) == 13
