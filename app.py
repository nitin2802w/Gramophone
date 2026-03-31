"""
GRAMOPHONE — Unified Music Player
Serves the HTML UI via Flask and exposes REST + SSE endpoints
for all playback, download, and playlist management.
"""

# ── Auto-install missing packages ─────────────────────────────────────────────
import sys, subprocess

REQUIRED = {
    "flask":           "flask",
    "pygame":          "pygame",
    "mutagen":         "mutagen",
    "pandas":          "pandas",
    "yt_dlp":          "yt-dlp",
    "spotify_scraper": "spotify-scraper",
    "requests":        "requests",
    "flask_cors":      "flask-cors",
}

def install_missing():
    missing = []
    for mod, pip_name in REQUIRED.items():
        try:
            __import__(mod)
        except ImportError:
            missing.append(pip_name)
    if missing:
        print(f"[Setup] Installing: {', '.join(missing)}")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", *missing])
        print("[Setup] Done.\n")

install_missing()

# ── Imports ───────────────────────────────────────────────────────────────────
import os, re, shutil, zipfile, random, threading, urllib.request, json, time, queue
from datetime import date
import pygame
from mutagen.mp3 import MP3
import pandas as pd
import yt_dlp
import requests as req
from spotify_scraper import SpotifyClient
from flask import Flask, jsonify, request, send_from_directory, Response, send_file
from flask_cors import CORS

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
SONGS_ROOT = os.path.join(BASE_DIR, "songs")
FFMPEG_DIR = os.path.join(BASE_DIR, "ffmpeg")
STATIC_DIR = os.path.join(BASE_DIR, "static")
CSV_DIR    = os.path.join(BASE_DIR, "csv_data")   # ← dedicated CSV folder

for d in [SONGS_ROOT, FFMPEG_DIR, STATIC_DIR, CSV_DIR]:
    os.makedirs(d, exist_ok=True)

# ── ffmpeg ────────────────────────────────────────────────────────────────────
def find_ffmpeg():
    if shutil.which("ffmpeg"):
        return None
    local_bin = os.path.join(FFMPEG_DIR, "bin")
    if os.path.isfile(os.path.join(local_bin, "ffmpeg.exe")):
        return local_bin
    for path in [r"C:\ffmpeg\bin", r"C:\Program Files\ffmpeg\bin"]:
        if os.path.isfile(os.path.join(path, "ffmpeg.exe")):
            return path
    return None

FFMPEG_PATH = find_ffmpeg()

# ── Pygame ────────────────────────────────────────────────────────────────────
pygame.mixer.init()

# ── State ─────────────────────────────────────────────────────────────────────
state = {
    "playlist":       None,
    "songs":          [],
    "current_index":  0,
    "is_playing":     False,
    "is_paused":      False,
    "volume":         0.75,
    "shuffle":        False,
    "repeat":         False,
    "total_length":   0.0,
    "seek_base":      0.0,
    "paused_at":      0.0,
    "downloading":    False,
    "shuffle_order":  [],
    "shuffle_pos":    0,
}

state_lock = threading.Lock()
log_queue  = queue.Queue()

# ── Helpers ───────────────────────────────────────────────────────────────────
def format_time(seconds):
    m = int(seconds // 60)
    s = int(seconds % 60)
    return f"{m}:{s:02d}"

def get_playlists():
    try:
        return [d for d in sorted(os.listdir(SONGS_ROOT))
                if os.path.isdir(os.path.join(SONGS_ROOT, d))]
    except:
        return []

def get_songs_in_playlist(pl_name):
    folder = os.path.join(SONGS_ROOT, pl_name)
    if not os.path.isdir(folder):
        return []
    songs = []
    for f in sorted(os.listdir(folder)):
        if not f.lower().endswith(".mp3"):
            continue
        path  = os.path.join(folder, f)
        base  = os.path.splitext(f)[0]
        img   = os.path.join(folder, base + ".jpg")
        parts = base.split(" - ", 1)
        title  = parts[0].strip() if len(parts) > 0 else base
        artist = parts[1].strip() if len(parts) > 1 else "Unknown"
        try:
            dur_s = MP3(path).info.length
            dur   = format_time(dur_s)
        except:
            dur   = "?:??"
            dur_s = 0
        songs.append({
            "id":      base,
            "title":   title,
            "artist":  artist,
            "album":   pl_name,
            "dur":     dur,
            "dur_s":   dur_s,
            "path":    path,
            "img":     img if os.path.exists(img) else None,
        })
    return songs

def song_to_api(s, idx):
    return {
        "id":      s["id"],
        "index":   idx,
        "title":   s["title"],
        "artist":  s["artist"],
        "album":   s["album"],
        "dur":     s["dur"],
        "dur_s":   s["dur_s"],
        "has_img": s["img"] is not None,
    }

def generate_shuffle_order(songs, current_idx=0):
    n = len(songs)
    if n == 0:
        return []
    order = list(range(n))
    for i in range(n - 1, 0, -1):
        j = random.randint(0, i)
        order[i], order[j] = order[j], order[i]
    if 0 <= current_idx < n:
        pos = order.index(current_idx)
        order[0], order[pos] = order[pos], order[0]
    return order

# ── Playback engine ───────────────────────────────────────────────────────────
_playback_thread = None

def _playback_monitor():
    while True:
        time.sleep(0.5)
        with state_lock:
            if not state["is_playing"] or state["is_paused"]:
                continue
            if not pygame.mixer.music.get_busy():
                _next_song_locked()

def _next_song_locked():
    songs = state["songs"]
    if not songs:
        return
    if state["repeat"]:
        pass
    elif state["shuffle"]:
        n     = len(songs)
        order = state["shuffle_order"]
        if not order or len(order) != n:
            order = generate_shuffle_order(songs, state["current_index"])
            state["shuffle_order"] = order
        next_pos = (state["shuffle_pos"] + 1) % n
        state["shuffle_pos"]   = next_pos
        state["current_index"] = order[next_pos]
    else:
        state["current_index"] = (state["current_index"] + 1) % len(songs)
    _play_current_locked()

def _play_current_locked():
    songs = state["songs"]
    if not songs:
        return
    idx  = state["current_index"]
    song = songs[idx]
    try:
        pygame.mixer.music.load(song["path"])
        pygame.mixer.music.set_volume(state["volume"])
        pygame.mixer.music.play()
        state["total_length"] = song["dur_s"]
        state["is_playing"]   = True
        state["is_paused"]    = False
        state["seek_base"]    = 0.0
        state["paused_at"]    = 0.0
    except Exception as e:
        print(f"[Play] Error: {e}")

def start_playback_monitor():
    global _playback_thread
    if _playback_thread and _playback_thread.is_alive():
        return
    _playback_thread = threading.Thread(target=_playback_monitor, daemon=True)
    _playback_thread.start()

start_playback_monitor()

# ── Image download helper ─────────────────────────────────────────────────────
def download_image(url, save_path, log_cb=None):
    if not url:
        return False
    if os.path.exists(save_path):
        return True
    try:
        r = req.get(url, timeout=10)
        r.raise_for_status()
        with open(save_path, "wb") as f:
            f.write(r.content)
        return True
    except Exception as e:
        if log_cb:
            log_cb(f"⚠️ Image: {e}")
        return False

def get_youtube_thumbnail(query):
    try:
        with yt_dlp.YoutubeDL({"quiet": True}) as ydl:
            info = ydl.extract_info(f"ytsearch1:{query}", download=False)
            if "entries" in info and info["entries"]:
                return info["entries"][0].get("thumbnail")
    except:
        return None

# ── Spotify download ──────────────────────────────────────────────────────────
def _download_thread(url):
    def log(msg):
        log_queue.put({"type": "log", "msg": msg})
    def done():
        log_queue.put({"type": "done"})

    log(" Connecting to Spotify...")
    try:
        client   = SpotifyClient()
        playlist = client.get_playlist_info(url)
        raw_name = playlist.get("name", "Untitled_Playlist")
        name     = re.sub(r'[\\/*?:"<>|]', "", raw_name).strip()

        out_dir = os.path.join(SONGS_ROOT, name)
        os.makedirs(out_dir, exist_ok=True)

        tracks = playlist.get("tracks", [])
        log(f" {name} — {len(tracks)} songs")

        rows = []
        for track in tracks:
            images    = track.get("album", {}).get("images", [])
            image_url = images[0].get("url") if images else None
            if not image_url:
                q = f"{track.get('name')} {track.get('artists',[{}])[0].get('name','')}"
                image_url = get_youtube_thumbnail(q)
            rows.append({
                "Track Name":     track.get("name"),
                "Artist Name(s)": ", ".join([a.get("name") for a in track.get("artists", [])]),
                "Album Name":     track.get("album", {}).get("name"),
                "Release Date":   track.get("album", {}).get("release_date", "N/A"),
                "Duration (ms)":  track.get("duration_ms", 0),
                "Image URL":      image_url,
            })

        # ── Save CSV to dedicated csv_data/ folder ─────────────────────────────
        csv_path = os.path.join(CSV_DIR, f"{name}.csv")
        pd.DataFrame(rows).to_csv(csv_path, index=False)
        log(f" CSV saved → csv_data/{name}.csv")

        base_opts = {
            "format":     "bestaudio/best",
            "noplaylist": True,
            "quiet":      True,
            "postprocessors": [{
                "key":              "FFmpegExtractAudio",
                "preferredcodec":   "mp3",
                "preferredquality": "192",
            }],
        }
        if FFMPEG_PATH:
            base_opts["ffmpeg_location"] = FFMPEG_PATH

        for i, row in enumerate(rows, 1):
            tname  = row["Track Name"]
            artist = row["Artist Name(s)"]
            safe   = re.sub(r'[\\/*?:"<>|]', "", f"{tname} - {artist}")
            mp3    = os.path.join(out_dir, safe + ".mp3")
            img    = os.path.join(out_dir, safe + ".jpg")

            log(f"[{i}/{len(rows)}] {'⏭' if os.path.exists(mp3) else '⬇'} {tname}")
            download_image(row["Image URL"], img, log)

            if not os.path.exists(mp3):
                try:
                    opts = {**base_opts, "outtmpl": os.path.join(out_dir, safe + ".%(ext)s")}
                    with yt_dlp.YoutubeDL(opts) as ydl:
                        ydl.download([f"ytsearch1:{tname} {artist} official audio"])
                except Exception as e:
                    log(f" {tname}: {e}")

        log(f"\n✨ Done! '{name}' is ready.")
        log_queue.put({"type": "playlist_ready", "name": name})

    except Exception as e:
        log(f" Error: {e}")
    finally:
        with state_lock:
            state["downloading"] = False
        done()

# ── Flask app ─────────────────────────────────────────────────────────────────
app = Flask(__name__, static_folder=STATIC_DIR)
CORS(app)

@app.route("/")
def index():
    return send_from_directory(BASE_DIR, "index.html")

# ── Playlists ─────────────────────────────────────────────────────────────────
@app.route("/api/playlists")
def api_playlists():
    pls = get_playlists()
    cur = state.get("playlist")
    return jsonify([{"name": p, "active": p == cur} for p in pls])

@app.route("/api/playlist/<n>/select", methods=["POST"])
def api_select_playlist(n):
    folder = os.path.join(SONGS_ROOT, n)
    if not os.path.isdir(folder):
        return jsonify({"error": "Not found"}), 404
    songs = get_songs_in_playlist(n)
    with state_lock:
        state["playlist"]      = n
        state["songs"]         = songs
        state["current_index"] = 0
        state["is_playing"]    = False
        state["is_paused"]     = False
        state["shuffle_pos"]   = 0
        state["shuffle_order"] = generate_shuffle_order(songs, 0)
        pygame.mixer.music.stop()
    return jsonify({"ok": True, "count": len(songs)})

@app.route("/api/playlist/<n>/delete", methods=["POST"])
def api_delete_playlist(n):
    """Delete a playlist folder and its CSV."""
    folder = os.path.join(SONGS_ROOT, n)
    if not os.path.isdir(folder):
        return jsonify({"error": "Not found"}), 404
    try:
        shutil.rmtree(folder)
        csv_path = os.path.join(CSV_DIR, f"{n}.csv")
        if os.path.exists(csv_path):
            os.remove(csv_path)
        with state_lock:
            if state["playlist"] == n:
                state["playlist"]      = None
                state["songs"]         = []
                state["current_index"] = 0
                state["is_playing"]    = False
                state["is_paused"]     = False
                pygame.mixer.music.stop()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/playlist/<n>/collage_info")
def api_playlist_collage_info(n):
    """Return indices of first 4 songs with art, for building the collage grid."""
    songs = get_songs_in_playlist(n)
    art_indices = []
    for i, s in enumerate(songs):
        if s["img"] and os.path.exists(s["img"]):
            art_indices.append(i)
        if len(art_indices) >= 4:
            break
    return jsonify({"art_indices": art_indices, "total": len(songs)})

@app.route("/api/playlist/<n>/art/<int:idx>")
def api_playlist_art(n, idx):
    """Playlist-scoped art endpoint — URL is unique per (playlist, song)
    so the browser cache never confuses art from different playlists."""
    songs = get_songs_in_playlist(n)
    if idx < 0 or idx >= len(songs):
        return jsonify({"error": "No art"}), 404
    img = songs[idx]["img"]
    if img and os.path.exists(img):
        resp = send_file(img, mimetype="image/jpeg")
        resp.headers["Cache-Control"] = "public, max-age=86400"
        return resp
    return jsonify({"error": "No art"}), 404

# ── Songs ─────────────────────────────────────────────────────────────────────
@app.route("/api/songs")
def api_songs():
    with state_lock:
        songs = state["songs"]
        cur   = state["current_index"]
    return jsonify({
        "songs":   [song_to_api(s, i) for i, s in enumerate(songs)],
        "current": cur,
    })

@app.route("/api/song/art/<int:idx>")
def api_song_art(idx):
    with state_lock:
        songs = state["songs"]
    if idx < 0 or idx >= len(songs):
        return jsonify({"error": "No art"}), 404
    img = songs[idx]["img"]
    if img and os.path.exists(img):
        return send_file(img, mimetype="image/jpeg")
    return jsonify({"error": "No art"}), 404

@app.route("/api/song/<int:idx>/play", methods=["POST"])
def api_play_song(idx):
    with state_lock:
        if idx < 0 or idx >= len(state["songs"]):
            return jsonify({"error": "Out of range"}), 400
        state["current_index"] = idx
        if state["shuffle"] and state["shuffle_order"]:
            try:
                state["shuffle_pos"] = state["shuffle_order"].index(idx)
            except ValueError:
                pass
        _play_current_locked()
    return jsonify({"ok": True})

# ── Playback controls ─────────────────────────────────────────────────────────
@app.route("/api/play", methods=["POST"])
def api_play():
    with state_lock:
        if state["is_paused"]:
            pygame.mixer.music.unpause()
            state["is_paused"]  = False
            state["is_playing"] = True
            state["seek_base"]  = state["paused_at"]
        elif not state["is_playing"]:
            _play_current_locked()
        else:
            raw = pygame.mixer.music.get_pos() / 1000
            state["paused_at"] = state["seek_base"] + raw
            pygame.mixer.music.pause()
            state["is_paused"] = True
    return jsonify({"ok": True})

@app.route("/api/pause", methods=["POST"])
def api_pause():
    with state_lock:
        if not state["is_paused"]:
            pygame.mixer.music.pause()
            state["is_paused"] = True
        else:
            pygame.mixer.music.unpause()
            state["is_paused"] = False
    return jsonify({"ok": True})

@app.route("/api/next", methods=["POST"])
def api_next():
    with state_lock:
        _next_song_locked()
    return jsonify({"ok": True})

@app.route("/api/prev", methods=["POST"])
def api_prev():
    with state_lock:
        songs = state["songs"]
        if not songs:
            return jsonify({"ok": True})
        if state["shuffle"] and state["shuffle_order"]:
            prev_pos = (state["shuffle_pos"] - 1) % len(songs)
            state["shuffle_pos"]   = prev_pos
            state["current_index"] = state["shuffle_order"][prev_pos]
        else:
            state["current_index"] = (state["current_index"] - 1) % len(songs)
        _play_current_locked()
    return jsonify({"ok": True})

@app.route("/api/volume", methods=["POST"])
def api_volume():
    vol = float(request.json.get("volume", 0.75))
    with state_lock:
        state["volume"] = vol
        pygame.mixer.music.set_volume(vol)
    return jsonify({"ok": True})

@app.route("/api/seek", methods=["POST"])
def api_seek():
    pos = float(request.json.get("position", 0))
    with state_lock:
        paused = state["is_paused"]
        pygame.mixer.music.play(start=pos)
        state["seek_base"] = pos
        state["paused_at"] = pos
        if paused:
            pygame.mixer.music.pause()
    return jsonify({"ok": True})

@app.route("/api/shuffle", methods=["POST"])
def api_shuffle():
    with state_lock:
        state["shuffle"] = not state["shuffle"]
        if state["shuffle"]:
            state["shuffle_order"] = generate_shuffle_order(
                state["songs"], state["current_index"])
            state["shuffle_pos"] = 0
        else:
            state["shuffle_order"] = []
            state["shuffle_pos"]   = 0
    return jsonify({"shuffle": state["shuffle"], "shuffle_order": state["shuffle_order"]})

@app.route("/api/repeat", methods=["POST"])
def api_repeat():
    with state_lock:
        state["repeat"] = not state["repeat"]
    return jsonify({"repeat": state["repeat"]})

@app.route("/api/state")
def api_state():
    with state_lock:
        songs  = state["songs"]
        idx    = state["current_index"]
        paused = state["is_paused"]
        playing = state["is_playing"]
        busy   = pygame.mixer.music.get_busy()
        total  = state["total_length"]

        if paused:
            real_pos = state["paused_at"]
        elif busy:
            real_pos = state["seek_base"] + pygame.mixer.music.get_pos() / 1000
        else:
            real_pos = 0.0

        real_pos = max(0.0, min(real_pos, total))
        progress = (real_pos / total * 100) if total > 0 else 0
        cur = songs[idx] if songs and 0 <= idx < len(songs) else None

    return jsonify({
        "playlist":      state["playlist"],
        "current_index": idx,
        "is_playing":    playing and (busy or paused),
        "is_paused":     paused,
        "shuffle":       state["shuffle"],
        "repeat":        state["repeat"],
        "progress":      min(progress, 100),
        "current_pos_s": real_pos,
        "total_s":       total,
        "current_time":  format_time(real_pos),
        "total_time":    format_time(total) if total else "--:--",
        "current":       song_to_api(cur, idx) if cur else None,
        "song_count":    len(songs),
        "downloading":   state["downloading"],
        "shuffle_order": state["shuffle_order"],
        "shuffle_pos":   state["shuffle_pos"],
    })

@app.route("/api/download", methods=["POST"])
def api_download():
    url = request.json.get("url", "").strip()
    if not url:
        return jsonify({"error": "No URL"}), 400
    with state_lock:
        if state["downloading"]:
            return jsonify({"error": "Already downloading"}), 409
        state["downloading"] = True
    threading.Thread(target=_download_thread, args=(url,), daemon=True).start()
    return jsonify({"ok": True})

@app.route("/api/download/stream")
def api_download_stream():
    def generate():
        while True:
            try:
                msg = log_queue.get(timeout=30)
                yield f"data: {json.dumps(msg)}\n\n"
                if msg.get("type") == "done":
                    break
            except queue.Empty:
                yield "data: {\"type\":\"ping\"}\n\n"
    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
 
if __name__ == "__main__":
    port       = int(os.environ.get("GRAMOPHONE_PORT", 5001))
    standalone = "GRAMOPHONE_PORT" not in os.environ
    print(f"\n🎵 GRAMOPHONE  —  port {port}")
    print(f"   Songs  → {SONGS_ROOT}")
    print(f"   CSVs   → {CSV_DIR}")
    if standalone:
        import webbrowser
        threading.Timer(1.5, lambda: webbrowser.open(f"http://localhost:{port}")).start()
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
