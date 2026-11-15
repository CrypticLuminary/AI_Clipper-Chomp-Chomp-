"""Deterministic clip-local SRT for *segment-timed* transcripts.

Inferred within-segment positions are estimates, NEVER word-alignment. We
favor a readable, serial caption stream over fabricated precision. When an
ASR segment has more text than can be displayed intelligibly within its real
speech interval, text may be abbreviated with an ellipsis; we do not extend
captions into known silence or across the export boundary.
"""
from __future__ import annotations

import re
import unicodedata

from .models import TranscriptSegment, checked_span

# No renderer font/style changes here. Code-point limits are *heuristics*,
# not glyph widths: Devanagari combining marks and CJK glyphs vary visually.
_LINE_CHARS = 28
_LINES_PER_CUE = 2
_TARGET_CPS = 24          # maximum approximate codepoints per second
_MIN_TARGET_MS = 700      # never extend a cue beyond its known speech window
_MIN_USABLE_MS = 250      # skip fragments that can only flicker
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


def _shorten(text: str, limit: int) -> str:
    """Remove unsupported trailing text instead of displaying it too fast."""
    if len(text) <= limit:
        return text
    limit = max(2, limit)
    clusters = _graphemes(text)
    taken = []
    used = 0
    for part in clusters:
        if used + len(part) > limit - 1:
            break
        taken.append(part)
        used += len(part)
    prefix = ''.join(taken).rstrip()
    # Prefer stopping between words, but don't throw away most of a small cue.
    last_space = prefix.rfind(' ')
    if last_space >= len(prefix) // 2:
        prefix = prefix[:last_space]
    return prefix.rstrip('.,;: ') + '…'


def _split_token(word: str, maximum: int) -> list[str]:
    groups: list[str] = []
    current = ''
    for item in _graphemes(word):
        if current and len(current) + len(item) > maximum:
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
            if current and len(current) + 1 + len(piece) > maximum:
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
        chosen = chars[int(len(chars) * beginning):int(len(chars) * ending) or len(chars)]
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


def _cue_texts(clean: str, available_ms: int) -> list[str]:
    """Produce <=2-line cues without making unsupported split timestamps."""
    max_cues = max(1, available_ms // _MIN_TARGET_MS)
    # A deliberately approximate character/s budget. Impossible ASR text is
    # shortened, explicitly marked with an ellipsis, not flashed at 80 ms.
    limit = max(8, available_ms * _TARGET_CPS // 1000)
    clean = _shorten(clean, limit)
    for _ in range(len(clean) + 1):
        lines = _chunks(clean, _LINE_CHARS)
        groups = [lines[i:i + _LINES_PER_CUE] for i in range(0, len(lines), _LINES_PER_CUE)]
        if len(groups) <= max_cues:
            return ['\n'.join(group) for group in groups]
        clean = _shorten(clean, max(2, len(clean) - 2))
    return []


def _spans(start: int, end: int, texts: list[str]) -> list[tuple[int, int, str]]:
    """Assign segment-relative time proportionally; respect a minimum where possible.

    Distributing text across segments is still an estimate. Suppress overly
    long holds but never extend a cue past the real segment end.
    """
    count = len(texts)
    if not count:
        return []
    available = end - start
    minimum = _MIN_TARGET_MS if available >= count * _MIN_TARGET_MS else 0
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


def make_srt(
    segments: list[TranscriptSegment], start_ms: int, end_ms: int, duration_ms: int,
) -> tuple[str, int]:
    """Create serial, clip-local UTF-8 SRT cues; no assumed word alignment.

    Signature and tuple return shape are unchanged. Windows/Linux rendering
    callers can write the returned string directly as UTF-8. Overlapping
    speakers are serialized by source onset, with the later onset taking over
    the display (no stacked, competing cues). Exact-simultaneous onsets use
    stable input order. No caption is extended into an observed silence gap.
    """
    checked_span(start_ms, end_ms, duration_ms)
    indexed = []
    for index, seg in enumerate(segments):
        seg.validate(duration_ms)
        if seg.end_ms > start_ms and seg.start_ms < end_ms and _sanitize(seg.text):
            indexed.append((seg.start_ms, index, seg))
    indexed.sort(key=lambda entry: (entry[0], entry[1]))
    # At a simultaneous onset no timestamp evidence supports which speaker is
    # audible; keep one deterministic stream rather than two fighting cues.
    ordered = []
    for item in indexed:
        if not ordered or item[0] != ordered[-1][0]:
            ordered.append(item)
    cues: list[tuple[int, int, str]] = []
    for i, (_, _, seg) in enumerate(ordered):
        begin = max(start_ms, seg.start_ms)
        finish = min(end_ms, seg.end_ms)
        if i + 1 < len(ordered):
            finish = min(finish, ordered[i + 1][0])
        if finish - begin < _MIN_USABLE_MS:
            continue
        approximate = _estimated_visible_text(seg.text, seg.start_ms, seg.end_ms, begin, finish)
        if not approximate:
            continue
        texts = _cue_texts(approximate, finish - begin)
        for left, right, text in _spans(begin, finish, texts):
            if right - left >= _MIN_USABLE_MS:
                cues.append((left - start_ms, right - start_ms, text))
    lines: list[str] = []
    for index, (left, right, value) in enumerate(cues, 1):
        lines.extend([str(index), f'{_clock(left)} --> {_clock(right)}', value, ''])
    return ('\n'.join(lines) + '\n' if lines else ''), len(cues)
