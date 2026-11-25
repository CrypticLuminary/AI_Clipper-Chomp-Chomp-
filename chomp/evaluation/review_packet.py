"""Build a private, newly rendered review room with source-context comparisons."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
from html import escape
import json
from pathlib import Path
from urllib.parse import quote

from ..artifacts import digest_file, write_json, write_text
from ..director.production import export_selection
from ..media import SOURCE_CLOCK_AUDIO_FILTER, exec_checked
from ..signals import read_transcript
from .core import make_template, read_manifest
from .technical import inspect_export


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def _stamp(ms):
    seconds = ms / 1000
    return f"{int(seconds//3600):02d}:{int(seconds//60)%60:02d}:{seconds%60:06.3f}"


def render_page(packet: dict) -> str:
    """All evidence is escaped; no third-party assets or network services."""
    nav, cards = [], []
    for group in packet["groups"]:
        nav.append(f'<h3>{escape(group["label"])}</h3>')
        for clip in group["clips"]:
            cid = clip["review_id"]
            nav.append(f'<a href="#{escape(cid)}">{escape(cid)} <span>{_stamp(clip["start_ms"])}</span></a>')
            fields = []
            choices = {
                "decision": ("Decision", ("good", "bad", "uncertain")),
                "context_completeness": ("Context (1–5)", (1,2,3,4,5)),
                "payoff_completeness": ("Payoff (1–5)", (1,2,3,4,5)),
                "hook_quality": ("Hook (1–5)", (1,2,3,4,5)),
                "audio_correct": ("Audio correct?", ("yes","no","n/a")),
                "visual_correct": ("Framing correct?", ("yes","no","n/a")),
                "subtitle_readable": ("Captions readable?", ("yes","no","n/a")),
                "duplicate_content": ("Duplicate content?", ("yes","no","uncertain")),
                "failure_category": ("Failure, if bad", ("uninteresting","weak_hook","missing_setup","missing_payoff","wrong_timing","audio","framing","subtitles","duplicate","other")),
            }
            for field, (label, values) in choices.items():
                options = '<option value="">Unrated</option>' + ''.join(f'<option>{escape(str(value))}</option>' for value in values)
                fields.append(f'<label>{label}<select name="{field}">{options}</select></label>')
            evidence = ''.join(f'<li><time>{_stamp(row["start_ms"])}</time> {escape(row["text"])}</li>' for row in clip["utterances"])
            urls = {key: quote(clip[key], safe="/") for key in ("preview", "source_context", "poster", "comparison")}
            diagnostics = clip["diagnostics"]
            cards.append(f'''<article class="moment" id="{cid}" data-group="{group['id']}" data-index="{clip['template_index']}" data-offset="{clip['source_offset_ms']/1000}">
<div class="moment-title"><div><span class="eyebrow">{escape(group['label'])}</span><h2>{cid} <small>{_stamp(clip['start_ms'])} — {_stamp(clip['end_ms'])}</small></h2></div><span class="pill">Fresh render · review pending</span></div>
<div class="players"><div><div class="player-label">AUTHORIZED SOURCE · includes lead-in and follow-through</div><video class="source" controls preload="metadata" muted src="{urls['source_context']}"></video><p class="muted">Source player starts {_stamp(clip['context_start_ms'])}; the selected interval begins at +{clip['source_offset_ms']/1000:.3f}s.</p></div><div><div class="player-label">PORTRAIT EXPORT · original selected audio</div><video class="export" controls preload="none" poster="{urls['poster']}" src="{urls['preview']}"></video></div></div>
<div class="transport"><button data-action="play">Play together</button><button data-action="pause" class="secondary">Pause</button><button data-action="align" class="secondary">Re-align source</button><label class="inline">Listen to <select class="listen"><option value="export">export</option><option value="source">source</option></select></label><a class="text-link" href="{urls['preview']}" download>Download MP4</a></div>
<details><summary>Inspect source comparison, transcript and checks</summary><img class="comparison" loading="lazy" src="{urls['comparison']}" alt="Source frame next to newly encoded portrait frame"><p>{escape(clip['reason'])}</p><p class="muted">Decoded output: {diagnostics['width']}×{diagnostics['height']} · {diagnostics['output_dbfs']:.1f} dBFS · full decode passed. Audio-envelope lag: {diagnostics.get('audio_envelope_lag_ms')} ms. Actual A/V synchronization and contextual completeness are unmeasured.</p><p class="muted">Transcript is machine-produced evidence, not verified dialogue. Check the source before judging missing context.</p><ol class="transcript">{evidence}</ol></details>
<form class="rating"><div class="rating-heading"><h3>Your review</h3><span class="muted">Nothing is pre-approved</span></div><div class="checks"><label><input type="checkbox" name="watched_source"> I watched the source context</label><label><input type="checkbox" name="watched_export"> I watched and listened to the export</label></div><div class="fields">{''.join(fields)}</div><label>Notes<textarea name="notes" rows="2" placeholder="Missing setup, crop issues, caption errors, or anything worth checking…"></textarea></label></form></article>''')
    groups = json.dumps({g["id"]: g["review_template"] for g in packet["groups"]}, ensure_ascii=False).replace("<", "\\u003c")
    buttons = ''.join(f'<button class="secondary" data-download="{g["id"]}">Download {escape(g["id"])} review JSON</button>' for g in packet["groups"])
    html = '''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Chomp-Chomp · R1 Review Room</title><style>
:root{color-scheme:dark;--bg:#0b1015;--panel:#141c24;--line:#2a3743;--text:#e9f0f4;--muted:#a8bac6;--accent:#a8e27c}*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:var(--bg);font:15px/1.55 'Segoe UI',system-ui,sans-serif;color:var(--text)}a{color:var(--accent)}button,select,input,textarea{font:inherit}button{border:0;background:var(--accent);color:#152414;padding:.65rem 1rem;border-radius:7px;cursor:pointer;font-weight:650}.secondary,select,input,textarea{background:#1b2732;color:var(--text);border:1px solid var(--line);border-radius:7px;padding:.55rem .7rem}.secondary{font-weight:500}button:focus-visible,a:focus-visible,select:focus-visible,input:focus-visible,textarea:focus-visible{outline:2px solid var(--accent);outline-offset:3px}aside{position:fixed;inset:0 auto 0 0;width:238px;border-right:1px solid var(--line);padding:30px 22px;background:#10171e;overflow:auto}.brand{font-size:18px;letter-spacing:-.5px;font-weight:800}.brand b{color:var(--accent)}aside h3{font-size:11px;text-transform:uppercase;letter-spacing:1.2px;color:var(--muted);margin:30px 0 8px}aside a{display:flex;justify-content:space-between;text-decoration:none;padding:9px 0;border-bottom:1px solid #1b2732;color:var(--text)}aside a span{font-size:12px;color:var(--muted)}aside p{font-size:12px;color:var(--muted);margin-top:35px}main{margin-left:238px;max-width:1530px;padding:40px 42px 80px}.eyebrow{color:var(--accent);letter-spacing:1.7px;font-size:11px;font-weight:700;text-transform:uppercase}h1{font-size:clamp(30px,4vw,48px);line-height:1.12;letter-spacing:-1.7px;margin:12px 0}h2{font-size:24px;letter-spacing:-.5px;margin:6px 0}h2 small{font-size:13px;font-weight:400;color:var(--muted);letter-spacing:0;margin-left:12px}h3{font-size:16px}.intro{max-width:850px;color:var(--muted)}.stats{display:flex;gap:12px;flex-wrap:wrap;margin:26px 0}.stat{border:1px solid var(--line);padding:12px 20px;border-radius:9px;color:var(--muted)}.stat b{color:var(--text);font-size:24px;margin-right:10px}.reviewer{display:flex;align-items:center;gap:12px;margin-bottom:25px}.reviewer input{width:min(360px,65vw)}.moment{background:var(--panel);border:1px solid var(--line);border-radius:12px;margin:24px 0;overflow:hidden;scroll-margin-top:15px}.moment-title{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:20px 23px;border-bottom:1px solid var(--line)}.pill{font-size:11px;color:#c6dfb5;background:#253a26;border-radius:20px;padding:6px 12px;white-space:nowrap}.players{display:grid;grid-template-columns:minmax(0,1.8fr) minmax(180px,.8fr);gap:20px;padding:20px 23px;background:#0e151c;align-items:start}.player-label{font-size:10px;letter-spacing:1px;color:var(--muted);margin-bottom:10px}video{display:block;width:100%;border-radius:6px;background:#050709}.source{aspect-ratio:16/9}.export{aspect-ratio:9/16;max-height:520px}.transport{padding:15px 23px;display:flex;align-items:center;gap:10px;flex-wrap:wrap;border-bottom:1px solid var(--line)}.inline{display:flex;align-items:center;gap:8px;font-size:13px}.text-link{font-size:13px;margin-left:auto}details{padding:15px 23px;border-bottom:1px solid var(--line)}summary{cursor:pointer;color:var(--muted)}.comparison{width:100%;max-width:1050px;margin-top:18px;border-radius:7px}.muted{color:var(--muted);font-size:12px}.transcript{font-size:13px;padding-left:25px;max-height:240px;overflow:auto}.transcript time{color:var(--accent);font-variant-numeric:tabular-nums}.rating{padding:10px 23px 23px}.rating-heading{display:flex;align-items:center;justify-content:space-between}.checks{display:flex;gap:20px;flex-wrap:wrap;font-size:13px;margin:4px 0 16px}.fields{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.fields label,.rating>label{display:grid;gap:5px;color:var(--muted);font-size:12px}.rating>label{margin-top:15px}textarea{width:100%;resize:vertical}.downloads{display:flex;gap:10px;flex-wrap:wrap;margin-top:20px}.notice{border-left:3px solid var(--accent);padding:8px 17px;background:#152019;color:#c2d4c4;font-size:13px}#status{font-size:13px;color:var(--accent);min-height:20px}@media(max-width:1050px){aside{display:none}main{margin:0;padding:25px}.fields{grid-template-columns:repeat(2,minmax(0,1fr))}.moment-title{align-items:start;flex-direction:column}}@media(max-width:650px){main{padding:16px}.players{grid-template-columns:1fr;gap:20px}.export{max-height:440px}.moment-title,.players,.transport,details,.rating{padding-left:15px;padding-right:15px}h2 small{display:block;margin:4px 0}.fields{grid-template-columns:1fr 1fr}.text-link{margin-left:0}}
</style></head><body><aside><div class="brand">chomp<b> / </b>review</div><span class="eyebrow">BASE-10-R1 · local only</span>__NAV__<p>Private authorized media. This page uses no external scripts, analytics, model calls or publishing services.</p><a href="packet.json">Generation record ↗</a></aside><main><span class="eyebrow">Human review room / fresh exports</span><h1>Watch the moment.<br>Check the whole story.</h1><p class="intro">Three newly discovered moments, followed by fresh renders of the three earlier selections. Compare each portrait export with the authorized source, inspect context, then record your own judgment.</p><div class="stats"><div class="stat"><b>3</b>new moments</div><div class="stat"><b>3</b>earlier selections re-rendered</div><div class="stat"><b>0</b>cached MP4s reused</div></div><p class="notice">These corrections improve budget allocation, evaluation and cache verification. They do not establish semantic completeness or better visual editing. The new moments use fresh local Whisper and heuristic discovery. Earlier selections keep their previous editorial decisions. Every video on this page was encoded in this run.</p><div class="reviewer"><label for="reviewer">Reviewer</label><input id="reviewer" autocomplete="name" placeholder="Your name or reviewer ID"></div><div id="status" role="status"></div>__CARDS__<h3>Save your independent review</h3><p class="muted">Downloads are native ClipLedger annotations for each source run. Nothing is submitted, published or approved automatically. Unrated entries remain null. Keep the files private.</p><div class="downloads">__DOWNLOADS__</div></main><script>
const templates=__TEMPLATES__;const storageKey='chomp-r1-'+__PACKET_ID__;
const articles=[...document.querySelectorAll('.moment')];const message=s=>document.querySelector('#status').textContent=s;
for(const card of articles){const src=card.querySelector('.source'),out=card.querySelector('.export'),offset=Number(card.dataset.offset);let paired=false;
const align=()=>{src.currentTime=out.currentTime+offset};const audio=()=>{const source=card.querySelector('.listen').value==='source';src.muted=!source;out.muted=source};
const ready=v=>v.readyState>=2?Promise.resolve():new Promise((resolve,reject)=>{v.addEventListener('loadeddata',resolve,{once:true});v.addEventListener('error',reject,{once:true});v.load()});
card.querySelector('.listen').addEventListener('change',audio);out.addEventListener('seeked',()=>{if(!src.paused)align()});
for(const b of card.querySelectorAll('[data-action]'))b.onclick=async()=>{if(b.dataset.action==='play'){for(const other of articles)for(const v of other.querySelectorAll('video'))v.pause();try{await Promise.all([ready(src),ready(out)]);align();audio();paired=true;await Promise.all([src.play(),out.play()])}catch(e){paired=false;message('Playback could not start. Try the native player controls or download the MP4.')}}else if(b.dataset.action==='pause'){paired=false;src.pause();out.pause()}else align()};
out.addEventListener('timeupdate',()=>{if(paired&&!out.paused&&!src.paused&&Math.abs(src.currentTime-out.currentTime-offset)>.25)align()});
out.addEventListener('ended',()=>{paired=false;src.pause()});card.querySelector('form').addEventListener('submit',e=>e.preventDefault());}
function snapshot(){return{reviewer:document.querySelector('#reviewer').value,forms:articles.map(a=>Object.fromEntries([...a.querySelectorAll('form [name]')].map(x=>[x.name,x.type==='checkbox'?x.checked:x.value])))}}
function save(){try{localStorage.setItem(storageKey,JSON.stringify(snapshot()))}catch(e){message('Browser storage unavailable. Download your reviews before closing this page.')}}
try{const saved=JSON.parse(localStorage.getItem(storageKey)||'null');if(saved){document.querySelector('#reviewer').value=saved.reviewer||'';articles.forEach((a,i)=>{for(const el of a.querySelectorAll('form [name]')){const value=saved.forms?.[i]?.[el.name];if(el.type==='checkbox')el.checked=Boolean(value);else el.value=value||''}})}}catch(e){}
document.addEventListener('change',save);document.addEventListener('input',save);
for(const button of document.querySelectorAll('[data-download]'))button.onclick=()=>{const group=button.dataset.download;const result=structuredClone(templates[group]);const reviewer=document.querySelector('#reviewer').value.trim();
for(const a of articles.filter(a=>a.dataset.group===group)){const form=a.querySelector('form'),row=result.clips[Number(a.dataset.index)],decision=form.elements.decision.value;
if(decision&&(!reviewer||!form.elements.watched_source.checked||!form.elements.watched_export.checked)){message('Add your reviewer ID and confirm source/export playback before rating '+a.id);a.scrollIntoView();return}
if(decision==='bad'&&!form.elements.failure_category.value){message('Choose the failure category for '+a.id);return}
for(const field of ['decision','audio_correct','visual_correct','subtitle_readable','duplicate_content','failure_category'])row[field]=form.elements[field].value||null;
for(const field of ['hook_quality','context_completeness','payoff_completeness'])row[field]=form.elements[field].value?Number(form.elements[field].value):null;
row.reviewer_id=decision?reviewer:'';row.notes=form.elements.notes.value;}
const blob=new Blob([JSON.stringify(result,null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download=group+'.reviews.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);message('Downloaded '+group+' review. No approval or publication was performed.');};
</script></body></html>'''
    new_count = sum(len(g["clips"]) for g in packet["groups"] if g["id"] == "new")
    earlier_count = sum(len(g["clips"]) for g in packet["groups"] if g["id"] != "new")
    html = html.replace("Three newly discovered moments, followed by fresh renders of the three earlier selections.",
                        f"{new_count} newly discovered moments, followed by fresh renders of {earlier_count} earlier selections.")
    html = html.replace("<b>3</b>new moments", f"<b>{new_count}</b>new moments").replace(
                        "<b>3</b>earlier selections", f"<b>{earlier_count}</b>earlier selections")
    return html.replace("__NAV__", ''.join(nav)).replace("__CARDS__", ''.join(cards)).replace("__DOWNLOADS__", buttons).replace("__TEMPLATES__", groups).replace("__PACKET_ID__", json.dumps(packet["packet_id"]))


def build_packet(existing_index: Path, new_report: Path, out: Path, *, revision: str) -> dict:
    if out.exists():
        raise FileExistsError("Choose a new private review directory; cached exports are never copied/reused")
    previous = _read(existing_index)
    new = _read(new_report)
    if new.get("audio_cache_hit") is not False or new.get("provider", {}).get("provider") != "heuristic":
        raise ValueError("This R1 packet requires fresh local heuristic discovery, without an audio-analysis cache hit")
    if new["source_id"] != previous["source_id"] or new["campaign"]["rights_ref"] != previous["rights_reference"]:
        raise ValueError("New review source/authorization differs from the private evaluation")
    out = out.resolve()
    out.mkdir(parents=True)
    packet = {"schema_version": "chomp-private-review-packet/1", "generated_at": datetime.now(timezone.utc).isoformat(),
        "revision": revision, "source_id": previous["source_id"], "rights_reference": previous["rights_reference"],
        "cached_mp4s_reused": 0, "new_paid_provider_calls": 0, "human_review_status": "pending", "groups": []}
    work = [("new", "Newly discovered moments", new_report)] + [
        (f"earlier-{i+1}", "Earlier selections · freshly re-rendered", Path(row["director_report"]))
        for i,row in enumerate(previous["variants"])]
    for group_id, label, report_path in work:
        report = _read(report_path)
        if report["source_id"] != previous["source_id"] or report["campaign"]["rights_ref"] != previous["rights_reference"]:
            raise ValueError("Existing review source/authorization mismatch")
        started = datetime.now(timezone.utc).isoformat()
        manifest = export_selection(report_path, out / group_id, acknowledge_permission=True, layout="shorts")
        if manifest.get("reused_job") or any(c.get("reused_job") for c in manifest["clips"]):
            raise ValueError("Review packet must contain freshly encoded media")
        template_path = out / f"{group_id}.reviews.json"
        make_template(read_manifest(manifest["review_manifest"]), template_path)
        transcript = read_transcript(Path(report["transcript_snapshot"]), report["duration_ms"])
        group = {"id": group_id, "label": label, "manifest": manifest["review_manifest"],
                 "selection_provenance": "fresh_local_heuristic" if group_id == "new" else "existing_editorial_decision",
                 "review_template": _read(template_path), "render_started_at": started, "clips": []}
        assets = out / "comparisons" / group_id
        assets.mkdir(parents=True)
        for i, clip in enumerate(manifest["clips"]):
            left, right = clip["start_ms"], clip["end_ms"]
            context_start, context_end = max(0,left-10000), min(report["duration_ms"],right+10000)
            source, preview = Path(report["source"]), Path(clip["preview"])
            context, poster, comparison = (assets / f"{i+1:02d}-{suffix}" for suffix in ("source.mp4","poster.png","comparison.png"))
            exec_checked(["ffmpeg","-hide_banner","-nostdin","-v","error","-y","-ss",f"{context_start/1000:.3f}","-i",str(source),
                "-t",f"{(context_end-context_start)/1000:.3f}","-map","0:v:0","-map",f"0:a:{report['audio_stream']}",
                "-vf","scale=960:540:force_original_aspect_ratio=decrease,pad=960:540:(ow-iw)/2:(oh-ih)/2,setsar=1",
                "-af",SOURCE_CLOCK_AUDIO_FILTER+",apad","-c:v","libx264","-preset","veryfast","-crf","24","-c:a","aac",
                "-movflags","+faststart",str(context)], timeout=300)
            midpoint = (right-left)/2000
            exec_checked(["ffmpeg","-hide_banner","-nostdin","-v","error","-y","-ss",f"{midpoint:.3f}","-i",str(preview),
                          "-frames:v","1",str(poster)],timeout=30)
            exec_checked(["ffmpeg","-hide_banner","-nostdin","-v","error","-y","-ss",f"{(left+right)/2000:.3f}","-i",str(source),
                "-ss",f"{midpoint:.3f}","-i",str(preview),"-filter_complex",
                "[0:v]scale=640:360,pad=640:640:0:140[left];[1:v]scale=360:640[right];[left][right]hstack=2[out]",
                "-map","[out]","-frames:v","1",str(comparison)],timeout=30)
            diagnostics = inspect_export(source, preview, start_ms=left, end_ms=right, audio_stream=report["audio_stream"])
            review_id = f"N{i+1:02d}" if group_id == "new" else f"E{len([c for g in packet['groups'] if g['id']!='new' for c in g['clips']])+i+1:02d}"
            group["clips"].append({"review_id":review_id,"template_index":i,"candidate_id":clip["candidate_id"],
                "start_ms":left,"end_ms":right,"context_start_ms":context_start,"source_offset_ms":left-context_start,
                "preview":preview.relative_to(out).as_posix(),"source_context":context.relative_to(out).as_posix(),
                "poster":poster.relative_to(out).as_posix(),"comparison":comparison.relative_to(out).as_posix(),
                "artifact_sha256":clip["artifact_sha256"],"source_context_sha256":digest_file(context),"diagnostics":diagnostics,
                "reason":clip["editorial"]["reason"],"utterances":[{"start_ms":s.start_ms,"end_ms":s.end_ms,"text":s.text}
                    for s in transcript if s.start_ms<context_end and s.end_ms>context_start]})
        group["render_finished_at"] = datetime.now(timezone.utc).isoformat()
        packet["groups"].append(group)
    packet["packet_id"] = sha256(json.dumps([packet["generated_at"],packet["source_id"],revision]).encode()).hexdigest()[:20]
    write_json(out / "packet.json", packet)
    write_text(out / "index.html", render_page(packet))
    return packet


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--existing-index",type=Path,required=True)
    parser.add_argument("--new-report",type=Path,required=True)
    parser.add_argument("--out",type=Path,required=True)
    parser.add_argument("--revision",required=True)
    args=parser.parse_args(argv)
    packet=build_packet(args.existing_index,args.new_report,args.out,revision=args.revision)
    print(json.dumps({"review_page":str(args.out.resolve()/"index.html"),"clips":sum(len(g["clips"]) for g in packet["groups"]),
                      "cached_mp4s_reused":packet["cached_mp4s_reused"]}))


if __name__=="__main__":
    main()
