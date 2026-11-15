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


def test_boundary_refinement_includes_neighboring_speech_and_caps():
    from chomp.boundaries import refine_boundaries
    c = Candidate(18_000, 29_000, 23_000, observations=[])
    segs = [TranscriptSegment(13_000,20_000,"setup"), TranscriptSegment(25_000,36_000,"payoff")]
    out = refine_boundaries(c,segs,100_000,max_length_ms=50_000)
    assert out.start_ms <= 13_000 and out.end_ms >= 36_000
    assert c.start_ms == out.start_ms and c.anchor_ms >= c.start_ms
    too_long = Candidate(10_000,100_000,70_000)
    out2 = refine_boundaries(too_long,[],120_000,max_length_ms=30_000)
    assert out2.end_ms-out2.start_ms==30_000
    assert out2.start_ms <= too_long.anchor_ms <= out2.end_ms


def test_caption_generation_is_clip_local_and_sanitized():
    from chomp.captions import make_srt
    parts = [TranscriptSegment(3_000,8_000,"Setup <something>"), TranscriptSegment(9_000,13_000,"then a fun payoff!")]
    srt,n = make_srt(parts,5_000,12_000,20_000)
    assert n==2
    assert "00:00:00,000 --> 00:00:03,000" in srt
    assert "00:00:04,000 --> 00:00:07,000" in srt
    assert "‹something›" in srt and "<something>" not in srt
    assert make_srt([],5_000,12_000,20_000)==("",0)


def test_boundary_rejects_invalid_config():
    from chomp.boundaries import refine_boundaries
    c=Candidate(1000,2000,1500)
    with pytest.raises(ValueError):
        refine_boundaries(c,[],10_000,max_length_ms=1000,min_length_ms=2000)


def test_top_three_does_not_repeat_large_part_of_shorter_clip():
    # Real VOD regression: 6:33-7:22 and 7:02-7:59 share ~20 s.
    high = Candidate(422230,478880,437550,score=61)
    duplicate = Candidate(393790,442050,407490,score=49)
    distinct = Candidate(770000,805000,782000,score=55)
    assert select([duplicate, distinct, high],min_score=45)==[high, distinct]
    from chomp.ranking import overlap_ratio
    assert overlap_ratio(high,duplicate) > .4


def test_speaker_turns_and_conservative_attribution(tmp_path: Path):
    from chomp.speakers import read_speaker_turns, attribute_segments, speaker_inventory, candidate_speaker_context
    from chomp.models import TranscriptSegment
    turns_file = tmp_path / "turns.json"
    turns_file.write_text(json.dumps({"turns":[
        {"start": 0, "end": 5, "speaker_id": "S0"},
        {"start": 5, "end": 10, "speaker_id": "S1"}
    ]}))
    turns=read_speaker_turns(turns_file,10_000)
    segs=[TranscriptSegment(1_000,3_000,"Streamer line"),
          TranscriptSegment(4_000,6_000,"two people talk"),
          TranscriptSegment(6_000,8_000,"Teammate line")]
    labelled=attribute_segments(segs,turns)
    assert [x.speaker_id for x in labelled]==["S0",None,"S1"]
    assert speaker_inventory(labelled,turns,"S0")==["S0","S1"]
    with pytest.raises(ValueError,match="does not occur"):
        speaker_inventory(labelled,turns,"wrong")
    context=candidate_speaker_context(labelled,0,9_000,"S0")
    assert [c["role"] for c in context]==["main_streamer","unverified","other_speaker"]


def test_transcript_speaker_label_preserved(tmp_path: Path):
    path=tmp_path/"labelled.json"
    path.write_text(json.dumps({"segments":[{"start_ms":100,"end_ms":700,"text":"Hello", "speaker_id":"SPEAKER_00"}]}))
    transcript=read_transcript(path,1000)
    assert transcript[0].speaker_id=="SPEAKER_00"
