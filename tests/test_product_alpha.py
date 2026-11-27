from pathlib import Path
import json
import shutil
from threading import Thread
from time import monotonic, sleep
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from chomp.clipcontrol import ClipControl
from chomp.director.provider import Response
from chomp.evolution import EvolutionConfig, _assess
from chomp.evolution_context import Proposal, investigate
from chomp.evolution_memory import Memory
from chomp.evolution_reasoning import Reasoner
from chomp.media import exec_checked, probe
from chomp.research_interfaces import evidence_item
from chomp.studio.server import StudioServer


def test_clipcontrol_intersects_campaign_and_scales_target():
    config = ClipControl(clips=12, mode="TARGET", min_ms=15000, max_ms=45000,
                         campaign_min_ms=1000, campaign_max_ms=35000, preferred_ms=40000,
                         aspect="16:9", resolution=720)
    assert config.bounds == (15000, 35000)
    assert config.preferred == 35000 and config.dimensions == (1280, 720)
    assert config.calls > 8 and config.export_summary(4)["shortfall"] == 8
    with pytest.raises(ValueError, match="intersect"):
        ClipControl(min_ms=30000, campaign_max_ms=25000)
    with pytest.raises(ValueError, match="source range"):
        config.check_span(10, 15009, 60000)


def test_thinkloop_reconsiders_and_verifies_repaired_interval(tmp_path):
    identity = {"source_id": "test", "duration_ms": 60000}

    def row(start, end, text):
        return evidence_item(**identity, start_ms=start, end_ms=end, modality="utterance",
                 model_fingerprint={"producer": "test"}, provenance={"source": "fixture"},
                 speaker_id="storyteller", text=text)

    setup = row(1000, 5000, "The blue envelope contained the librarian's note.")
    core = row(16000, 25000, "I finally realized what the unexpected gesture meant.")
    ending = row(26500, 28000, "Everything was resolved by the end of the day.")
    memory = Memory([setup, core, ending], **identity)
    proposal = Proposal("dialogue", (core["id"],), 16000, 25000, .9, "Story")
    investigation = investigate(memory, proposal, False, False)
    calls = []

    class Provider:
        fingerprint = {"model": "controlled test"}

        def complete_structured(self, prompt, schema, *, timeout):
            phase = prompt.split("Phase: ")[1].split("\n")[0]
            payload = json.loads(prompt[prompt.index("\n{") + 1:])
            calls.append(phase)
            if phase in {"investigation", "reconsider"}:
                answer = {"candidate_id": "id", "interpretation": "Story", "viewer_value": "Closure",
                        "supporting_ids": [core["id"]], "missing_information": [], "alternative_interpretations": [],
                        "relationships": [], "interpretation_ids": [], "critical_issues": [], "uncertainty": "low",
                        "stop_reason": "ready", "viewer_groups": [[]], "queries": [], "action": "select"}
                if phase == "investigation":
                    answer["action"] = "request_context"
                    answer["queries"] = [{"query": "blue envelope", "scope": "past", "observation": "utterance"}]
                else:
                    assert payload["newly_retrieved_ids"] == [setup["id"]]
                    answer["viewer_groups"] = [[setup["id"]]]
                    answer["relationships"] = [{"kind": "setup", "evidence_id": setup["id"], "role": "viewer"}]
            else:
                first = phase == "verify"
                assert payload["interval_ms"] == ([1000, 25000] if first else [1000, 28000])
                answer = {"candidate_id": "id", "action": "expand_end" if first else "select",
                          "rationale": "Preserve the natural ending", "score": 88, "critical_issues": [],
                          "supporting_ids": [core["id"]], "repair_evidence_id": ending["id"] if first else None}
            return Response("response_received", decision=answer)

    reasoner = Reasoner(Provider(), tmp_path, 8, balanced=True)
    control = ClipControl(min_ms=9000, max_ms=35000, preferred_ms=24000)
    config = EvolutionConfig(min_ms=9000, max_ms=35000)
    candidate, outcome = _assess(memory, proposal, "id", investigation, reasoner, tmp_path / "unused",
                                 tmp_path, config, identity, [], 250, 45, None, control)
    assert calls == ["investigation", "reconsider", "verify", "verify_repair"]
    assert outcome["decision"] == "eligible"
    assert (candidate.start_ms, candidate.end_ms) == (1000, 28000)
    assert outcome["provider_investigation"]["answer"]["viewer_groups"] == [[setup["id"]]]
    assert len(outcome["verification_rounds"]) == 2


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg required")
def test_studio_upload_job_playback_revision_archive_and_feedback(tmp_path):
    media = tmp_path / "fixture.mp4"
    exec_checked(["ffmpeg","-v","error","-f","lavfi","-i","color=s=160x90:r=12:d=14",
                  "-f","lavfi","-i","sine=duration=14","-shortest","-c:v","libx264","-c:a","aac",str(media)])
    transcript = {"segments": [{"start_ms": 1000, "end_ms": 10000,
        "text": "I finally realized why the library keeps blue envelopes in different books. "
                "What matters is the note that connects the reader with the author."}]}
    with StudioServer(0, tmp_path / "studio") as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            def get(path, **headers):
                with urlopen(Request(base + path, headers=headers), timeout=20) as response:
                    return response.status, response.headers, response.read()

            token = json.loads(get("/api/session")[2])["token"]
            def post(path, payload, filename=None):
                data = payload if filename else json.dumps(payload).encode()
                headers = {"X-Chomp-Local": token, "Content-Type": "application/octet-stream" if filename else "application/json"}
                if filename:
                    headers["X-Filename"] = filename
                with urlopen(Request(base + path, data, headers), timeout=20) as response:
                    return json.load(response)

            def wait(job):
                deadline = monotonic() + 35
                while monotonic() < deadline:
                    state = json.loads(get("/api/jobs/" + job["id"])[2])
                    if state["status"] in {"completed", "partial", "failed", "cancelled"}:
                        return state
                    sleep(.15)
                pytest.fail("Studio worker timed out")

            assert b"Make clips" in get("/")[2]
            with pytest.raises(HTTPError) as err:
                get("/api/session", Host="untrusted.example")
            assert err.value.code == 403
            upload = post("/api/uploads", media.read_bytes(), "fixture.mp4")["id"]
            assert post("/api/uploads", media.read_bytes(), "same.mp4")["id"] == upload
            script = post("/api/uploads", json.dumps(transcript).encode(), "script.json")["id"]
            job = post("/api/jobs", {"upload":upload, "transcript_upload":script, "confirmed":True, "rights_ref":"synthetic",
                "requirements":{"mode":"TARGET","clips":5,"min_ms":6000,"max_ms":12000,"layout":"fit","aspect":"1:1"}})
            result = wait(job)
            assert (result["status"], result["produced"], result["count_result"]["shortfall"]) == ("partial",1,4)
            root = "/api/jobs/" + job["id"]
            clip = root + "/clips/1/"
            prefix = get(clip + "play", Range="bytes=0-31")
            assert prefix[0] == 206 and len(prefix[2]) == 32
            downloaded = get(clip + "download")
            assert "attachment" in downloaded[1]["Content-Disposition"]
            saved = tmp_path / "download.mp4"
            saved.write_bytes(downloaded[2])
            assert probe(saved)["duration_ms"] == 9000
            assert get(root + "/zip")[2].startswith(b"PK")
            post(clip + "feedback", {"decision":"approve"})
            revise = post(clip + "revise", {"start_ms":1200,"end_ms":9700,"layout":"fit"})
            assert wait(revise)["status"] == "completed"
            revised = json.loads(get(root + "/clips")[2])["clips"][0]
            assert (revised["start_ms"], revised["end_ms"]) == (1200,9700)
            assert revised["feedback"] is None
        finally:
            server.shutdown()
            thread.join()