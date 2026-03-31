# 🎵 Gramophone — Desktop Music Player

A vintage-themed offline music player. Paste a Spotify playlist URL → downloads all tracks as MP3 with album art → plays them back in a native desktop window. Built with Python (Flask + pygame) wrapped in Electron.

---

## Requirements

| Tool | Version | Download |
|------|---------|----------|
| **Node.js** | 18 or newer | https://nodejs.org |
| **Python** | 3.8 or newer | https://python.org |

> **Windows:** When installing Python, tick ✅ **"Add Python to PATH"**

All Python packages (Flask, pygame, yt-dlp, etc.) install automatically on first launch.

---

## Quick Start

**Windows** — double-click `start.bat`

**macOS / Linux:**
```bash
chmod +x start.sh && ./start.sh
```

**Or manually:**
```bash
npm install   # first time only (~200 MB, downloads Electron)
npm start     # launches the app
```

---

## What happens on launch

1. A **splash screen** appears instantly (spinning vinyl)
2. Python boots in the background, auto-installing any missing packages
3. The splash swaps to the full Gramophone UI in a native frameless window

First launch takes ~15–30 seconds (package installs). After that, ~1–2 seconds.

---

## File Structure

```
gramophone/
├── main.js        ← Electron: spawns Flask, manages the window
├── preload.js     ← Electron: secure IPC bridge for window controls
├── splash.html    ← Boot animation
├── index.html     ← Full player UI (served by Flask)
├── app.py         ← Python backend: playback, download, REST API
├── package.json   ← Node config + build settings
├── start.bat      ← Windows one-click launcher
├── start.sh       ← macOS/Linux one-click launcher
└── assets/
    └── icon.png   ← App icon (replace with your own 512×512 PNG)
```

Songs are stored in `songs/<Playlist Name>/` next to the app files.

---

## Using the App

**Playing music**
- Select a playlist from the **bottom strip**
- Click any track, or **drag it onto the vinyl disc** to play it
- Transport controls: ⏮ ⏯ ⏭  +  shuffle / repeat

**Downloading a Spotify playlist**
1. Click the **＋** button in the bottom strip
2. Paste any Spotify playlist URL
3. Hit **Sync & Download** — watch the live log as tracks download
4. The playlist appears in the strip automatically when done

**Adding your own MP3s**
Drop `.mp3` files into `songs/<Any Name>/`. Add matching `.jpg` files for album art.

---

## Building an Installer

Add icons to `assets/` first:
- `icon.ico`  — Windows (convert: https://cloudconvert.com/png-to-ico)
- `icon.icns` — macOS   (convert: https://cloudconvert.com/png-to-icns)

```bash
npm run build:win     # → dist/Gramophone Setup 1.0.0.exe
npm run build:mac     # → dist/Gramophone-1.0.0.dmg
npm run build:linux   # → dist/Gramophone-1.0.0.AppImage
```

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Splash stuck / backend crash | Run `python app.py` in terminal to see the error |
| "Python Not Found" | Reinstall Python with "Add to PATH" checked |
| No audio on Linux | Run `pulseaudio --start` or check PipeWire |
| Port 5001 conflict | Change `PORT = 5001` in `main.js` to another number |
| yt-dlp download fails | Install ffmpeg: `brew install ffmpeg` / `winget install ffmpeg` / `sudo apt install ffmpeg` |
