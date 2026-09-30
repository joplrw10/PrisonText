# PumpkinSync

PumpkinSync is a local browser editor that turns an audio file into a lip-synchronized Trick-style performance video. It accepts spoken skits or songs, generates an editable A-H/X mouth-cue timeline, previews the animation against your private calibrated face template, and exports an MP4 containing the original supplied audio.

No account, cloud upload, subscription, telemetry, or database is used.

## What is included

- A private face template calibrated from your supplied purchased media: 9 mouth shapes × 3 expressions × 3 motion poses.
- Built-in offline audio analysis that works immediately.
- Optional Rhubarb Lip Sync support for stronger phoneme-oriented cue timing.
- Optional Demucs vocal isolation for songs; the isolated stem is used only for analysis.
- Waveform navigation, per-cue correction, A-H/X keyboard shortcuts, automatic saving, and one-click reopening of recent projects.
- Quick 960×540 proof exports and final 1920×1080 exports.
- H.264/yuv420p video, 30 fps, AAC 48 kHz stereo audio, and web-optimized MP4 layout.

## Accuracy expectations

The built-in analyzer is deterministic, audio-reactive, and phoneme-inspired. It gives a useful first pass, especially for clean dialogue, but it does not know the exact words. Rhubarb plus a pasted script or lyrics gives more deliberate viseme timing. For songs, Demucs helps prevent drums and instruments from opening the mouth, but difficult mixes, backing vocals, screams, and heavily processed vocals can still require cue corrections.

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

PumpkinSync opens at `http://127.0.0.1:8765`. Closing the console stops the local server.

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

Projects and exports persist under `./data`. The container binds port 8765 to the LAN, so only expose it to networks you trust. This MVP has no user authentication and is intended for a private home network.

To stop it:

```bash
docker compose down
```

The base Docker image does not install Demucs because its model and PyTorch dependencies substantially increase the image size. Use the Windows/Linux source installation with `-WithDemucs`, or extend the Docker image with the `songs` optional dependency.

## Making a performance

1. Drag in an MP3, WAV, M4A, AAC, FLAC, OGG, or OPUS file.
2. Choose **Skit** for dialogue or **Song** for music.
3. Leave the analyzer on **Auto**. PumpkinSync uses Rhubarb when available and otherwise uses the built-in analyzer.
4. For a song, enable vocal isolation when Demucs is installed.
5. Paste the known script or lyrics when using Rhubarb. This text is stored only inside the local project.
6. Select **Analyze and create performance**.
7. Play the preview. Click the waveform or a cue row to navigate.
8. Change a selected cue with the A-H/X buttons or keyboard keys. Left/right arrows move between cues.
9. Make a quick proof export to check placement and timing.
10. Export the final 1080p MP4 and import or play it through your normal projector workflow.

## Mouth shapes

| Key | Typical sound or position |
|---|---|
| A | Closed lips: P, B, M |
| B | Small opening and many consonants |
| C | Medium-open vowels |
| D | Wide-open AH sounds |
| E | Rounded OH / ER |
| F | Narrow OO / W |
| G | F / V contact |
| H | L / tongue-forward sounds |
| X | Rest or silence |

## Optional Rhubarb installation

PumpkinSync searches these locations in order:

1. The `PUMPKINSYNC_RHUBARB` environment variable.
2. `tools/rhubarb/rhubarb.exe` on Windows or `tools/rhubarb/rhubarb` on Linux.
3. An executable named `rhubarb` available on `PATH`.

Rhubarb is optional and is not bundled here. The health panel in the editor reports whether it was found. The installers below query the official GitHub `releases/latest` API when you run them, then place the matching executable and resource files under `tools/rhubarb`.

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
project.json          settings and source metadata
source/               original uploaded audio
analysis.wav          mono analysis copy
waveform.json         browser waveform data
cues.json             editable A-H/X timeline
transcript.txt        optional local script or lyrics
exports/              proof and final MP4 files
```

Projects remain local. Delete a project folder to remove its audio, transcript, cues, and exports.

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

## Troubleshooting

**The health panel says FFmpeg is missing**  
Run `setup_windows.ps1` again after opening a new PowerShell window, or install FFmpeg and ensure both `ffmpeg` and `ffprobe` are on `PATH`.

**Song vocal isolation is unavailable**  
Run `setup_windows.ps1 -WithDemucs`. Its first use can take longer because Demucs obtains its model locally.

**The mouth reacts to instruments**  
Enable Demucs, use a cleaner vocal source, or manually set instrumental regions to X.

**The words look too approximate**  
Install Rhubarb and paste the script or lyrics before analysis, then correct the few remaining cues in the timeline.

**The browser did not open automatically**  
Open `http://127.0.0.1:8765` manually while the PumpkinSync console is running.

## Privacy and media status

The application never contacts a PumpkinSync server because no such service exists. Browser, operating-system, package-manager, Demucs-model, or dependency downloads are separate network activity initiated by your own environment.

Purchased source videos and audio are not included. The calibrated private template is not covered by the MIT software license and should not be redistributed. See `USER_MEDIA_NOTICE.md`.
