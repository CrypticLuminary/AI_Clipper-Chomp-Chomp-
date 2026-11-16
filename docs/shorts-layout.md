# Shorts layout manual

The visual manual's default composition is a stable streamer panel over the important gameplay. `shorts` is the default layout on the command line. It tracks source geometry independently of the output layout. The output is 9:16, with the top camera pane at 30% of the picture when a webcam is visible.

| Source condition | Shorts behavior |
| --- | --- |
| Webcam in a corner, along an edge, or at top/bottom center | Locate the face with YuNet, confirm the enclosing camera panel using image edges, and refine repeated panels using their independent motion. |
| Webcam enlarges or moves | Update the source camera box while leaving the output camera seam in place. A cut is refined near the observed change. |
| Gameplay moves within the picture | Plan a smooth crop from motion, detail and optional learned saliency. Exclude the observed webcam container and encoded black edges from the gameplay viewport. |
| The game scene or a persistent overlay changes | Plan the next gameplay view independently from the previous scene. A short transient does not need to move the crop. |
| No webcam panel can be found | Fill the portrait with the visible gameplay and ask the reviewer to check the action and HUD. Ambiguous or unsupported layouts use a full-picture preview. |
| The webcam covers most of the original image | Use the available gameplay pixels and flag the span. A separately recorded gameplay feed is needed to see what the composite covered. |

Use `--camera-slot bottom`, `left`, or `right` for alternate compositions. Side placements use an inset, so inspect the area it covers before approval. Use `--facecam-roi` or `--layout-plan` to override framing for a source. Edit plans store source-clock camera regions, gameplay crop keyframes, detector evidence, and framing warnings. Rerendering the same interval with a different profile or caption position reuses those observations.

## Run on Windows

Install Python 3.11+ and FFmpeg with libass. Install the optional OpenCV dependency to enable the planner. The wheel includes the small pinned YuNet ONNX file and its MIT notice.

~~~powershell
python -m pip install -e '.[facecam,asr]'
chomp --input 'C:\VODs\recording.mp4' --transcribe --rights-ref creator-grant --acknowledge-permission --layout shorts --profile final --render
~~~

A timestamped `--transcript` may be used in place of local Whisper. The `final` profile uses 1080×1920 H.264/AAC with a 12 Mbps video bitrate target and retains the source frame cadence for ordinary 30/60 fps footage. The `preview` profile uses 540×960. Open the review page in the printed job folder.

The normal planner uses motion and detail to track gameplay. The small Apache-2.0 U²-Net ONNX model adds a foreground estimate. It runs locally in OpenCV on sampled frames from the selected clip, and repeated renders can reuse the saved crop plan.

~~~powershell
New-Item -ItemType Directory -Force -Path models
curl.exe --fail --location 'https://huggingface.co/nfa04/u2netp-onnx/resolve/8e72726/u2netp.onnx' --output 'models\u2netp.onnx'
Get-FileHash -Algorithm SHA256 'models\u2netp.onnx'
~~~

The expected SHA-256 is `309c8469258dda742793dce0ebea8e6dd393174f89934733ecc8b14c76f4ddd8`. Add `--saliency-model models\u2netp.onnx` to the clip command.

## Editorial and review

Keep the hook, essential setup, payoff, and meaningful reaction. Aim for roughly 10–20 seconds when the event fits. Allow more time when context or the reaction needs it. Routine gameplay, filler transitions and menu views are weak candidates unless they are central to the event. Text-based semantic ranking remains an optional local model. Gameplay-only events that have no meaningful audio or transcript warrant separate visual review.

Captions use high contrast with up to two lines and preserve the transcript words. Overlapping dialogue gets a timing review flag when re-sequencing is needed. Move the cue with `--caption-position` or supply a corrected SRT in a revision. Use `--title` on an exact interval or rerender to supply a short, truthful title. The semantic ranker can propose a short title from cited dialogue. Where a suitable standalone quotation is present, it can also be suggested from the transcript. `--burn-hook` shows the title for up to 1.75 seconds.

Approve only after watching the exported clip and checking the camera, gameplay and key UI, captions, audio, and editorial payoff. A legal crop can still hide an important part of the source. Open its saved plan if you need to adjust the crop or reframe a cut. Use the independent benchmark and review workflow in the [Stage 1 guide](stage1-quality.md).

See [Finishing a Short](shorts-finishing.md) for editorial checks and timecoded sound cues.

Model and design sources are [OpenCV YuNet](https://huggingface.co/opencv/face_detection_yunet), [U²-Net upstream](https://github.com/xuebinqin/U-2-Net), the [pinned U²-Net-small ONNX mirror](https://huggingface.co/nfa04/u2netp-onnx), and [AutoFlip's scene-aware crop planning](https://research.google/blog/autoflip-an-open-source-framework-for-intelligent-video-reframing/).
