# PumpkinSync

PumpkinSync is a local browser editor that turns an audio file into a lip-synchronized, projector-ready pumpkin performance video. It accepts spoken skits or songs, generates an editable A–H/X mouth-cue timeline, previews the animation against a face template, and exports an MP4 containing the original supplied audio.

No account, cloud upload, subscription, telemetry, or database is used.

## What is included

- **A built-in carved jack-o'-lantern face** (9 mouth shapes × 3 expressions × 3 head poses), drawn on first launch, so the app works out of the box.
- **Private template support**: drop in your own calibrated face (for example a Trick-style template built from media you own) and pick it per project.
- **Offline audio analysis** that works immediately and opens/closes the mouth syllable by syllable, with a vocal-band mode for songs so drums and bass do not drive the mouth.
- Optional **Rhubarb Lip Sync** for phoneme-oriented timing (uses its phonetic recognizer for songs without lyrics).
- Optional **Demucs** vocal isolation for songs; the isolated stem is used only for analysis and is reused when you re-analyze.
- **Editor**: zoomable waveform with a colored cue strip, drag cue edges to retime, split / merge / delete cues, undo / redo, 0.5× and 0.75× playback, frame nudging, mouth-shape thumbnails, and A–H/X keyboard shortcuts. Changes save automatically.
- **Background jobs with progress** for analysis and export, re-analysis with different settings, a mouth-lead offset for projector sync, and recent projects you can reopen or delete.
- **Rhubarb-compatible cue sheets** (TSV / JSON) to download or import.
- Quick 960×540 proof exports and final 1920×1080 exports: H.264/yuv420p, 30 fps, AAC 48 kHz stereo, fast-start MP4.

## Accuracy expectations

The built-in analyzer is deterministic, audio-reactive, and phoneme-inspired. It finds syllables and closes the mouth between them, which reads well for clean dialogue, but it does not know the exact words. Rhubarb plus a pasted script or lyrics gives more deliberate viseme timing. For songs, song mode and Demucs help keep instruments from opening the mouth, but difficult mixes, backing vocals, screams, and heavily processed vocals can still need cue corrections.

The original uploaded audio—not the isolated analysis stem—is always embedded in the finished video.

## Windows setup

### 1. Extract the ZIP

Keep the whole `PumpkinSync` folder together. Do not run it from inside the ZIP viewer.

### 2. Run setup

Right-click `setup_windows.ps1`, choose **Run with PowerShell**, or open PowerShell in the folder and run:

```powershell
powershell -ExecutionPolicy Bypass -File .\setup_windows.ps1
```

The script creates a private `.venv`, installs PumpkinSync, checks for FFmpeg/FFprobe, and can install FFmpeg through WinGet when it is missing.

For optional Demucs song-vocal isolation:

```powershell
powershell -ExecutionPolicy Bypass -File .\setup_windows.ps1 -WithDemucs
```

Python 3.11 or 3.12 is recommended for the broadest Demucs/PyTorch compatibility. The base application also works without Demucs.

### 3. Launch

Double-click:

```text
run_windows.bat
```

PumpkinSync opens at `http://127.0.0.1:8765`. The first launch takes a few extra seconds while it draws the built-in pumpkin face. Closing the console stops the local server.

## Linux launch

Install Python 3.11+ and FFmpeg, then run:

```bash
chmod +x run_linux.sh
./run_linux.sh
```

## Unraid / Docker

From the extracted PumpkinSync directory:

```bash
docker compose up -d --build
```

Open:

```text
http://YOUR-UNRAID-IP:8765
```

Projects, exports, and custom templates persist under `./data`. The container binds port 8765 to the LAN, so only expose it to networks you trust. There is no user authentication; it is intended for a private home network.

To stop it:

```bash
docker compose down
```

The base Docker image does not install Demucs because its model and PyTorch dependencies substantially increase the image size. Use the Windows/Linux source installation with `-WithDemucs`, or extend the Docker image with the `songs` optional dependency.

## Face templates

PumpkinSync looks for templates in this order; the first one found is the default for new projects:

1. `--template path/to/template.json` on the command line, or the `PUMPKINSYNC_TEMPLATE` environment variable.
2. A private template installed at `src/pumpkinsync/private_template/template.json`.
3. Any folder containing a `template.json` under `data/templates/` or `templates/` next to the app.
4. The built-in jack-o'-lantern.

Every project remembers its template, and you can switch it in **Project settings** at any time. The health panel (click the status pill in the header) lists the templates that loaded and explains any that could not.

**Installing your private Trick template:** copy the `private_template` folder from your original PumpkinSync ZIP into `src/pumpkinsync/private_template/` (or into `data/templates/trick/` for Docker). Both locations are ignored by git so the private frames are never committed. See `USER_MEDIA_NOTICE.md`.

**Building a template from a performance video you own:**

```bash
pip install -e .[template-builder]
python scripts/build_private_template.py --source "your-video.mp4" --output data/templates/my-face
```

A template folder contains `template.json` and PNG frames. Each of the mouth shapes A–H and X needs at least a `normal`/`center` frame; `squint`/`wide` expressions and `up`/`down` poses are optional.

## Making a performance

1. Drag in an MP3, WAV, M4A, AAC, FLAC, OGG, OPUS, or AIFF file, or a video (MP4, MOV, WEBM, MKV) whose soundtrack you want.
2. Choose **Skit** for dialogue or **Song** for music, and pick a face template.
3. Leave the analyzer on **Auto**. PumpkinSync uses Rhubarb when available and otherwise the built-in analyzer.
4. For a song, enable vocal isolation when Demucs is installed.
5. Paste the known script or lyrics when using Rhubarb. This text is stored only inside the local project.
6. Select **Analyze performance** and watch the progress bar.
7. Play the preview. Click the waveform to jump; use **Zoom** (or Ctrl + mouse wheel) for detail.
8. Fix cues: press A–H or X to change the selected cue, drag a cue edge on the waveform to retime it, **S** to split at the playhead, **M** to merge with the next cue, **Delete** to remove a cue, and Ctrl+Z / Ctrl+Shift+Z to undo / redo.
9. If the mouth feels late on the projector, raise **Mouth lead** (30–70 ms is typical).
10. Make a quick proof export to check placement and timing, then export the final 1080p MP4.

## Keyboard shortcuts

| Key | Action |
|---|---|
| A–H, X | Set the selected cue's mouth shape |
| Space | Play / pause |
| ← / → | Previous / next cue |
| Shift + ← / → | Move the cue start one frame earlier / later |
| S | Split the cue at the playhead |
| M | Merge the selected cue with the next |
| Delete / Backspace | Remove the selected cue (the previous cue grows to fill it) |
| Ctrl+Z, Ctrl+Shift+Z / Ctrl+Y | Undo, redo |
| + / − | Zoom the waveform |
| ? | Show the shortcut list |

## Mouth shapes

| Key | Typical sound or position |
|---|---|
| A | Closed lips: P, B, M |
| B | Slightly open: most consonants |
| C | Open vowels (E in "men", A in "cat") |
| D | Wide open: AH sounds |
| E | Rounded: OH / ER |
| F | Puckered: OO / W |
| G | Upper teeth on lip: F / V |
| H | Tongue raised: L |
| X | Rest or silence |

## Optional Rhubarb installation

PumpkinSync searches these locations in order:

1. The `PUMPKINSYNC_RHUBARB` environment variable.
2. `tools/rhubarb/rhubarb.exe` on Windows or `tools/rhubarb/rhubarb` on Linux.
3. An executable named `rhubarb` available on `PATH`.

Rhubarb is optional and is not bundled here. The installers below query the official GitHub `releases/latest` API when you run them, then place the matching executable and resource files under `tools/rhubarb`.

**Windows** — after completing the normal setup:

```powershell
powershell -ExecutionPolicy Bypass -File .\install_rhubarb_windows.ps1
```

**Linux** — after running the base launcher once:

```bash
chmod +x install_rhubarb_linux.sh
./install_rhubarb_linux.sh
```

## Project files

Each import creates a self-contained folder under `data/projects/<project-id>/`:

```text
project.json          settings, template choice, mouth lead, and source metadata
source/               original uploaded audio
analysis.wav          mono analysis copy
playback.wav          browser-playable copy (only for AIFF, WMA, MOV, MKV sources)
demucs/               isolated vocal stem (song mode with Demucs)
waveform.json         browser waveform data
cues.json             editable A-H/X timeline
transcript.txt        optional local script or lyrics
exports/              proof and final MP4 files
```

Projects remain local. Delete a project from the Recent projects list (or delete its folder) to remove its audio, transcript, cues, and exports.

## Export format

Final export defaults:

```text
Container: MP4
Video: H.264 High profile, yuv420p
Resolution: 1920 × 1080
Frame rate: 30 fps constant
Audio: AAC, 48 kHz, stereo, 256 kb/s target
Background: black
Web playback: fast-start metadata
```

## Development

```bash
python -m venv .venv
.venv/bin/pip install -e .[dev]
.venv/bin/python -m pytest
```

FFmpeg must be on `PATH` for the analysis, render, and API tests. The HTTP API is documented at `http://127.0.0.1:8765/api/docs` while the app runs.

## Troubleshooting

**The header says FFmpeg is missing**  
Run `setup_windows.ps1` again after opening a new PowerShell window, or install FFmpeg and ensure both `ffmpeg` and `ffprobe` are on `PATH`.

**Song vocal isolation is unavailable**  
Run `setup_windows.ps1 -WithDemucs`. Its first use can take longer because Demucs obtains its model locally.

**The mouth reacts to instruments**  
Re-analyze in Song mode, enable Demucs, use a cleaner vocal source, or select the instrumental cues and press X.

**The words look too approximate**  
Install Rhubarb and paste the script or lyrics before analysis, then correct the few remaining cues in the timeline.

**The mouth moves too much / too little**  
Re-analyze with a lower or higher Mouth intensity.

**My private template does not appear**  
Open the health panel from the header. It lists every loaded template and the reason any template folder was skipped.

**The browser did not open automatically**  
Open `http://127.0.0.1:8765` manually while the PumpkinSync console is running.

## Privacy and media status

The application never contacts a PumpkinSync server because no such service exists. Browser, operating-system, package-manager, Demucs-model, or dependency downloads are separate network activity initiated by your own environment.

The built-in jack-o'-lantern template is original artwork generated by PumpkinSync's own code and is covered by the MIT license. Purchased source videos and audio are not included, and private calibrated templates are not covered by the MIT software license and should not be redistributed. See `USER_MEDIA_NOTICE.md`.
