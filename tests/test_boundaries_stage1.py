"""Section 6: synthetic contract tests, NOT perceived highlight-quality scores."""
from __future__ import annotations

from dataclasses import replace
import random

import pytest

from chomp.boundaries import BoundaryDecision, refine_boundaries
from chomp.models import Candidate, TranscriptSegment

DURATION = 120_000


def segment(start, end, text="speech", who=None):
    return TranscriptSegment(start, end, text, who)


def candidate(start, end, anchor):
    return Candidate(start, end, anchor)


def run(c, transcript=(), duration=DURATION, **settings):
    original_anchor = c.anchor_ms
    result = refine_boundaries(c, list(transcript), duration, **settings)
    assert isinstance(result, BoundaryDecision)
    assert c.start_ms == result.start_ms and c.end_ms == result.end_ms
    assert result.start_ms <= original_anchor <= result.end_ms
    assert 0 <= result.start_ms < result.end_ms <= duration
    return result


def test_short_reaction_ignores_distant_chat_and_limits_length():
    c = candidate(40_000, 63_000, 49_000)
    result = run(c, [segment(35_000,37_000,"unrelated"),segment(48_000,50_000,"No way!"),
                     segment(58_000,60_000,"different topic")])
    assert 44_000 <= result.start_ms < 48_000
    assert 50_000 <= result.end_ms < 54_000
    assert result.end_ms - result.start_ms <= 12_000
    assert c.transcript_excerpt == "No way!"


def test_joke_setup_and_payoff_separated_by_pause():
    c = candidate(18_000, 29_000, 23_000)
    result = run(c, [segment(13_000,20_000,"Why did it happen?", "A"),
                     segment(25_000,36_000,"Because of that!", "B")],
                 max_length_ms=50_000)
    assert result.start_ms <= 13_000 and result.end_ms >= 36_000
    assert "Why" in c.transcript_excerpt and "Because" in c.transcript_excerpt


def test_continuous_conversation_must_not_fill_maximum():
    c = candidate(2_000, 100_000, 51_000)
    transcript = [segment(i, i+2_700, f"talk {i}") for i in range(0, 110_000, 3_000)]
    result = run(c, transcript)
    assert result.end_ms - result.start_ms <= 34_000
    assert result.start_ms >= 40_000
    assert result.end_ms <= 65_000


def test_multispeaker_turns_without_streamer_preference():
    c = candidate(12_000, 32_000, 21_000)
    speech = [segment(16_000,19_000,"Question?", "teammate"),
              segment(19_600,22_000,"I found it!", "streamer"),
              segment(22_400,26_000,"Nice save.", "teammate")]
    result = run(c, list(reversed(speech)))
    assert result.start_ms < 16_000 and result.end_ms > 26_000
    assert "Question" in c.transcript_excerpt and "Nice" in c.transcript_excerpt


def test_quiet_anchor_far_from_speech_uses_fallback():
    c = candidate(35_000, 45_000, 40_000)
    result = run(c, [segment(25_000,28_000,"noise"),segment(50_000,54_000,"different")])
    assert result.method == "signal_padding_fallback"
    assert 35_000 <= result.start_ms and result.end_ms <= 45_000


def test_incomplete_transcript_sparse_anchor():
    c = candidate(10_000, 80_000, 60_000)
    result = run(c, [segment(10_000,20_000,"long-ago context")])
    assert result.method == "signal_padding_fallback"
    assert result.end_ms - result.start_ms == 30_000
    assert "long-ago context" not in c.transcript_excerpt


def test_extreme_source_boundaries_and_min_duration():
    a = run(candidate(0, 2_000, 0), [segment(0, 1_200,"opening")], duration=9_000)
    assert a.start_ms == 0 and a.end_ms - a.start_ms >= 8_000
    b = run(candidate(7_000, 9_000, 9_000), [segment(8_000,9_000,"ending")], duration=9_000)
    assert b.end_ms == 9_000 and b.end_ms - b.start_ms >= 8_000
    short = run(candidate(1_000, 2_000, 1_500), [], duration=3_000)
    assert (short.start_ms, short.end_ms) == (0,3_000)


def test_absent_transcript_and_audio_returns_deterministic_result():
    c1 = candidate(10_000, 99_000, 55_000)
    c2 = replace(c1)
    a, b = run(c1), run(c2)
    assert a == b
    assert a.method == "signal_padding_fallback"
    assert a.end_ms - a.start_ms == 30_000


def test_audio_quiet_hints_optional_and_not_event_detection():
    c = candidate(20_500, 29_500, 25_000)
    curve = [-25.0] * 120
    curve[20] = curve[29] = -70.0
    result = run(c, [], audio_levels=curve)
    assert result.method == "signal_padding_audio_silence"
    assert result.start_ms == 20_000 and result.end_ms == 30_000
    # A huge spike isn't evidence to move the event or the clip.
    loud = [-30.] * 120
    loud[26] = 0.
    assert run(candidate(20_500,29_500,25_000),[],audio_levels=loud).method == "signal_padding_fallback"


def test_wrong_audio_samples_do_not_break_without_audio():
    with pytest.raises(ValueError):
        run(candidate(1_000,2_000,1_500), [], audio_hop_ms=0)
    with pytest.raises(TypeError):
        run(candidate(1_000,2_000,1_500), [], audio_levels=["loud"])
    assert run(candidate(1_000,3_000,2_000),[],audio_levels=[float("nan")]*12).start_ms >= 0


def test_single_huge_asr_segment_sets_hard_cut_method():
    c = candidate(20_000,80_000,55_000)
    out = run(c, [segment(15_000,95_000,"one very long unaligned ASR chunk")])
    assert out.method == "transcript_assisted_hard_cut"
    assert out.end_ms - out.start_ms == 34_000
    assert "may split speech" in out.note


def test_empty_and_overlapping_multispeaker_segments():
    c = candidate(20_000,30_000,25_000)
    segs = [segment(24_000,27_000,"first","A"),segment(24_500,28_000,"second","B"),
            segment(28_300,31_000,"   ")]
    res = run(c, segs)
    assert res.start_ms <= 24_000 and res.end_ms >= 28_000
    assert "first" in c.transcript_excerpt and "second" in c.transcript_excerpt


@pytest.mark.parametrize("opts", [dict(max_length_ms=0),dict(min_length_ms=0),
    dict(min_length_ms=20_000,max_length_ms=10_000),dict(context_ms=-1),
    dict(max_length_ms=60_000.0),dict(context_ms=0.5)])
def test_bad_config_rejected(opts):
    with pytest.raises(ValueError):
        run(candidate(3_000,7_000,5_000),[],**opts)


def test_invalid_transcript_rejected_not_silently_used():
    with pytest.raises(ValueError):
        run(candidate(5_000,9_000,7_000),[segment(-1,2_000)])


def test_span_cap_with_shorter_explicit_max():
    c = candidate(5_000,100_000,70_000)
    result = run(c, [], max_length_ms=30_000)
    assert result.end_ms - result.start_ms == 30_000


def test_randomized_time_contract_and_repeatability():
    rng = random.Random(20261114)
    for _ in range(200):
        duration = rng.randint(1, 300_000)
        start = rng.randrange(duration)
        end = rng.randint(start + 1, duration)
        anchor = rng.randint(start,end)
        minimum = rng.randint(1, 10_000)
        maximum = rng.randint(minimum, 60_000)
        segs = []
        for _ in range(8):
            a = rng.randrange(duration)
            b = rng.randint(a+1,duration)
            segs.append(segment(a,b))
        a = run(candidate(start,end,anchor), segs, duration=duration,
                min_length_ms=minimum,max_length_ms=maximum)
        assert a.end_ms-a.start_ms <= maximum
        assert a.end_ms-a.start_ms >= min(minimum,duration)


def test_repeated_refinement_does_not_drop_the_setup():
    c = candidate(18_000,29_000,23_000)
    speech = [segment(13_000,20_000,"setup"),segment(25_000,36_000,"payoff")]
    first = run(c,speech)
    second = run(c,speech)
    assert first == second

