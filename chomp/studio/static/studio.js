"use strict";

const by = id => document.getElementById(id);
const input = id => by(id).value.trim();
const number = (id, fallback = null) => input(id) === "" ? fallback : Number(input(id));
const ms = id => number(id) === null ? null : Math.round(1000 * number(id));
const terminal = new Set(["completed", "partial", "failed", "cancelled"]);
const state = { token: "", presets: {}, job: null, rendered: null, edit: null, upload: new Map() };
const names = {resolving_source:"Resolving source", acquiring_media:"Acquiring media", processing_source:"Checking source",
  reading_transcript:"Reading transcript", optimizing_boundaries:"Optimizing boundaries", preparing_review:"Preparing review"};
const pretty = value => names[value] || String(value || "").replaceAll("_", " ").replace(/^\w/, c => c.toUpperCase());
const sec = value => Number.isFinite(value) ? (value/1000).toFixed(3).replace(/\.?0+$/, "") : "—";

function el(kind, text, css) {
  const node = document.createElement(kind);
  if (text !== undefined) node.textContent = text;
  if (css) node.className = css;
  return node;
}

function announce(message, failed = false, target = "form-message") {
  by(target).textContent = message;
  by(target).classList.toggle("error", failed);
}

async function request(path, options = {}) {
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error?.message || "This operation could not complete.");
  return data;
}

function post(path, data) {
  return request(path, {method:"POST", headers:{"Content-Type":"application/json", "X-Chomp-Local":state.token},
    body:JSON.stringify(data)});
}

async function upload(file) {
  if (!file) return null;
  if (state.upload.has(file)) return state.upload.get(file);
  const result = await request("/api/uploads", {method:"POST", headers:{"Content-Type":"application/octet-stream",
    "X-Filename":encodeURIComponent(file.name), "X-Chomp-Local":state.token}, body:file});
  state.upload.set(file, result.id);
  return result.id;
}

function button(text, action) {
  const node = el("button", text, "secondary");
  node.type = "button";
  node.addEventListener("click", async () => {
    node.disabled = true;
    try { await action(); } catch (error) { announce(error.message, true, "result-note"); }
    finally { node.disabled = false; }
  });
  return node;
}

function link(text, url) {
  const node = el("a", text, "secondary");
  node.href = url;
  return node;
}

async function inspectSource() {
  if (!input("source-url")) return announce("Enter a Twitch or YouTube URL.", true);
  const area = by("source-info");
  area.hidden = false;
  area.replaceChildren(el("p", "Resolving source…"));
  try {
    const result = await post("/api/source", {source:input("source-url")});
    const s = result.source;
    const summary = el("div", undefined, "source-summary");
    if (s.thumbnail) {
      const img = el("img");
      img.src = s.thumbnail.replace("{width}", "320").replace("{height}", "180");
      img.alt = "";
      summary.append(img);
    }
    const meta = el("div");
    meta.append(el("strong", s.title || s.canonical_url || pretty(s.platform)));
    meta.append(el("p", [s.channel?.title || s.channel?.login, pretty(s.live_status),
      s.duration_ms ? sec(s.duration_ms) + " s" : null].filter(Boolean).join(" · ")));
    summary.append(meta);
    area.replaceChildren(summary, el("p", "Editing media: attach a creator original or feed."));
    if (result.metadata_error) area.append(el("p", result.metadata_error.message));
    if (result.embed) {
      const show = button("Open source player", () => {
        const frame = el("iframe");
        frame.src = result.embed.url;
        frame.title = "Original platform player";
        frame.allow = "fullscreen; encrypted-media; picture-in-picture";
        frame.allowFullscreen = true;
        show.replaceWith(frame);
      });
      area.append(show);
    }
  } catch (error) { area.replaceChildren(el("p", error.message, "error")); }
}

function requirements() {
  return {clips:number("clip-count"), mode:input("count-mode"), min_ms:ms("duration-min"), max_ms:ms("duration-max"),
    preferred_ms:ms("duration-pref"), campaign_min_ms:ms("campaign-min"), campaign_max_ms:ms("campaign-max"),
    aspect:input("aspect"), resolution:number("resolution"), layout:input("layout"), captions:by("captions").checked,
    audio_profile:input("audio"), audio_stream:number("audio-track", 0), hardware:input("hardware"),
    instructions:input("instructions"), reasoning_calls:number("reasoning-calls"),
    source_start_ms:ms("source-start"), source_end_ms:ms("source-end")};
}

async function submit(event) {
  event.preventDefault();
  const start = by("start-job");
  start.disabled = true;
  const kind = document.querySelector("input[name=source-mode]:checked").value;
  try {
    const control = requirements();
    await post("/api/requirements", control);
    announce(kind === "recording" ? "Preparing the recording…" : "Starting live capture…");
    const original = kind === "recording" ? await upload(by("video-file").files[0]) : null;
    const transcript = kind === "recording" ? await upload(by("transcript-file").files[0]) : null;
    const result = await post("/api/jobs", {kind, source:input("source-url") || null,
      media_url:kind === "recording" ? input("media-url") || null : null, upload:original, transcript_upload:transcript,
      feed_url:kind === "live" ? input("feed-url") || null : null, live_seconds:number("live-seconds", 300),
      rights_ref:input("rights-ref"), confirmed:by("permission").checked,
      asr_device:input("asr-device"), provider_model:input("provider-model") || null,
      provider_confirmed:by("provider-confirmed").checked, requirements:control,
      twitch_download:kind === "recording" && by("twitch-download").checked});
    announce("Job submitted.");
    await selectJob(result.id);
    by("activity").scrollIntoView({behavior:"smooth",block:"start"});
  } catch (error) { announce(error.message, true); }
  finally { start.disabled = false; }
}

async function jobs() {
  const data = await request("/api/jobs");
  by("jobs").replaceChildren(...data.jobs.filter(j => j.kind !== "revision").slice(0, 10).map(j => {
    const node = button(j.label, () => selectJob(j.id));
    node.append(el("small", pretty(j.status)));
    node.setAttribute("aria-current", String(j.id === state.job));
    return node;
  }));
}

async function selectJob(id) {
  state.job = id;
  state.rendered = null;
  by("clip-list").replaceChildren();
  by("download-all").hidden = true;
  by("result-note").textContent = "Clips will appear here after rendering.";
  await refresh();
  await jobs();
}

async function refresh() {
  if (!state.job) return;
  const id = state.job;
  const data = await request("/api/jobs/" + id);
  if (id !== state.job) return;
  const details = data.progress || {};
  let message = data.error?.message || [
    details.candidates !== undefined ? details.candidates + " candidates" : null,
    details.investigated !== undefined ? details.investigated + " investigated" : null,
    details.ingested_chunks !== undefined ? details.ingested_chunks + " chunks" : null,
    details.bytes_received ? (details.bytes_received / (1024*1024)).toFixed(1) + " MiB received" : null,
    (details.produced ?? data.produced) !== undefined ? (details.produced ?? data.produced) + " clips produced" : null,
  ].filter(Boolean).join(" · ");
  if (!message) message = data.label;
  by("status").replaceChildren(el("strong", pretty(data.stage)), el("p", message, data.error ? "error" : ""));
  by("cancel-job").hidden = terminal.has(data.status);
  by("retry-job").hidden = !terminal.has(data.status);
  by("events").replaceChildren(...data.events.slice(-8).map(event => el("li", pretty(event.stage))));
  if (terminal.has(data.status) && state.rendered !== id) {
    state.rendered = id;
    if (data.status === "completed" || data.status === "partial") await renderClips(id);
    else announce(data.error?.message || "This job has no completed exports.", Boolean(data.error), "result-note");
    await jobs();
  }
}

async function renderClips(id) {
  const data = await request("/api/jobs/" + id + "/clips");
  if (state.job !== id) return;
  by("download-all").hidden = !data.clips.some(c => c.has_video);
  by("download-all").href = "/api/jobs/" + id + "/zip";
  const suffix = data.count_result?.shortfall ? " · Shortfall " + data.count_result.shortfall + ". " + data.count_result.explanation : "";
  announce(data.clips.length ? data.clips.length + " clips ready for review" + suffix :
    "No candidates met the requirements." + suffix, false, "result-note");
  by("clip-list").replaceChildren(...data.clips.map(c => clipCard(id, c)));
}

function clipCard(id, clip) {
  const card = el("article", undefined, "clip-card");
  const root = "/api/jobs/" + id + "/clips/" + clip.index + "/";
  if (clip.has_video) {
    const video = el("video");
    video.controls = true;
    video.preload = "metadata";
    video.playsInline = true;
    video.src = root + "play" + (clip.revision ? "?revision=" + clip.revision : "");
    video.setAttribute("aria-label", clip.title || "Clip " + clip.index);
    card.append(video);
  }
  const text = el("div");
  text.append(el("h3", clip.title || "Clip " + clip.index));
  text.append(el("p", sec(clip.start_ms) + "–" + sec(clip.end_ms) + " s · " +
    sec(clip.media_duration_ms ?? clip.end_ms - clip.start_ms) + " s" +
    (clip.width && clip.height ? " · " + clip.width + "×" + clip.height : "")));
  const label = clip.revision ? "Manual revision · review again" :
    clip.ranking_method === "evolution_director_verified" ? "Model-verified · human review pending" : "Local review";
  text.append(el("p", label, "method"));
  if (clip.description) text.append(el("p", clip.description.slice(0, 340)));
  if (clip.reason) text.append(el("p", clip.reason));
  if (clip.warnings?.length) text.append(el("p", clip.warnings.join(" "), "warning"));
  const actions = el("div", undefined, "clip-actions");
  if (clip.has_video) actions.append(link("Download MP4", root + "download"));
  actions.append(button("Trim and reframe", () => editClip(id, clip)));
  for (const [text, decision] of [["Approve","approve"],["Reject","reject"]]) {
    actions.append(button(text, async () => {
      await post(root + "feedback", {decision});
      await renderClips(id);
    }));
  }
  text.append(actions);
  if (clip.feedback) text.append(el("p", "Saved feedback · " + pretty(clip.feedback.decision), "feedback-note"));
  card.append(text);
  return card;
}

const canvas = by("frame-canvas");

function editClip(id, clip) {
  state.edit = {id, clip, image:null, start:null, rects:{}};
  by("edit-start").value = clip.start_ms / 1000;
  by("edit-end").value = clip.end_ms / 1000;
  canvas.hidden = true;
  announce("", false, "edit-error");
  announce("Drag on the frame to mark a region.", false, "region-note");
  by("edit-dialog").showModal();
}

async function loadFrame() {
  const edit = state.edit;
  by("load-frame").disabled = true;
  try {
    const response = await fetch("/api/jobs/" + edit.id + "/clips/" + edit.clip.index + "/frame");
    if (!response.ok) throw new Error("The original source frame is unavailable.");
    edit.width = Number(response.headers.get("X-Source-Width"));
    edit.height = Number(response.headers.get("X-Source-Height"));
    const blob = URL.createObjectURL(await response.blob());
    try {
      const img = new Image();
      img.src = blob;
      await img.decode();
      edit.image = img;
      canvas.width = img.naturalWidth;
      canvas.height = img.naturalHeight;
      canvas.hidden = false;
      paint();
    } finally { URL.revokeObjectURL(blob); }
  } catch (error) { announce(error.message, true, "edit-error"); }
  finally { by("load-frame").disabled = false; }
}

function point(event) {
  const box = canvas.getBoundingClientRect();
  return [(event.clientX-box.left)/box.width,(event.clientY-box.top)/box.height].map(x => Math.max(0,Math.min(1,x)));
}

function paint(current) {
  const ctx = canvas.getContext("2d");
  const edit = state.edit;
  if (!edit?.image) return;
  ctx.drawImage(edit.image, 0, 0);
  const rect = current && edit.start ? {
    [input("region-type")]: [edit.start[0],edit.start[1],current[0]-edit.start[0],current[1]-edit.start[1]]
  } : {};
  for (const [name,bounds] of Object.entries({...edit.rects,...rect})) {
    ctx.strokeStyle = name === "facecam_roi" ? "#d9fa73" : "#a5b9ff";
    ctx.lineWidth = 3;
    ctx.strokeRect(bounds[0]*canvas.width,bounds[1]*canvas.height,bounds[2]*canvas.width,bounds[3]*canvas.height);
  }
}

canvas.addEventListener("pointerdown", event => {
  state.edit.start = point(event);
  canvas.setPointerCapture(event.pointerId);
});
canvas.addEventListener("pointermove", event => paint(point(event)));
canvas.addEventListener("pointercancel", () => { if (state.edit) state.edit.start = null; paint(); });
canvas.addEventListener("pointerup", event => {
  const edit = state.edit;
  if (!edit?.start) return;
  const [x,y] = point(event), [ox,oy] = edit.start;
  if (Math.abs(x-ox) > .02 && Math.abs(y-oy) > .02) {
    edit.rects[input("region-type")] = [Math.min(x,ox),Math.min(y,oy),Math.abs(x-ox),Math.abs(y-oy)];
    announce(Object.keys(edit.rects).map(key => key === "facecam_roi" ? "Streamer region" : "Gameplay region").join(" · ") + " selected.",
      false, "region-note");
  }
  edit.start = null;
  paint();
});

by("edit-form").addEventListener("submit", async event => {
  event.preventDefault();
  const edit = state.edit;
  by("revise").disabled = true;
  try {
    const payload = {start_ms:ms("edit-start"), end_ms:ms("edit-end")};
    for (const [name, bounds] of Object.entries(edit.rects)) {
      payload[name] = bounds.map((x,i) => Math.floor(x * (i % 2 === 0 ? edit.width : edit.height)));
    }
    if (payload.facecam_roi) payload.layout = "facecam-gameplay";
    else if (payload.gameplay_roi) payload.layout = "gameplay-only";
    const revision = await post("/api/jobs/" + edit.id + "/clips/" + edit.clip.index + "/revise", payload);
    by("edit-dialog").close();
    announce("Rerender queued.", false, "result-note");
    const watch = setInterval(async () => {
      try {
        const job = await request("/api/jobs/" + revision.id);
        if (terminal.has(job.status)) {
          clearInterval(watch);
          if (job.status === "completed") await renderClips(edit.id);
          else announce(job.error?.message || "Rerender stopped.", true, "result-note");
        }
      } catch (error) { clearInterval(watch); announce(error.message, true, "result-note"); }
    }, 800);
  } catch (error) { announce(error.message, true, "edit-error"); }
  finally { by("revise").disabled = false; }
});

by("job-form").addEventListener("submit", submit);
by("inspect-button").addEventListener("click", inspectSource);
by("video-file").addEventListener("change", () => { by("video-name").textContent = by("video-file").files[0]?.name || "MP4, MOV, MKV, WebM or TS"; });
document.querySelectorAll("input[name=source-mode]").forEach(node => node.addEventListener("change", () => {
  by("live-fields").hidden = node.value !== "live";
  by("recording-fields").hidden = node.value === "live";
  by("start-job").textContent = node.value === "live" ? "Start capture" : "Start processing";
}));
by("preset").addEventListener("change", () => {
  const p = state.presets[input("preset")];
  if (!p) return;
  const fields = {aspect:p.aspect,resolution:p.resolution,layout:p.layout || "shorts",
    "duration-min":p.min_ms/1000,"duration-max":p.max_ms/1000,"duration-pref":p.preferred_ms/1000};
  for (const [id,value] of Object.entries(fields)) by(id).value = value;
});
by("refresh-jobs").addEventListener("click", () => jobs().catch(error => announce(error.message,true)));
by("cancel-job").addEventListener("click", async () => {
  try { await post("/api/jobs/" + state.job + "/cancel", {}); await refresh(); } catch (error) { announce(error.message,true); }
});
by("retry-job").addEventListener("click", async () => {
  try { const data = await post("/api/jobs/" + state.job + "/retry", {}); await selectJob(data.id); }
  catch (error) { announce(error.message,true); }
});
by("load-frame").addEventListener("click", loadFrame);
by("close-edit").addEventListener("click", () => by("edit-dialog").close());
request("/api/session").then(data => {
  state.token = data.token;
  state.presets = data.presets;
  return jobs();
}).catch(error => announce(error.message,true));
setInterval(() => { if (state.job && state.rendered !== state.job) refresh().catch(error => announce(error.message,true)); }, 900);