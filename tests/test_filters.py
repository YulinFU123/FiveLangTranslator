from app.asr.filters import HallucinationFilter


def test_empty_text_is_rejected():
    decision = HallucinationFilter().evaluate("   ")
    assert decision.accepted is False
    assert decision.reason == "empty"


def test_high_no_speech_probability_is_rejected():
    decision = HallucinationFilter().evaluate("hello", .9, .92)
    assert decision.accepted is False
    assert decision.reason == "high_no_speech_probability"


def test_known_outro_hallucination_is_rejected():
    decision = HallucinationFilter().evaluate("Thanks for watching", .9)
    assert decision.accepted is False


def test_repetition_loop_is_rejected():
    filter_ = HallucinationFilter()
    assert filter_.evaluate("same line", .9).accepted
    assert filter_.evaluate("same line", .9).accepted
    assert filter_.evaluate("same line", .9).accepted
    decision = filter_.evaluate("same line", .9)
    assert decision.accepted is False
    assert decision.reason == "repetition_loop"


def test_normal_text_is_accepted():
    decision = HallucinationFilter().evaluate("I think he knows the truth.", .85)
    assert decision.accepted is True
