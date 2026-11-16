# Editing regression index

| ID | Reproduction | Expected review evidence |
| --- | --- | --- |
| EMIRU-03 | `test2(emiru vod).mp4`, 16,490–29,590 source ms. | Retain the stable camera split across 19,490–19,740 ms. Record live source continuity, camera fit and the rendered seam. [StableCamera handoff](../tasks/EDIT-FX-01.md) |
| NIGHTCRAWLER | `test.mp4`, 780,000–795,000 source ms. | Separate in-game camera motion, the editor's crop motion and the enlarged source webcam. Confirm the camera return at 794,850 ms. Measure player and HUD coverage at matching timestamps. |
| JAY3-03 | `test.mp4`, 73,350–90,170 source ms. | **Subject unavailable / insufficient gameplay visibility.** The source shows the selected player at the beginning, an enlarged webcam by 73,733 ms, a narrow gameplay strip, then the player again in ordinary gameplay. The expanded panel covers the recent player region during 73,716–77,416 ms in the plan. Preserve the difference between a covered region, a lost track in visible gameplay and a crop that excludes a visible player. The recording establishes the source overlay. A death event remains unverified. |

In EDIT-FX-03 the editor should use the JAY3-03 evidence to decide whether to show the streamer, available context or another approved shot. EDIT-FX-06 should verify subject visibility, viewport coverage and supported source pixels independently. Both phases should use matching source times and keep screenshots, track state and approved outputs linked to their source and edit hashes.
