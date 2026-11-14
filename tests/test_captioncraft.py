"""CAPT-08 synthetic regressions; no claim of perceptual caption quality."""
from __future__ import annotations

import random
import re
import shutil
import subprocess

import pytest

from chomp.captions import _chunks, make_srt
from chomp.models import TranscriptSegment


def segment(start, end, text, speaker=None):
    return TranscriptSegment(start, end, text, speaker)


def parse_srt(text: str):
    if not text:
        return []
    blocks = re.split(r'\n\s*\n', text.strip())
    cues = []
    for number, block in enumerate(blocks, 1):
        lines = block.splitlines()
        assert lines[0] == str(number)
        assert re.fullmatch(r'\d\d+:\d\d:\d\d,\d{3} --> \d\d+:\d\d:\d\d,\d{3}', lines[1])
        def time(value):
            hours, mins, secs = value.split(':')
            whole, frac = secs.split(',')
            return ((int(hours) * 60 + int(mins)) * 60 + int(whole)) * 1000 + int(frac)
        left, right = map(time, lines[1].split(' --> '))
        assert right > left
        assert len(lines[2:]) <= 2
        assert all(0 < len(line) <= 28 for line in lines[2:])
        cues.append((left, right, '\n'.join(lines[2:])))
    for previous, current in zip(cues, cues[1:]):
        assert previous[1] <= current[0]  # serial, chronological, no competing cues
    return cues


def generate(parts, start, end, duration):
    output, count = make_srt(parts, start, end, duration)
    parsed = parse_srt(output)
    assert count == len(parsed)
    assert all(0 <= a < b <= end - start for a, b, _ in parsed)
    return parsed


def test_current_pipeline_contract_and_exact_srt_format():
    parts = [segment(3000, 8000, 'Setup <something>'),
             segment(9000, 13000, 'then a fun payoff!')]
    srt, count = make_srt(parts, 5000, 12000, 20000)
    assert count == 2
    assert srt == ('1\n00:00:00,000 --> 00:00:03,000\n… ‹something›\n\n'
                   '2\n00:00:04,000 --> 00:00:07,000\nthen a fun …\n\n')
    assert generate(parts, 5000, 12000, 20000) == [
        (0, 3000, '… ‹something›'), (4000, 7000, 'then a fun …')]


def test_empty_and_whitespace_only_no_subtitles():
    assert make_srt([], 5000, 12000, 20000) == ('', 0)
    assert make_srt([segment(0, 5000, ' \r\t\n ')], 0, 5000, 5000) == ('', 0)


def test_short_reaction_and_micro_utterance_no_extension_into_silence():
    cues = generate([segment(2000, 3100, 'No way!')], 0, 8000, 8000)
    assert cues == [(2000, 3100, 'No way!')]
    micro = generate([segment(1000, 1300, 'Go!')], 0, 5000, 5000)
    assert micro == [(1000, 1300, 'Go!')]
    assert generate([segment(1000, 1170, 'flicker')], 0, 5000, 5000) == []


def test_long_utterance_two_lines_and_no_flash():
    text = ('This is a much longer sentence that should be wrapped into one or '
            'two lines and never produce a flash even for a quickly narrated joke.')
    cues = generate([segment(0, 12000, text)], 0, 12000, 12000)
    assert len(cues) >= 2
    assert all(b - a >= 700 for a, b, _ in cues)
    assert all(b - a <= 6000 for a, b, _ in cues)
    assert ' '.join(t.replace('\n', ' ') for _, _, t in cues) == text
    assert cues[0][0] == 0 and cues[-1][1] == 12000


def test_fast_speech_stays_readable_by_transparent_abbreviation():
    text = ' '.join('quickly' for _ in range(50))
    cues = generate([segment(1000, 2500, text)], 0, 8000, 8000)
    assert 1 <= len(cues) <= 2
    assert all(b - a >= 700 for a, b, _ in cues)
    assert all(len(t.replace('\n', ' ')) / ((b-a)/1000) <= 25 for a,b,t in cues)
    assert cues[-1][2].endswith('…')
    assert cues[-1][1] <= 2500


def test_slow_speech_avoids_stale_ten_second_cue():
    cues = generate([segment(0, 20000, 'Okay.')], 0, 20000, 20000)
    assert len(cues) == 1
    assert cues[0] == (0, 6000, 'Okay.')


def test_silence_gap_remains_blank_and_source_end_restricted():
    cues = generate([segment(1000, 3000, 'Hello.'),
                     segment(8000, 10000, 'Goodbye.')], 0, 12000, 12000)
    assert len(cues) == 2
    assert cues[0][1] == 3000
    assert cues[1][0] == 8000
    assert cues[1][1] == 10000


def test_overlap_prefers_next_speaker_without_stacked_cues():
    parts = [segment(2000, 8000, 'The first person is speaking for some time.', 'A'),
             segment(5000, 10000, 'The second person has an interruption here.', 'B'),
             segment(7000, 11000, 'A final important response occurs.', 'C')]
    cues = generate(parts, 0, 12000, 12000)
    assert len(cues) >= 3
    assert any(a < 5000 and b <= 5000 for a,b,_ in cues)
    assert any(5000 <= a < 7000 and b <= 7000 for a,b,_ in cues)
    assert any(a >= 7000 for a,_,_ in cues)
    assert all('‹' not in t for _,_,t in cues)


def test_same_onset_deterministic_no_double_caption():
    parts = [segment(1000, 5000, 'first speaker', 'S1'),
             segment(1000, 6000, 'second speaker', 'S2')]
    cues = generate(parts, 0, 7000, 7000)
    assert len(cues) == 1 and 'first speaker' in cues[0][2]
    assert 'second' not in cues[0][2]
    assert make_srt(parts, 0, 7000, 7000) == make_srt(parts,0,7000,7000)


def test_truncation_at_clip_open_and_close_is_approximated_and_marked():
    parts = [segment(1000, 9000, 'one two three four five six seven eight')]
    left = generate(parts, 5000, 9000, 12000)
    assert left and left[0][2].startswith('…')
    assert 'one two three four five six seven eight' not in left[0][2]
    right = generate(parts, 1000, 5000, 12000)
    assert right and right[-1][2].endswith('…')
    both = generate(parts, 3000, 7000, 12000)
    assert both and both[0][2].startswith('…') and both[-1][2].endswith('…')
    assert generate(parts, 8950, 9050, 12000) == []


def test_source_beginning_end_and_clip_local_clock():
    segs = [segment(0, 1500, 'start!'),segment(8500, 10000, 'end!')]
    cues = generate(segs, 0, 10000, 10000)
    assert cues[0][0] == 0 and cues[-1][1] == 10000
    assert generate(segs, 9000, 10000, 10000)[-1][1] == 1000
    assert generate(segs, 0, 1000, 10000)[0][0] == 0


def test_unicode_nepali_and_english_and_unsafe_markup():
    txt = 'नमस्ते साथीहरू! Kathmandu <font color="red">hi</font> {\\an7} 💙\\N अर्को कुरा।'
    cues = generate([segment(0, 5000, txt)], 0, 5000, 5000)
    joined = ' '.join(t.replace('\n', ' ') for _,_,t in cues)
    assert 'नमस्ते' in joined and 'अर्को' in joined and '💙' in joined
    assert '<' not in joined and '>' not in joined and '{' not in joined and '}' not in joined
    assert '\\N' not in joined and '\\an7' not in joined
    assert '‹font' in joined and '（' in joined
    # User-controlled newlines cannot inject SRT cue blocks.
    adversarial = 'hello\n\n2\n00:00:00,000 --> 99:00:00,000\nworld'
    assert len(generate([segment(0, 3000, adversarial)],0,3000,3000)) <= 2


def test_long_unspaced_nepali_conjunct_no_detached_virama():
    txt = 'क्षेत्र' * 15
    lines = _chunks(txt)
    assert len(lines) > 1
    assert all(len(line) <= 28 for line in lines)
    assert ''.join(lines) == txt
    assert all(not line.endswith('\u094d') for line in lines)


def test_long_uninterrupted_asr_segment_is_bounded():
    text = ' '.join('explanation' for _ in range(70))
    cues = generate([segment(0, 80000, text)], 0, 80000, 80000)
    assert len(cues) > 2
    assert all(b-a <= 6000 for a,b,_ in cues)
    assert all(b <= 80000 for a,b,_ in cues)
    assert all(len(line) <= 28 for _,_,t in cues for line in t.split('\n'))


def test_out_of_order_input_sorted_and_exact_nonoverlap():
    items = [segment(8000, 9000, 'later'),segment(1000, 3000, 'earlier'),
             segment(4000, 7000, 'middle')]
    cues = generate(items, 0, 10000, 10000)
    assert [text for _,_,text in cues] == ['earlier', 'middle', 'later']
    assert [a for a,_,_ in cues] == [1000,4000,8000]


@pytest.mark.parametrize('span', [(-1,1000,1000), (0,0,1000), (0,1200,1000),
                                   (0,1000,1000.0), (0.0,500,1000)])
def test_invalid_clip_span_rejected(span):
    with pytest.raises((ValueError,TypeError)):
        make_srt([], *span)


def test_invalid_transcript_span_rejected_even_if_outside_clip():
    with pytest.raises(ValueError):
        make_srt([segment(-1,500,'bad')],500,900,2000)
    with pytest.raises(TypeError):
        make_srt([segment(0,100,'bad',None), segment(100,300,19)],0,300,1000)


def test_randomized_timing_line_contract():
    rng = random.Random(20261114)
    vocab = ['Nepali', 'यो', 'मजा', 'right?', 'awesome!', 'okay,', 'क्षेत्र', 'साथीहरू']
    for _ in range(200):
        duration = rng.randint(1, 90000)
        start = rng.randrange(duration)
        end = rng.randint(start+1,duration)
        segments = []
        for _ in range(rng.randint(0, 10)):
            a = rng.randrange(duration)
            b = rng.randint(a+1, duration)
            text = ' '.join(rng.choice(vocab) for _ in range(rng.randint(1,20)))
            segments.append(segment(a,b,text))
        generate(segments,start,end,duration)


@pytest.mark.skipif(shutil.which('ffmpeg') is None or shutil.which('ffprobe') is None,
                    reason='requires local FFmpeg render binaries')
def test_render_with_existing_ffmpeg_compositor_and_unicode_srt(tmp_path):
    # No compositor changes; integration against the existing public renderer.
    media = pytest.importorskip('chomp.media')
    work = tmp_path / 'video folder with spaces'
    work.mkdir()
    original = work / 'source clip.mp4'
    subprocess.run([
        'ffmpeg', '-hide_banner','-nostdin','-y','-v','error',
        '-f','lavfi','-i','testsrc2=size=320x180:rate=12:duration=3',
        '-f','lavfi','-i','sine=frequency=600:duration=3',
        '-shortest','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac',str(original),
    ],check=True)
    subtitle = work / 'candidate_01.srt'
    txt,_ = make_srt([segment(250,2600,'नमस्ते Hello <safe>!')],0,3000,3000)
    subtitle.write_text(txt,encoding='utf-8')
    export = work / 'candidate_01.mp4'
    media.render_preview(original,export,0,3000,captions_path=subtitle)
    probed = media.probe(export)
    assert any(s.get('codec_type') == 'video' for s in probed['streams'])
    assert any(s.get('codec_type') == 'audio' for s in probed['streams'])
