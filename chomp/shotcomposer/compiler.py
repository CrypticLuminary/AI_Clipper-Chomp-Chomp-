"""Pure, bounded video graph compiler. No paths, subprocesses or model calls.

All views use subsets of one CFR source frame grid. A dissolve is the only
time two views process the same frames. Audio remains owned by media.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import math

from ..media import _crop_axis
from .contract import fraction, frame_index, geometry, pointer_blocks, project, validate


@dataclass(frozen=True)
class CompiledShots:
    graph: str
    report: dict


def _number(value) -> str:
    return f"{float(value):.9f}".rstrip("0").rstrip(".") or "0"


def _zoom(keys: list[dict], origin: int, easing: str) -> str:
    result = _number(keys[-1]["scale"])
    for left, right in reversed(list(zip(keys, keys[1:]))):
        a = _number(Fraction(left["at_ms"] - origin, 1000))
        length = _number(Fraction(right["at_ms"] - left["at_ms"], 1000))
        u = f"clip((it-{a})/{length},0,1)"
        if easing == "ease_in_out":
            u = f"({u})*({u})*(3-2*({u}))"
        scale = f"{_number(left['scale'])}+{_number(right['scale'] - left['scale'])}*({u})"
        stop = _number(Fraction(right["at_ms"] - origin, 1000))
        result = f"if(lt(it,{stop}),{scale},{result})"
    return result


def _stack_order(slots: list[dict], width: int, height: int) -> tuple[str, list[int]] | None:
    """Use a stack when opaque slots exactly tile one axis of the canvas."""
    for filter_name, axis, cross, length in (("vstack", 1, width, height), ("hstack", 0, height, width)):
        order = sorted(range(len(slots)), key=lambda i: slots[i]["rect"][axis])
        edge = 0
        for i in order:
            rect = slots[i]["rect"]
            if rect[1 - axis] or rect[3 - axis] != cross or rect[axis] != edge:
                break
            edge += rect[axis + 2]
        else:
            if edge == length:
                return filter_name, order
    return None


def _dissolve_end(shot: dict, origin: int, rate: Fraction) -> int:
    return frame_index(shot["start_ms"] + shot["transition"]["duration_ms"], origin, rate)


def compile_plan(plan: dict, evidence: dict) -> CompiledShots:
    result = validate(plan, evidence)
    if not result.renderable:
        codes = sorted({p["code"] for p in result.problems if p["severity"] in {"blocked", "needs_review"}})
        raise ValueError("ShotPlan cannot render: " + ", ".join(codes))
    source, canvas = evidence["source"], plan["canvas"]
    regions = {r["id"]: r for r in evidence["regions"]}
    cw, ch, rate, sar = canvas["width"], canvas["height"], fraction(canvas["fps"]), fraction(source["sar"])
    start = plan["start_ms"]
    shots = plan["shots"]
    count = len(shots)
    total = frame_index(plan["end_ms"], start, rate)
    clock = f"[0:v:0]fps={rate}:start_time=0,trim=end_frame={total}"
    graph = [clock + (f",split={count}" if count > 1 else "") + "".join(f"[scin{i}]" for i in range(count))]
    parts = []
    for i, shot in enumerate(shots):
        prefix = f"sc{i}"
        first = frame_index(shot["start_ms"], start, rate)
        end = frame_index(shot["end_ms"], start, rate)
        dissolve_end = _dissolve_end(shot, start, rate)
        view_end = _dissolve_end(shots[i + 1], start, rate) if i + 1 < count else end
        graph.append(f"[scin{i}]trim=start_frame={first}:end_frame={view_end}[{prefix}active]")
        slots = shot["slots"]
        stack = _stack_order(slots, cw, ch)
        from_first_slot = slots[0]["rect"] == [0, 0, cw, ch]
        matte = not (from_first_slot or stack)
        inputs = [f"[{prefix}src{j}]" for j in range(len(slots))]
        taps = [f"[{prefix}bg]", *inputs] if matte else inputs
        if len(taps) > 1:
            graph.append(f"[{prefix}active]split={len(taps)}" + "".join(taps))
        else:
            inputs[0] = f"[{prefix}active]"
        if matte:
            # A single source-derived pixel patch supplies the matte clock.
            graph.append(f"[{prefix}bg]crop=2:2:0:0,drawbox=c=0x101419:t=fill,"
                         f"scale={cw}:{ch}:flags=neighbor,setsar=1,format=yuv420p[{prefix}base]")
        tiles = []
        for j, slot in enumerate(slots):
            region = regions[slot["region_id"]]
            x, y, w, h = region["box"]
            if slot["crop_path"]:
                path = tuple(tuple(p) for p in slot["crop_path"])
                x, y = _crop_axis(path, start, 1), _crop_axis(path, start, 2)
            dx, dy, dw, dh = slot["rect"]
            correction = f"scale='max(2,trunc(iw*{sar}/2)*2)':ih,setsar=1," if sar != 1 else ""
            fit = "increase" if slot["fit"] == "cover" else "decrease"
            edge = f"crop={dw}:{dh}" if slot["fit"] == "cover" else f"pad={dw}:{dh}:(ow-iw)/2:(oh-ih)/2:color=0x101419"
            motion = ""
            if slot["zoom"]:
                zoom = _zoom(slot["zoom"], start, slot["easing"])
                # One output per input on the already normalized CFR grid.
                # Fixed link dimensions keep centering stable between zoom keys.
                motion = (f",format=yuv444p,zoompan=z='{zoom}':x='(iw-iw/zoom)/2':"
                          f"y='(ih-ih/zoom)/2':d=1:s={dw}x{dh}:fps={rate},setpts=PTS+{first},setsar=1")
            tile = f"[{prefix}tile{j}]"
            graph.append(f"{inputs[j]}crop={w}:{h}:{x}:{y}:exact=1,{correction}"
                         f"scale={dw}:{dh}:force_original_aspect_ratio={fit}:force_divisible_by=2,"
                         f"{edge},setsar=1{motion},format=yuv420p{tile}")
            tiles.append(tile)
        if stack and len(slots) > 1:
            name, order = stack
            previous = f"[{prefix}stack]"
            graph.append("".join(tiles[j] for j in order) + f"{name}=inputs={len(slots)}:shortest=1{previous}")
        else:
            previous = f"[{prefix}base]" if matte else tiles[0]
            for j in range(0 if matte else 1, len(slots)):
                dx, dy, _, _ = slots[j]["rect"]
                layer = f"[{prefix}layer{j}]"
                graph.append(f"{previous}{tiles[j]}overlay=x={dx}:y={dy}:shortest=1:eof_action=endall{layer}")
                previous = layer
        effects = []
        for graphic in shot["graphics"]:
            slot = next(s for s in slots if s["id"] == graphic["slot_id"])
            _, transform = geometry(slot, regions[slot["region_id"]], sar)
            box = project(regions[graphic["region_id"]]["box"], transform)
            x, y = math.ceil(box[0]), math.ceil(box[1])
            w, h = max(2, math.floor(box[2]) - 2), max(2, math.floor(box[3]) - 2)
            a = frame_index(graphic["start_ms"], start, rate) - first
            b = frame_index(graphic["end_ms"], start, rate) - first
            enabled = f"enable='gte(n,{a})*lt(n,{b})'"
            color = "0x" + graphic["color"][1:] + "@" + _number(graphic["opacity"])
            if graphic["kind"] == "outline":
                effects.append(f"drawbox=x={x}:y={y}:w={w}:h={h}:color={color}:t={graphic['thickness']}:{enabled}")
            elif graphic["kind"] == "spotlight":
                dx, dy, dw, dh = slot["rect"]
                masks = [(dx, dy, dw, y-dy), (dx, y+h, dw, dy+dh-y-h),
                         (dx, y, x-dx, h), (x+w, y, dx+dw-x-w, h)]
                for mx, my, mw, mh in masks:
                    if mw > 0 and mh > 0:
                        effects.append(f"drawbox=x={mx}:y={my}:w={mw}:h={mh}:color={color}:t=fill:{enabled}")
            else:
                for px, py, pw, ph in pointer_blocks(slot, graphic, shot, regions, sar, plan["caption_exclusions"]):
                    effects.append(f"drawbox=x={px}:y={py}:w={pw}:h={ph}:color={color}:t=fill:{enabled}")
        view = f"[scview{i}]"
        graph.append(previous + ",".join([*effects, "format=yuv420p"]) + view)
        windows = [(name, a, b) for name, a, b in
                   (("head", first, dissolve_end), ("body", dissolve_end, end), ("tail", end, view_end)) if a < b]
        if len(windows) > 1:
            graph.append(f"{view}split={len(windows)}" + "".join(f"[{prefix}{name}in]" for name, _, _ in windows))
        for name, a, b in windows:
            inp = f"[{prefix}{name}in]" if len(windows) > 1 else view
            trim = f"trim=start_frame={a-first}:end_frame={b-first}"
            if name == "body":
                part = f"[{prefix}body]"
                graph.append(f"{inp}{trim},setpts=PTS-STARTPTS{part}")
                parts.append(part)
            elif name == "tail":
                graph.append(f"{inp}{trim}[{prefix}tail]")
            else:
                graph.append(f"{inp}{trim}[{prefix}head]")
                # Compute opacity from advancing PTS. The two input windows
                # have identical frame timestamps throughout this blend.
                alpha = f"clip((round(T*({rate}))-{a})/{b-a},0,1)"
                part = f"[{prefix}blend]"
                graph.append(f"[sc{i-1}tail][{prefix}head]blend=all_expr='A*(1-({alpha}))+B*({alpha})':"
                             f"shortest=1,setpts=PTS-STARTPTS{part}")
                parts.append(part)
    # Each piece already has the exact frame count. Restore the rational grid
    # after concat, whose default time base rounds segment durations.
    assembled = "".join(parts)
    if len(parts) > 1:
        assembled += f"concat=n={len(parts)}:v=1:a=0,"
    graph.append(f"{assembled}settb=expr={rate.denominator}/{rate.numerator},setpts=N[stage]")
    text = ";".join(graph)
    if len(text) > 250_000:
        raise ValueError("Compiled filtergraph exceeds the FX03 complexity budget")
    report = result.as_dict()
    report["resource_estimate"]["filtergraph_characters"] = len(text)
    report["audio_policy"] = "existing_selected_source_track_continuous"
    return CompiledShots(text, report)
