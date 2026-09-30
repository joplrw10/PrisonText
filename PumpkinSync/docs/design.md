# PumpkinSync Design

## Goal

A local-first application that accepts a song or spoken skit, derives a stable mouth-cue timeline, previews the result against a face template, permits fast cue corrections, and exports a projector-ready MP4 containing the supplied audio.

## Core workflow

1. Import MP3, WAV, M4A, AAC, FLAC, OGG, OPUS, AIFF, WMA, or a video soundtrack. The upload is stored and analysis starts as a background job.
2. Convert a mono 16 kHz analysis copy while preserving the original input for export (plus a WAV playback copy for formats browsers cannot play).
3. Optionally isolate vocals with Demucs when installed; the stem is cached per project.
4. Prefer Rhubarb Lip Sync when its executable is available; otherwise use the bundled analyzer.
5. Preview in the browser with waveform zoom, cue dragging, split/merge/undo, and A–H/X hotkeys. Edits autosave.
6. Render a 1920×1080, 30 fps, H.264/yuv420p MP4 with AAC audio using FFmpeg, as a background job with progress.

## Architecture

- FastAPI local server (`app.py`) and a static vanilla-JavaScript editor (`static/`).
- `jobs.py`: in-memory job registry. Analysis runs on a single worker (Demucs is heavy); exports run on two.
- `pipeline.py`: shared analysis for uploads and re-analysis.
- `projects.py`: one folder per project with the original audio, analysis WAV, waveform, cues, transcript, settings, and exports. Metadata updates are merged under a lock.
- `templates.py`: a template library discovered from explicit paths, the package's private template, `data/templates`, and the built-in template. A manifest maps visemes A–H/X × expression × pose to frames.
- `builtin_template.py`: draws the original jack-o'-lantern frames with Pillow and caches them under `data/.cache`.
- `render.py`: FFmpeg's concat demuxer turns the cue list into a still-image sequence, so long tracks never create thousands of intermediate bitmaps. A mouth-lead offset shifts cue boundaries before rendering.
- No cloud account, remote upload, telemetry, or database.

## Built-in analyzer

Per 20 ms frame it measures loudness (full-band RMS for skits, 250–3500 Hz band level for songs), spectral centroid, low/high band ratios, and zero-crossing rate. Energy peaks are found with `scipy.signal.find_peaks`; clear valleys between neighbouring peaks become short A/B closures so continuous speech visibly opens and closes. Each short syllable nucleus takes one shape from its loudest frame, and very short cues are merged so the mouth never flickers.

## Limits

The bundled analyzer is audio-reactive and phoneme-inspired rather than transcript-perfect. Installing Rhubarb improves explicit viseme recognition. Demucs is optional because its model is substantially larger than the base application. Jobs are in memory: a project interrupted by a restart is marked for re-analysis on the next launch.

## Private media handling

User-specific templates are derived from media supplied by their owner for personal use. Purchased source videos, source audio, PDFs, cover artwork, and private template frames are not included in the repository. Template assets other than the built-in pumpkin are not covered by the software license and should not be redistributed.
