# Sample footage

## `demo-street-traffic.avi`

* Source: `vtest.avi`, sample data published with the OpenCV project
  (`opencv_extra`), which is distributed under the Apache License 2.0.
* Content: a short street scene (768×576, 10 fps, ~80 s) with pedestrians and
  passing vehicles; originally captured for pedestrian-tracking research.
* Purpose: lets you try CivicWatch immediately after setup — around 45 seconds
  of analysis on a Mac, and it produces a handful of trackable vehicles.
* **Not** for enforcement, adjudication, commercial model training, or any use
  beyond a functional demo. It shows identifiable members of the public, so
  treat it like any other CCTV clip: do not redistribute it as part of your own
  footage library.

## A useful demo sequence

1. Video Analysis → upload this file.
2. Zone Editor → load a frame from that upload → draw:
   * a **No parking** polygon over the kerb lane (dwell 20 s default),
   * a **Footpath** polygon over the pedestrian area (dwell 5 s).
3. Video Analysis → sampling **every 6th–12th frame** (good balance of speed and
   continuity) → Start.
4. Incidents → open a candidate, check the snapshot and clip, then confirm or
   dismiss it.

## Using your own footage

Supported inputs are whatever the bundled OpenCV build can decode: MP4/M4V
(H.264, H.265 where decodable), AVI, MOV, MKV and most RTSP/HTTP streams for
live use. Practical guidance:

* 720p–1080p, 10–25 fps, 30 s–10 min clips process comfortably on a laptop.
* Keep the camera angle fixed and the road surface visible; heavy rain, night
  glare and severe compression reduce detection quality.
* For live cameras, confirm authorisation in writing before connecting, and add
  the camera in Live Monitoring rather than uploading a recording.
* Never commit real municipal footage to this repository — `samples/*.mp4`,
  `*.avi`, `*.mov` and `*.mkv` are git-ignored, with this demo as the only
  exception.
