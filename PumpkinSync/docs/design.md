# PumpkinSync MVP Design

## Goal

Create a local-first application that accepts a song or spoken skit, derives a stable mouth-cue timeline, previews the result against a user-owned Trick-style face template, permits quick cue corrections, and exports a projector-ready MP4 containing the supplied audio.

## Core workflow

1. Import MP3, WAV, M4A, AAC, FLAC, or OGG.
2. Convert a mono 16 kHz analysis copy while preserving the original input for export.
3. Optionally isolate vocals with Demucs when installed.
4. Prefer Rhubarb Lip Sync when its executable is available; otherwise use the bundled deterministic spectral/envelope analyzer.
5. Preview the cue sequence in a browser with audio playback, waveform navigation, cue selection, and A-H/X hotkeys.
6. Render a 1920x1080, 30 fps, H.264/yuv420p MP4 with AAC audio using FFmpeg.

## Architecture

- FastAPI local server and static vanilla-JavaScript interface.
- Project folders containing the original audio, analysis WAV, waveform data, cue JSON, settings, and exports.
- A template manifest maps visemes A-H/X to pre-rendered user-specific face frames.
- Rendering uses FFmpeg's concat demuxer so long tracks do not require creating thousands of intermediate bitmap frames.
- No cloud account, remote upload, telemetry, or database.

## MVP limits

The bundled analyzer is audio-reactive and phoneme-inspired rather than transcript-perfect. Installing Rhubarb improves explicit viseme recognition. Demucs is optional because its model is substantially larger than the base application.

## Private media handling

The user-specific template is derived from media supplied by its owner for personal use. Purchased source videos, source audio, PDFs, and cover artwork are not included in the deliverable. Template assets are not covered by the software license and should not be redistributed.
