from voice_agent.text import normalize_for_speech, split_sentences, spoken_digits


def test_split_holds_incomplete_and_merges_short():
    sents, rest = split_sentences("Hi. Aarav is in class eight. His attendance is")
    assert sents == ["Hi. Aarav is in class eight."]
    assert rest.strip() == "His attendance is"


def test_normalize_rupees_percent_numbers_markdown():
    out = normalize_for_speech("**Fees:** ₹1,50,000 due. Attendance 94.5%. Roll No. 12")
    assert "one lakh, fifty thousand rupees" in out
    assert "ninety-four point five percent" in out
    assert "Number twelve" in out
    assert "*" not in out


def test_strip_think_block():
    assert normalize_for_speech("<think>hmm</think>Hello there.") == "Hello there."


def test_spoken_digits():
    assert spoken_digits("four three two one") == "4321"
    assert spoken_digits("4 3 2 1") == "4321"
    assert spoken_digits("char teen do ek") == "4321"
