import json
from pathlib import Path

import pytest

from chomp.discovery import discover
from chomp.models import Candidate, Observation, TranscriptSegment, checked_span
from chomp.ranking import heuristic_rank, quality, select
from chomp.signals import audio_observations, chat_observations, read_chat, read_transcript, transcript_observations


def test_timebase_rejects_invalid_ms():
    for args in [(0,0,300),(10,9,300),(-1,50,300),(0,301,300),(1.5,20,300)]:
        with pytest.raises((ValueError,TypeError)):
            checked_span(*args)


def test_audio_peaks_and_zero_audio():
    curve = [-40.]*12 + [-8.,-7.] + [-40.]*13
    peaks = audio_observations(curve,27_000)
    assert any(12_000 <= p.start_ms <= 13_000 for p in peaks)
    assert audio_observations([-40.]*30,30_000) == []


def test_transcript_broad_without_emotional_keyword():
    words = [TranscriptSegment(1000, 4500, "I found a pattern in these locations.")]
    obs = transcript_observations(words,100_000)
    assert len(obs)==1 and obs[0].modality=="transcript"
    result=discover(obs,words,100_000)
    assert result and "pattern" in result[0].transcript_excerpt


def test_chat_unique_users_and_absence():
    assert chat_observations([],20_000)==[]
    from chomp.models import ChatMessage
    flooded = [ChatMessage(1000,"same", "wow") for _ in range(50)]
    assert chat_observations(flooded,20_000)==[]
    genuine = [ChatMessage(1000,"a","wow"),ChatMessage(1200,"b","wow"),ChatMessage(1300,"c","clip"),ChatMessage(1500,"d","!!!")]
    assert len(chat_observations(genuine,20_000)) == 1


def test_rank_before_duplicate_suppression():
    strong = Candidate(10000,35000,20000,score=89)
    weak = Candidate(12000,34000,21000,score=35)
    separate = Candidate(60000,90000,75000,score=81)
    assert select([weak,strong,separate], min_score=0) == [strong,separate]


def test_no_forced_three_clips():
    assert select([Candidate(0,1000,500,score=12)], min_score=45)==[]


def test_score_formula():
    assert quality({"category_payoff":5,"standalone_clarity":5,"hook_strength":5,"distinctiveness":5})==100
    with pytest.raises(ValueError):
        quality({"category_payoff":6,"standalone_clarity":3,"hook_strength":3,"distinctiveness":3})


def test_json_milliseconds_and_seconds(tmp_path: Path):
    path=tmp_path/"transcript.json"
    path.write_text(json.dumps({"segments":[{"start":1.2,"end":3.5,"text":"Hey!"},{"start_ms":5000,"end_ms":6200,"text":"Wait."}]}))
    assert [s.start_ms for s in read_transcript(path,8000)]==[1200,5000]
    chatfile=tmp_path/"chat.json"
    chatfile.write_text(json.dumps([{"time":3,"user_id":"u1","text":"clip"}]))
    assert read_chat(chatfile,8000,offset_ms=500)[0].time_ms==3500
