"""Reproducible synthetic before/after evidence, NOT subjective clip quality.

Run from an editable install: python tests/bench_boundaries_stage1.py
Baseline copied from feat/vod-foundation at 37d8ff2; no test media needed.
"""
from dataclasses import dataclass
import statistics
from time import perf_counter

from chomp.boundaries import refine_boundaries as improved
from chomp.models import Candidate, TranscriptSegment, checked_span
from chomp.signals import nearby_text


@dataclass(frozen=True)
class OldDecision:
    start_ms: int
    end_ms: int
    method: str


def original(candidate, transcript, duration_ms, *, max_length_ms=60_000,
             min_length_ms=8_000, context_ms=6_000):
    """Exact selection and padding calculations from the 37d8ff2 version."""
    candidate.validate(duration_ms)
    if not 0 < min_length_ms <= max_length_ms:
        raise ValueError("invalid clip length configuration")
    if context_ms < 0:
        raise ValueError("context_ms must be nonnegative")
    start, end = candidate.start_ms, candidate.end_ms
    nearby = [s for s in transcript if s.end_ms >= start-context_ms and s.start_ms <= end+context_ms]
    method = "signal_padding"
    if nearby:
        start = max(0, min(start, min(s.start_ms for s in nearby)-500))
        end = min(duration_ms, max(end, max(s.end_ms for s in nearby)+1000))
        method = "transcript_assisted"
    if end-start > max_length_ms:
        start = max(start, min(candidate.anchor_ms-round(max_length_ms*.45), end-max_length_ms))
        end = start+max_length_ms
        method += "_capped"
    if end-start < min_length_ms and duration_ms >= min_length_ms:
        missing = min_length_ms-(end-start)
        start = max(0,start-missing//2)
        end = min(duration_ms,start+min_length_ms)
        start = max(0,end-min_length_ms)
    checked_span(start,end,duration_ms)
    candidate.start_ms, candidate.end_ms = start,end
    candidate.transcript_excerpt = nearby_text(transcript,start,end)
    candidate.validate(duration_ms)
    return OldDecision(start,end,method)


def seg(a,b,text="speech",speaker=None):
    return TranscriptSegment(a,b,text,speaker)


def cases():
    return {
        "short reaction": (Candidate(40_000,63_000,49_000),[seg(35_000,37_000,"other"),seg(48_000,50_000,"No way!"),seg(58_000,60_000,"other")],120_000),
        "continuous conversation": (Candidate(2_000,100_000,51_000),[seg(i,i+2_700,"talk") for i in range(0,110_000,3_000)],120_000),
        "setup / delayed payoff": (Candidate(18_000,29_000,23_000),[seg(13_000,20_000,"setup","A"),seg(25_000,36_000,"payoff","B")],120_000),
        "multiple speakers": (Candidate(12_000,32_000,21_000),[seg(16_000,19_000,"Q?","S1"),seg(19_600,22_000,"A!","S2"),seg(22_400,26_000,"Reaction","S1")],120_000),
        "quiet anchor": (Candidate(35_000,45_000,40_000),[seg(25_000,28_000,"prior"),seg(50_000,54_000,"after")],120_000),
        "no transcript broad": (Candidate(10_000,80_000,60_000),[],120_000),
    }


def main():
    for name, (c, segments, duration) in cases().items():
        old = original(Candidate(c.start_ms,c.end_ms,c.anchor_ms), segments, duration)
        new = improved(Candidate(c.start_ms,c.end_ms,c.anchor_ms), segments, duration)
        print(f"{name:25}: old {old.start_ms:6}-{old.end_ms:6} ({(old.end_ms-old.start_ms)/1000:5.2f}s), new {new.start_ms:6}-{new.end_ms:6} ({(new.end_ms-new.start_ms)/1000:5.2f}s)")

    # Timing-only microbenchmark. No audio decode or subjective quality scoring.
    duration = 4*3600*1000
    transcript = [seg(i*1300,i*1300+900,"line") for i in range(duration//1300)]
    anchors = [max(20_000,min(duration-20_000,20_000+i*199_000)) for i in range(70)]
    for label, algorithm in (("original", original), ("improved", improved)):
        timings=[]
        for _ in range(5):
            t=perf_counter()
            for anchor in anchors:
                algorithm(Candidate(anchor-10_000,anchor+14_000,anchor),transcript,duration)
            timings.append(perf_counter()-t)
        print(f"{label:8} median of five / 4h, {len(transcript)} segments, {len(anchors)} candidates: {statistics.median(timings):.4f}s")


if __name__ == "__main__":
    main()
