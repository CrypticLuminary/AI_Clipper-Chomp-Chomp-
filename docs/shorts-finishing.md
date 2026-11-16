# Finishing a Short

Start with the story and its payoff. Every later edit should help the viewer hear the setup, see what changed, or understand the reaction.

| Job | Current approach | What to check on playback |
| --- | --- | --- |
| Boundaries | The director cites story beats on the source clock. A reviewer can tighten the cut after playing the source. | The setup fits the opening, the payoff lands, and the clip ends at a clean point. |
| Composition | The director specifies what must stay visible. The existing planner handles verified webcam and gameplay regions. | Check the important subject, reaction, game text, and crop movement at each cut. |
| Speech and captions | Selected-range faster-whisper supplies timing. libass renders the captions, with corrected SRT available on revisions. | Check words, sync, overlapping voices, and whether captions cover source text. |
| Title and hook | A short title and an optional on-screen hook cite the event. | The claim is accurate and the hook leaves the payoff visible. |
| Audio | The selected VOD track is the base. Optional local effects use the source clock, adjustable gain, short fades, and peak limiting. | Dialogue stays intelligible, and any added cue serves the beat. |
| Export | FFmpeg creates 9:16 H.264/AAC previews and checks the output streams and duration. | Review the export at playback size and verify the edit plan and media hashes before approval. |

Keep timing repair separate from transcription. WhisperX is an open-source forced-alignment option when a language-specific playback test shows that existing word timing is the problem. Keep caption rendering with libass and audio mixing with FFmpeg, using the components already in the export path. [WhisperX](https://github.com/m-bain/whisperX) · [libass](https://github.com/libass/libass) · [FFmpeg audio filters](https://ffmpeg.org/ffmpeg-filters.html)

## Place an optional sound cue

Use local audio you have permission to edit. Create a JSON schedule beside the audio assets. Sound cues share the recording's source clock with shots and captions. `duration_ms` selects the section to mix and `offset_ms` optionally skips into the sound file. The default offset is zero and the default gain is −18 dB.

~~~json
{
  "cues": [
    {
      "path": "soft-hit.wav",
      "at_ms": 15730,
      "duration_ms": 240,
      "offset_ms": 30,
      "gain_db": -20
    }
  ]
}
~~~

~~~powershell
chomp --input 'C:\VODs\recording.mp4' --rights-ref creator-grant --acknowledge-permission --start 12.5 --end 24.5 --sound-cues 'sound.json' --render
chomp rerender --edit 'outputs\manual-id\manual_preview.edit.json' --sound-cues 'sound.json'
~~~

A revision reuses the saved schedule and asset hashes by default. Supply a revised schedule when changing an asset or its placement. Use an empty `cues` array to remove the effects. When trimming a clip, Chomp-Chomp keeps the cue on the same source time and mixes the portion inside the new cut.
