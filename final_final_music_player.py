# ─── Auto-install missing packages BEFORE anything else ─────────────────────
import sys
import subprocess

REQUIRED_PACKAGES = {
    "pygame":           "pygame",
    "mutagen":          "mutagen",
    "pandas":           "pandas",
    "yt_dlp":           "yt-dlp",
    "spotify_scraper":  "spotify-scraper",
}

def install_missing():
    missing = []
    for module, pip_name in REQUIRED_PACKAGES.items():
        try:
            __import__(module)
        except ImportError:
            missing.append(pip_name)

    if missing:
        print(f"[Setup] Installing missing packages: {', '.join(missing)}")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--quiet", *missing]
        )
        print("[Setup] All packages installed. Starting app...\n")

install_missing()

# ─── Normal imports (now guaranteed to exist) ────────────────────────────────
import os
import re
import shutil
import zipfile
import random
import threading
import urllib.request
import tkinter as tk
from tkinter import ttk, simpledialog, messagebox
from datetime import date
import pygame
from mutagen.mp3 import MP3
import pandas as pd
import yt_dlp
from spotify_scraper import SpotifyClient

# ─── Dynamic Paths ───────────────────────────────────────────────────────────

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
SONGS_ROOT = os.path.join(BASE_DIR, "songs")
FFMPEG_DIR = os.path.join(BASE_DIR, "ffmpeg")

os.makedirs(SONGS_ROOT, exist_ok=True)
os.makedirs(FFMPEG_DIR, exist_ok=True)

# ─── ffmpeg Auto-Setup ───────────────────────────────────────────────────────

FFMPEG_URL = (
    "https://github.com/yt-dlp/FFmpeg-Builds/releases/download/latest/"
    "ffmpeg-master-latest-win64-gpl.zip"
)

def find_ffmpeg():
    if shutil.which("ffmpeg"):
        return None  # already on PATH, yt-dlp finds it automatically

    local_bin = os.path.join(FFMPEG_DIR, "bin")
    if os.path.isfile(os.path.join(local_bin, "ffmpeg.exe")):
        return local_bin

    for path in [
        r"C:\ffmpeg\bin",
        r"C:\Program Files\ffmpeg\bin",
        r"C:\Program Files (x86)\ffmpeg\bin",
    ]:
        if os.path.isfile(os.path.join(path, "ffmpeg.exe")):
            return path

    for root_dir in [r"C:\ffmpeg", r"C:\Program Files"]:
        if os.path.exists(root_dir):
            for folder in os.listdir(root_dir):
                candidate = os.path.join(root_dir, folder, "bin")
                if os.path.isfile(os.path.join(candidate, "ffmpeg.exe")):
                    return candidate

    return None


def download_ffmpeg_with_progress(progress_var, status_var):
    zip_path = os.path.join(FFMPEG_DIR, "ffmpeg.zip")

    def reporthook(block_num, block_size, total_size):
        if total_size > 0:
            pct = min(block_num * block_size / total_size * 100, 100)
            progress_var.set(pct)
            status_var.set(f"Downloading ffmpeg... {pct:.1f}%")

    try:
        status_var.set("Downloading ffmpeg (one-time setup)...")
        urllib.request.urlretrieve(FFMPEG_URL, zip_path, reporthook)

        status_var.set("Extracting ffmpeg...")
        progress_var.set(0)

        with zipfile.ZipFile(zip_path, "r") as z:
            members = z.namelist()
            total   = len(members)
            for i, member in enumerate(members):
                z.extract(member, FFMPEG_DIR)
                progress_var.set((i + 1) / total * 100)

        os.remove(zip_path)

        for folder in os.listdir(FFMPEG_DIR):
            full = os.path.join(FFMPEG_DIR, folder)
            if os.path.isdir(full) and "ffmpeg" in folder.lower():
                for item in os.listdir(full):
                    src = os.path.join(full, item)
                    dst = os.path.join(FFMPEG_DIR, item)
                    if not os.path.exists(dst):
                        shutil.move(src, dst)
                shutil.rmtree(full, ignore_errors=True)
                break

        status_var.set("✅ ffmpeg ready!")
        return True

    except Exception as e:
        status_var.set(f"❌ ffmpeg download failed: {e}")
        return False


def ensure_ffmpeg():
    path = find_ffmpeg()
    if path is not None or shutil.which("ffmpeg"):
        return path

    win = tk.Toplevel()
    win.title("One-time Setup")
    win.geometry("400x140")
    win.resizable(False, False)
    win.configure(bg="#121212")
    win.grab_set()

    tk.Label(win, text="ffmpeg not found — downloading automatically",
             font=("Helvetica", 10, "bold"), fg="#FFFFFF", bg="#121212").pack(pady=(18, 6))

    status_var   = tk.StringVar(value="Starting...")
    progress_var = tk.DoubleVar(value=0)

    tk.Label(win, textvariable=status_var,
             font=("Helvetica", 9), fg="#b3b3b3", bg="#121212").pack()

    ttk.Progressbar(win, variable=progress_var, maximum=100, length=340).pack(pady=10)

    result = {"ok": False}

    def run():
        result["ok"] = download_ffmpeg_with_progress(progress_var, status_var)
        win.after(1000, win.destroy)

    threading.Thread(target=run, daemon=True).start()
    win.wait_window()

    if result["ok"]:
        return find_ffmpeg()
    else:
        messagebox.showerror(
            "Setup Failed",
            "Could not download ffmpeg automatically.\n\n"
            "Please install it manually from https://ffmpeg.org/download.html\n"
            "and add it to your system PATH."
        )
        return None


# ─── State ───────────────────────────────────────────────────────────────────

pygame.mixer.init()

current_index = 0
is_paused     = False
total_length  = 0
seeking       = False
music_folder  = ""
songs         = []
FFMPEG_PATH   = None


# ─── Helpers ─────────────────────────────────────────────────────────────────

def format_time(seconds):
    mins = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{mins:02}:{secs:02}"


def get_playlists():
    return [d for d in os.listdir(SONGS_ROOT)
            if os.path.isdir(os.path.join(SONGS_ROOT, d))]


def refresh_combo():
    playlist_combo["values"] = get_playlists()


# ─── Spotify Downloader ──────────────────────────────────────────────────────

def download_playlist_thread(url):
    global FFMPEG_PATH
    log("🚀 Connecting to Spotify...")
    try:
        client     = SpotifyClient()
        playlist   = client.get_playlist_info(url)
        raw_name   = playlist.get("name", "Untitled_Playlist")
        clean_name = re.sub(r'[\\/*?:"<>|]', "", raw_name).strip()

        output_dir   = os.path.join(SONGS_ROOT, clean_name)
        csv_filename = os.path.join(BASE_DIR, f"{clean_name}.csv")
        os.makedirs(output_dir, exist_ok=True)

        tracks = playlist.get("tracks", [])
        log(f"📊 Found: {clean_name} ({len(tracks)} songs)")

        formatted_data = []
        for track in tracks:
            row = {
                "Track URI":      f"spotify:track:{track.get('id')}",
                "Track Name":     track.get("name"),
                "Album Name":     track.get("album", {}).get("name"),
                "Artist Name(s)": ", ".join([a.get("name") for a in track.get("artists", [])]),
                "Release Date":   track.get("album", {}).get("release_date", "N/A"),
                "Duration (ms)":  track.get("duration_ms", 0),
                "Popularity":     track.get("popularity", 0),
                "Explicit":       track.get("explicit", False),
                "Added By":       "Scraper",
                "Added At":       str(date.today()),
                "Genres":         "N/A",
                "Record Label":   "N/A",
            }
            formatted_data.append(row)

        pd.DataFrame(formatted_data).to_csv(csv_filename, index=False, encoding="utf-8")
        log(f"✅ CSV saved: {os.path.basename(csv_filename)}")
        log(f"🎵 Downloading into: {output_dir}\n")

        base_ydl_opts = {
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
            base_ydl_opts["ffmpeg_location"] = FFMPEG_PATH

        for i, song in enumerate(formatted_data, 1):
            track_name  = song["Track Name"]
            artist_name = song["Artist Name(s)"]
            safe_name   = re.sub(r'[\\/*?:"<>|]', "", f"{track_name} - {artist_name}.mp3")
            file_path   = os.path.join(output_dir, safe_name)

            if os.path.exists(file_path):
                log(f"[{i}/{len(formatted_data)}] ⏭ Skipping (exists): {track_name}")
                continue

            log(f"[{i}/{len(formatted_data)}] ⬇ Downloading: {track_name}")
            ydl_opts = {**base_ydl_opts,
                        "outtmpl": os.path.join(output_dir, safe_name.replace(".mp3", ".%(ext)s"))}

            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([f"ytsearch1:{track_name} {artist_name} official audio"])
            except Exception as e:
                log(f"❌ Failed: {track_name} | {e}")

        log(f"\n✨ Done! '{clean_name}' is ready.")
        root.after(0, lambda: _after_download(clean_name))

    except Exception as e:
        log(f"❌ Error: {e}")
    finally:
        root.after(0, lambda: btn_download.config(state="normal", text="＋ Add Playlist"))


def _after_download(clean_name):
    refresh_combo()
    playlist_combo.set(clean_name)
    load_playlist(clean_name)


def open_download_dialog():
    global FFMPEG_PATH

    url = simpledialog.askstring(
        "Add Spotify Playlist",
        "Paste your Spotify playlist link:",
        parent=root
    )
    if not url or not url.strip():
        return

    FFMPEG_PATH = ensure_ffmpeg()

    btn_download.config(state="disabled", text="Downloading...")
    log_box.config(state="normal")
    log_box.delete("1.0", tk.END)
    log_box.config(state="disabled")
    threading.Thread(target=download_playlist_thread, args=(url.strip(),), daemon=True).start()


def log(msg):
    def _append():
        log_box.config(state="normal")
        log_box.insert(tk.END, msg + "\n")
        log_box.see(tk.END)
        log_box.config(state="disabled")
    root.after(0, _append)


# ─── Playlist switching ──────────────────────────────────────────────────────

def load_playlist(name):
    global music_folder, songs, current_index, is_paused, total_length
    pygame.mixer.music.stop()
    is_paused     = False
    total_length  = 0
    current_index = 0

    music_folder = os.path.join(SONGS_ROOT, name)
    songs = [f for f in os.listdir(music_folder) if f.endswith(".mp3")]

    playlist_label.config(text=f"📁  {name}")
    song_label.config(text="No song playing")
    time_label.config(text="00:00 / 00:00")
    seek_bar.set(0)
    btn_pause.config(text="⏸")
    refresh_playlist()


def on_playlist_switch(event=None):
    sel = playlist_combo.get()
    if sel:
        load_playlist(sel)


# ─── Playback ────────────────────────────────────────────────────────────────

def play_song():
    global current_index, is_paused, total_length
    if not songs:
        return
    song = songs[current_index]
    path = os.path.join(music_folder, song)

    pygame.mixer.music.load(path)
    pygame.mixer.music.play()
    is_paused = False
    btn_pause.config(text="⏸")

    audio        = MP3(path)
    total_length = audio.info.length

    song_label.config(text=os.path.splitext(song)[0])
    playlist_box.selection_clear(0, tk.END)
    playlist_box.selection_set(current_index)
    playlist_box.see(current_index)
    seek_bar.config(to=total_length)
    update_ui()


def pause_resume():
    global is_paused
    if is_paused:
        pygame.mixer.music.unpause()
        is_paused = False
        btn_pause.config(text="⏸")
    else:
        pygame.mixer.music.pause()
        is_paused = True
        btn_pause.config(text="▶")


def next_song():
    global current_index
    current_index = (current_index + 1) % len(songs)
    play_song()


def prev_song():
    global current_index
    current_index = (current_index - 1) % len(songs)
    play_song()


def stop_song():
    pygame.mixer.music.stop()
    seek_bar.set(0)
    time_label.config(text="00:00 / " + format_time(total_length))


def shuffle_songs():
    global songs, current_index
    random.shuffle(songs)
    current_index = 0
    refresh_playlist()
    play_song()


def set_volume(val):
    pygame.mixer.music.set_volume(float(val))


# ─── Seek bar ────────────────────────────────────────────────────────────────

def on_seek_press(event):
    global seeking
    seeking = True


def on_seek_release(event):
    global seeking
    seeking = False
    new_pos = seek_bar.get()
    pygame.mixer.music.play(start=new_pos)
    if is_paused:
        pygame.mixer.music.pause()


# ─── UI update loop ──────────────────────────────────────────────────────────

def update_ui():
    if pygame.mixer.music.get_busy() or is_paused:
        if not seeking:
            current_time = pygame.mixer.music.get_pos() / 1000
            seek_bar.set(current_time)
            time_label.config(
                text=f"{format_time(current_time)} / {format_time(total_length)}"
            )
        root.after(500, update_ui)
    else:
        if not is_paused and total_length > 0:
            next_song()


# ─── Song list ───────────────────────────────────────────────────────────────

def refresh_playlist():
    playlist_box.delete(0, tk.END)
    for s in songs:
        playlist_box.insert(tk.END, os.path.splitext(s)[0])


def on_song_select(event):
    global current_index
    sel = playlist_box.curselection()
    if sel:
        current_index = sel[0]
        play_song()


# ─── GUI Setup ───────────────────────────────────────────────────────────────

BG      = "#121212"
SURFACE = "#1e1e1e"
ACCENT  = "#1DB954"
TEXT    = "#FFFFFF"
SUBTEXT = "#b3b3b3"
BTN_BG  = "#282828"

root = tk.Tk()
root.title("Music Player 🎵")
root.geometry("520x680")
root.configure(bg=BG)
root.resizable(False, False)

style = ttk.Style()
style.theme_use("clam")

# ── Playlist switcher ─────────────────────────────────────────────────────────
switch_frame = tk.Frame(root, bg=BG)
switch_frame.pack(fill="x", padx=20, pady=(16, 4))

tk.Label(switch_frame, text="SWITCH PLAYLIST", font=("Helvetica", 9, "bold"),
         fg=ACCENT, bg=BG).pack(anchor="w")

style.configure("PL.TCombobox",
                fieldbackground=SURFACE, background=SURFACE,
                foreground=TEXT, arrowcolor=ACCENT,
                selectbackground=SURFACE, selectforeground=TEXT)
style.map("PL.TCombobox",
          fieldbackground=[("readonly", SURFACE)],
          foreground=[("readonly", TEXT)],
          background=[("readonly", SURFACE)])

combo_frame = tk.Frame(switch_frame, bg=BG)
combo_frame.pack(fill="x", pady=(4, 0))

playlist_combo = ttk.Combobox(combo_frame, values=get_playlists(), state="readonly",
                               style="PL.TCombobox", font=("Helvetica", 11))
playlist_combo.pack(side="left", fill="x", expand=True)
playlist_combo.bind("<<ComboboxSelected>>", on_playlist_switch)

btn_download = tk.Button(combo_frame, text="＋ Add Playlist",
                         font=("Helvetica", 10, "bold"),
                         bg=ACCENT, fg=TEXT, relief="flat",
                         activebackground="#17a347", activeforeground=TEXT,
                         bd=0, padx=10, pady=4, cursor="hand2",
                         command=open_download_dialog)
btn_download.pack(side="left", padx=(8, 0))

# ── Now playing ───────────────────────────────────────────────────────────────
top_frame = tk.Frame(root, bg=BG)
top_frame.pack(fill="x", padx=20, pady=(12, 4))

tk.Label(top_frame, text="NOW PLAYING", font=("Helvetica", 9, "bold"),
         fg=ACCENT, bg=BG).pack(anchor="w")

playlist_label = tk.Label(top_frame, text="", font=("Helvetica", 9), fg=SUBTEXT, bg=BG)
playlist_label.pack(anchor="w")

song_label = tk.Label(top_frame, text="No song playing",
                      font=("Helvetica", 14, "bold"),
                      fg=TEXT, bg=BG, wraplength=460, anchor="w")
song_label.pack(anchor="w", pady=(2, 0))

# ── Seek bar ──────────────────────────────────────────────────────────────────
seek_frame = tk.Frame(root, bg=BG)
seek_frame.pack(fill="x", padx=20, pady=(10, 0))

style.configure("Seek.Horizontal.TScale",
                background=BG, troughcolor=SURFACE,
                sliderlength=14, sliderrelief="flat")
style.map("Seek.Horizontal.TScale", background=[("active", BG)])

seek_bar = ttk.Scale(seek_frame, from_=0, to=100, orient="horizontal",
                     style="Seek.Horizontal.TScale")
seek_bar.pack(fill="x")
seek_bar.bind("<ButtonPress-1>", on_seek_press)
seek_bar.bind("<ButtonRelease-1>", on_seek_release)

time_label = tk.Label(root, text="00:00 / 00:00",
                      font=("Helvetica", 10), fg=SUBTEXT, bg=BG)
time_label.pack(anchor="e", padx=20)

# ── Controls ──────────────────────────────────────────────────────────────────
ctrl_frame = tk.Frame(root, bg=BG)
ctrl_frame.pack(pady=12)

btn_cfg = dict(font=("Helvetica", 16), bg=BTN_BG, fg=TEXT,
               relief="flat", activebackground=ACCENT, activeforeground=TEXT,
               bd=0, padx=10, pady=6, cursor="hand2")

tk.Button(ctrl_frame, text="🔀", command=shuffle_songs, **btn_cfg).grid(row=0, column=0, padx=6)
tk.Button(ctrl_frame, text="⏮", command=prev_song,     **btn_cfg).grid(row=0, column=1, padx=6)

btn_pause = tk.Button(ctrl_frame, text="⏸", command=pause_resume,
                      font=("Helvetica", 20, "bold"),
                      bg=ACCENT, fg=TEXT, relief="flat",
                      activebackground="#17a347", activeforeground=TEXT,
                      bd=0, padx=14, pady=8, cursor="hand2")
btn_pause.grid(row=0, column=2, padx=6)

tk.Button(ctrl_frame, text="⏭", command=next_song, **btn_cfg).grid(row=0, column=3, padx=6)
tk.Button(ctrl_frame, text="⏹", command=stop_song, **btn_cfg).grid(row=0, column=4, padx=6)

# ── Volume ────────────────────────────────────────────────────────────────────
vol_frame = tk.Frame(root, bg=BG)
vol_frame.pack(fill="x", padx=20, pady=(4, 10))

tk.Label(vol_frame, text="🔈", font=("Helvetica", 12), fg=SUBTEXT, bg=BG).pack(side="left")

style.configure("Vol.Horizontal.TScale",
                background=BG, troughcolor=SURFACE,
                sliderlength=12, sliderrelief="flat")
vol_slider = ttk.Scale(vol_frame, from_=0, to=1, orient="horizontal",
                       style="Vol.Horizontal.TScale", command=set_volume)
vol_slider.set(1.0)
vol_slider.pack(side="left", fill="x", expand=True, padx=8)
tk.Label(vol_frame, text="🔊", font=("Helvetica", 12), fg=SUBTEXT, bg=BG).pack(side="left")

# ── Song list ─────────────────────────────────────────────────────────────────
pl_frame = tk.Frame(root, bg=BG)
pl_frame.pack(fill="both", expand=True, padx=20, pady=(0, 6))

tk.Label(pl_frame, text="SONGS", font=("Helvetica", 9, "bold"),
         fg=ACCENT, bg=BG).pack(anchor="w", pady=(0, 4))

scrollbar = tk.Scrollbar(pl_frame, bg=SURFACE, troughcolor=BG)
scrollbar.pack(side="right", fill="y")

playlist_box = tk.Listbox(pl_frame, bg=SURFACE, fg=TEXT,
                          selectbackground=ACCENT, selectforeground=TEXT,
                          font=("Helvetica", 11), relief="flat", bd=0,
                          activestyle="none", yscrollcommand=scrollbar.set)
playlist_box.pack(fill="both", expand=True)
scrollbar.config(command=playlist_box.yview)
playlist_box.bind("<<ListboxSelect>>", on_song_select)

# ── Download log ──────────────────────────────────────────────────────────────
log_frame = tk.Frame(root, bg=BG)
log_frame.pack(fill="x", padx=20, pady=(4, 14))

tk.Label(log_frame, text="DOWNLOAD LOG", font=("Helvetica", 9, "bold"),
         fg=ACCENT, bg=BG).pack(anchor="w", pady=(0, 3))

log_box = tk.Text(log_frame, height=5, bg=SURFACE, fg=SUBTEXT,
                  font=("Courier", 9), relief="flat", bd=0,
                  state="disabled", wrap="word")
log_box.pack(fill="x")

# ── Auto-load first playlist ──────────────────────────────────────────────────
playlists = get_playlists()
if playlists:
    playlist_combo.set(playlists[0])
    load_playlist(playlists[0])
else:
    log("No playlists yet. Click '＋ Add Playlist' to download one from Spotify.")

refresh_playlist()
root.mainloop()