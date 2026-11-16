"""Faithful clip-local captions with explicit timing and readability diagnostics."""
from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata

from .models import TimedWord, TranscriptSegment, checked_span

# No renderer font/style changes here. Code-point limits are *heuristics*,
# not glyph widths: Devanagari combining marks and CJK glyphs vary visually.
_LINE_CHARS = 28
_LINES_PER_CUE = 2
_TARGET_CPS = 24          # maximum approximate codepoints per second
_MIN_TARGET_MS = 700      # never extend a cue beyond its known speech window
_MIN_USABLE_MS = 250      # flag brief cues; keep the corresponding words
_MAX_HOLD_MS = 6_000     # avoid stale captions across sparse ASR spans
_TERMINATORS = '.!?।؟'
_BIDI_CONTROLS = {chr(c) for c in (*range(0x202a, 0x202f), *range(0x2066, 0x206a), 0x200e, 0x200f)}


def _clock(milliseconds: int) -> str:
    hours, rest = divmod(milliseconds, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    seconds, millis = divmod(rest, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02},{millis:03}"


def _sanitize(text: str) -> str:
    """Collapse SRT-breaking newlines and neutralize HTML/ASS styling controls.

    Keep language text, punctuation, emoji and Devanagari combining characters;
    never strip Unicode diacritics just to make char limits easier to enforce.
    """
    cleaned = []
    for char in text:
        if char in _BIDI_CONTROLS:
            continue
        if char.isspace() or unicodedata.category(char) in ('Cc', 'Cs'):
            cleaned.append(' ')
        else:
            cleaned.append({'<': '‹', '>': '›', '{': '（', '}': '）', '\\': '∖'}.get(char, char))
    return re.sub(r'\s+', ' ', ''.join(cleaned)).strip()


def _graphemes(text: str) -> list[str]:
    """Small stdlib approximation: avoid splitting marks/virama/ZWJ sequences.

    This is not a full Unicode grapheme-break implementation. It handles
    common Nepali dependent vowels and conjuncts without another dependency.
    """
    out: list[str] = []
    for char in text:
        if out and (unicodedata.category(char).startswith('M') or char in ('\u200c','\u200d')
                    or out[-1].endswith(('\u200c','\u200d','\u094d'))):
            out[-1] += char
        else:
            out.append(char)
    return out


def _width(text: str) -> int:
    """Estimate glyph width in narrow characters pending the rendered preview."""
    width = sum(0 if unicodedata.category(c).startswith("M") else
                2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in text)
    return max(len(text), width)


def _split_token(word: str, maximum: int) -> list[str]:
    groups: list[str] = []
    current = ''
    for item in _graphemes(word):
        if current and _width(current + item) > maximum:
            groups.append(current)
            current = ''
        current += item
    if current:
        groups.append(current)
    return groups


def _chunks(text: str, maximum: int = 28) -> list[str]:
    """Return short, printable single lines (kept for pipeline test users)."""
    if type(maximum) is not int or maximum < 2:
        raise ValueError('maximum must be an integer >= 2')
    clean = _sanitize(text)
    if not clean:
        return []
    lines: list[str] = []
    current = ''
    for word in clean.split(' '):
        pieces = _split_token(word, maximum)
        for part_index, piece in enumerate(pieces):
            if current and _width(current + ' ' + piece) > maximum:
                lines.append(current)
                current = ''
            # After a strong sentence terminator prefer an intentional break.
            if current and current[-1] in _TERMINATORS and len(current) >= 8:
                lines.append(current)
                current = ''
            if not current:
                current = piece
            elif part_index == 0:
                current = current + ' ' + piece
            else:
                # Long unspaced token was already split: no invented spaces.
                current += piece
            if part_index < len(pieces) - 1:
                lines.append(current)
                current = ''
    if current:
        lines.append(current)
    return lines


def _estimated_visible_text(
    text: str, source_start: int, source_end: int, visible_start: int, visible_end: int,
) -> str:
    """Conservatively select approximate words from a clipped ASR segment.

    Proportional positions are NOT true word timestamps. Ellipses disclose
    missing beginning/end. Tiny, unsupported fragments may produce no text.
    """
    clean = _sanitize(text)
    if not clean:
        return ''
    left_cut, right_cut = visible_start > source_start, visible_end < source_end
    if not left_cut and not right_cut:
        return clean
    span = source_end - source_start
    beginning = (visible_start - source_start) / span
    ending = (visible_end - source_start) / span
    words = clean.split(' ')
    if len(words) == 1:
        chars = _graphemes(clean)
        chosen = [part for i, part in enumerate(chars) if beginning <= (i + .5) / len(chars) < ending]
        middle = ''.join(chosen)
    else:
        weights = [len(w) + 1 for w in words]
        total = sum(weights)
        elapsed = 0
        kept = []
        for word, weight in zip(words, weights):
            midpoint = (elapsed + weight / 2) / total
            if beginning <= midpoint < ending:
                kept.append(word)
            elapsed += weight
        middle = ' '.join(kept)
    if not middle.strip():
        return ''
    return ('… ' if left_cut else '') + middle + (' …' if right_cut else '')


def _cue_texts(clean: str) -> list[str]:
    lines = _chunks(clean, _LINE_CHARS)
    if len(lines) == 2:
        words = _sanitize(clean).split()
        choices = [(' '.join(words[:i]), ' '.join(words[i:])) for i in range(1, len(words))]
        choices = [(a, b) for a, b in choices if max(_width(a), _width(b)) <= _LINE_CHARS]
        if choices and min(map(_width, lines)) < _LINE_CHARS / 2:
            lines = list(min(choices, key=lambda pair: abs(_width(pair[0]) - _width(pair[1]))))
    return ['\n'.join(lines[i:i + _LINES_PER_CUE]) for i in range(0, len(lines), _LINES_PER_CUE)]


def _spans(start: int, end: int, texts: list[str]) -> list[tuple[int, int, str]]:
    """Assign segment-relative time proportionally; respect a minimum where possible.

    Distributing text across segments is still an estimate. Suppress overly
    long holds but never extend a cue past the real segment end.
    """
    if not texts:
        return []
    available = end - start
    if len(texts) > available:
        # Preserve even an implausibly dense imported segment and flag it for review.
        texts = ['\n'.join(texts)]
    count = len(texts)
    minimum = _MIN_TARGET_MS if available >= count * _MIN_TARGET_MS else 1
    remaining = available - count * minimum
    weights = [max(1, len(value.replace('\n', ' '))) for value in texts]
    total = sum(weights)
    cursor = start
    result: list[tuple[int, int, str]] = []
    cumulative = 0
    for i, text in enumerate(texts):
        cumulative += weights[i]
        next_cursor = (end if i == count - 1 else
                       start + (i + 1) * minimum + round(remaining * cumulative / total))
        shown_end = min(next_cursor, cursor + _MAX_HOLD_MS)
        if shown_end > cursor:
            result.append((cursor, shown_end, text))
        cursor = next_cursor
    return result


@dataclass(frozen=True)
class CaptionResult:
    text: str
    count: int
    timing: str
    warnings: tuple[str, ...]


def _word_cues(words: list[TimedWord], start: int, end: int) -> list[tuple[int, int, str]]:
    # Choose phrase breaks together so the last few words aren't left as a
    # flash just because they wouldn't fit in the previous cue.
    costs = [0.] * (len(words) + 1)
    next_index = [0] * len(words)
    for i in range(len(words) - 1, -1, -1):
        best = float("inf")
        for j in range(i, min(len(words), i + 22)):
            if j > i and (words[j].start_ms - words[j - 1].end_ms > 450 or
                          words[j - 1].text.endswith(tuple(_TERMINATORS))):
                break
            left, right = max(start, words[i].start_ms), min(end, max(w.end_ms for w in words[i:j + 1]))
            span = right - left
            text = ' '.join(w.text for w in words[i:j + 1])
            if j > i and (span > 4300 or len(_chunks(text, _LINE_CHARS)) > _LINES_PER_CUE):
                break
            cps = _width(text) * 1000 / max(span, 1)
            penalty = max(0., (700 - span) / 350) + max(0., (cps - _TARGET_CPS) / 16)
            cost = 1 + penalty + costs[j + 1]
            if cost <= best:
                best, next_index[i] = cost, j + 1
        costs[i] = best
    cues = []
    i = 0
    while i < len(words):
        group = words[i:next_index[i]]
        a, b = max(start, group[0].start_ms), min(end, max(w.end_ms for w in group))
        cues.extend(_spans(a, b, _cue_texts(' '.join(w.text for w in group))))
        i = next_index[i]
    return cues


def _serialize_overlap(cues: list[tuple[int, int, str]]) -> tuple[list[tuple[int, int, str]], bool]:
    """Fit overlapping speakers into a serial timeline with at most two lines."""
    output: list[tuple[int, int, str]] = []
    overlapping = False
    group: list[tuple[int, int, str]] = []
    end = -1

    def flush():
        nonlocal overlapping
        if len(group) == 1:
            output.append(group[0])
        elif group:
            overlapping = True
            if len(group) == 2 and not any('\n' in cue[2] for cue in group) and group[0][:2] == group[1][:2]:
                output.append((group[0][0], end, group[0][2] + '\n' + group[1][2]))
            else:
                output.extend(_spans(group[0][0], end, [cue[2] for cue in group]))

    for cue in sorted(cues, key=lambda row: row[0]):
        if cue[0] >= end:
            flush()
            group = []
        group.append(cue)
        end = max(end, cue[1])
    flush()
    return output, overlapping


def build_captions(segments: list[TranscriptSegment], start_ms: int, end_ms: int, duration_ms: int) -> CaptionResult:
    checked_span(start_ms, end_ms, duration_ms)
    cues: list[tuple[int, int, str]] = []
    used_words = used_estimates = False
    warnings: set[str] = set()
    for seg in sorted(segments, key=lambda s: (s.start_ms, s.end_ms)):
        seg.validate(duration_ms)
        begin, finish = max(start_ms, seg.start_ms), min(end_ms, seg.end_ms)
        if finish <= begin or not _sanitize(seg.text):
            continue
        if seg.words:
            visible = [w for w in seg.words if w.start_ms < finish and w.end_ms > begin]
            if not visible:
                continue
            if any(w.confidence is not None and w.confidence < .45 for w in visible):
                warnings.add("Low-confidence words need listening review")
            if any(w.start_ms < begin or w.end_ms > finish for w in visible):
                warnings.add("A clip boundary intersects a word")
            cues.extend(_word_cues(visible, begin, finish))
            used_words = True
        else:
            if begin != seg.start_ms or finish != seg.end_ms:
                warnings.add("Clip boundary intersects a segment with estimated word positions")
            approximate = _estimated_visible_text(seg.text, seg.start_ms, seg.end_ms, begin, finish)
            cues.extend(_spans(begin, finish, _cue_texts(approximate)))
            used_estimates = True

    cues, overlaps = _serialize_overlap(cues)
    if overlaps:
        warnings.add("Overlapping dialogue was kept in two-line cues; check timing")
    if any(_width(t) * 1000 > (b - a) * _TARGET_CPS for a, b, t in cues):
        warnings.add("Fast dialogue exceeds the caption reading-speed target")
    if any(b - a < _MIN_USABLE_MS for a, b, _ in cues):
        warnings.add("Some cues are brief; review at playback speed")
    timing = ("mixed_word_and_segment_estimates" if used_words and used_estimates else
              "word_estimated" if used_words else "segment_estimated_not_word_aligned")
    lines: list[str] = []
    for index, (left, right, value) in enumerate(cues, 1):
        lines.extend([str(index), f'{_clock(left - start_ms)} --> {_clock(right - start_ms)}', value, ''])
    return CaptionResult('\n'.join(lines) + '\n' if lines else '', len(cues),
                         timing if cues else "unavailable", tuple(sorted(warnings)))


def make_srt(
    segments: list[TranscriptSegment], start_ms: int, end_ms: int, duration_ms: int,
) -> tuple[str, int]:
    result = build_captions(segments, start_ms, end_ms, duration_ms)
    return result.text, result.count
