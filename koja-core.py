# Koja core

# libraries
import gc
import json
import os
import platform
import shutil
import subprocess
import tempfile
import webbrowser
from datetime import datetime
from pathlib import Path

import speech_recognition as sr
from llama_index.core import Settings, SimpleDirectoryReader, VectorStoreIndex
from llama_index.embeddings.huggingface import HuggingFaceEmbedding
from llama_index.llms.ollama import Ollama

try:
    import spotipy
    from spotipy.oauth2 import SpotifyOAuth
except ImportError:
    spotipy = None
    SpotifyOAuth = None

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

os.environ["OLLAMA_KEEP_ALIVE"] = "0s"

# --- PATHS ---
BASE_DIR = Path(__file__).resolve().parent
if load_dotenv is not None:
    load_dotenv(BASE_DIR / ".env")
OPENCLAW_WORKSPACE = Path.home() / ".openclaw" / "workspace"
DATA_DIR = BASE_DIR / "data"

# --- CONFIGURATION ---
USE_OPENCLAW_AGENT = True
OPENCLAW_SESSION_ID = "koja-voice"
OPENCLAW_TIMEOUT_SECONDS = 600

# Listening tuning. Increase PAUSE_THRESHOLD if Koja cuts you off mid-sentence.
LISTEN_PAUSE_THRESHOLD = 2.5
LISTEN_NON_SPEAKING_DURATION = 1.0
LISTEN_PHRASE_TIME_LIMIT = 30
LISTEN_TIMEOUT = None
AMBIENT_NOISE_DURATION = 0.8

SYSTEM = platform.system().lower()
IS_WINDOWS = SYSTEM == "windows"
IS_MACOS = SYSTEM == "darwin"
IS_LINUX = SYSTEM == "linux"

# Path to your piper executable. Can be overridden with KOJA_PIPER_PATH.
def default_piper_path():
    executable = "piper.exe" if IS_WINDOWS else "piper"
    return BASE_DIR / "piper" / executable


PIPER_PATH = Path(os.environ.get("KOJA_PIPER_PATH", default_piper_path()))
# Path to your voice model (.onnx file). Can be overridden with KOJA_MODEL_PATH.
MODEL_PATH = Path(os.environ.get("KOJA_MODEL_PATH", BASE_DIR / "models" / "en_US-hfc_male-medium.onnx"))

# Spotify API configuration. Set these in your shell before running Koja:
# export SPOTIPY_CLIENT_ID="..."
# export SPOTIPY_CLIENT_SECRET="..."
# export SPOTIPY_REDIRECT_URI="http://127.0.0.1:8888/callback"
SPOTIFY_SCOPES = "user-read-playback-state user-modify-playback-state user-read-currently-playing"


def load_koja_context():
    """Load Koja's local identity/persona notes from the OpenClaw workspace."""
    context_files = [
        OPENCLAW_WORKSPACE / "IDENTITY.md",
        OPENCLAW_WORKSPACE / "SOUL.md",
        OPENCLAW_WORKSPACE / "USER.md",
    ]

    chunks = []
    for path in context_files:
        if path.exists():
            chunks.append(f"\n--- {path.name} ---\n{path.read_text(encoding='utf-8', errors='ignore')}")

    if not chunks:
        return ""

    return "\n".join(chunks)


KOJA_CONTEXT = load_koja_context()
SYSTEM_PROMPT = f"""
You are Koja, L's local voice assistant.
You are warm, capable, concise, and quietly supportive.
You speak naturally, not like a corporate chatbot.
Keep spoken answers brief unless L asks for detail.
If you do not know something, say so plainly.

Local persona notes:
{KOJA_CONTEXT}
""".strip()

# --- BRAIN SETUP (LlamaIndex) ---
print("Initializing Koja brain...", flush=True)
Settings.llm = Ollama(model="llama3.2:1b", request_timeout=90.0)
Settings.embed_model = HuggingFaceEmbedding(model_name="BAAI/bge-small-en-v1.5")

# Load files from data folder for retrieval/context, if present.
if DATA_DIR.exists():
    documents = SimpleDirectoryReader(str(DATA_DIR)).load_data()
else:
    print(f"Warning: data folder not found at {DATA_DIR}. Starting with empty context.", flush=True)
    documents = []

index = VectorStoreIndex.from_documents(documents)
chat_engine = index.as_chat_engine(
    chat_mode="context",
    system_prompt=SYSTEM_PROMPT,
    similarity_top_k=2,
)


def play_audio_file(path):
    """Play a wav file using the host OS default simple player."""
    if IS_MACOS and shutil.which("afplay"):
        subprocess.run(["afplay", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return

    if IS_LINUX:
        for player in (["aplay", str(path)], ["paplay", str(path)], ["pw-play", str(path)]):
            if shutil.which(player[0]):
                subprocess.run(player, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return

    if IS_WINDOWS:
        try:
            import winsound

            winsound.PlaySound(str(path), winsound.SND_FILENAME)
            return
        except Exception:
            pass

    # Last resort: ask the OS to open the file in its default audio app.
    open_path(path)


def open_path(path):
    """Open a local file/folder/URL in the platform default app."""
    target = str(path)
    try:
        if IS_WINDOWS:
            os.startfile(target)  # type: ignore[attr-defined]
        elif IS_MACOS:
            subprocess.Popen(["open", target])
        else:
            subprocess.Popen(["xdg-open", target])
    except Exception as exc:
        print(f"Could not open {target}: {exc}", flush=True)


def speak(text):
    """Send text to Piper and play it cross-platform."""
    text = str(text).strip()
    if not text:
        return

    print(f"Koja: {text}", flush=True)

    try:
        if not PIPER_PATH.exists():
            raise FileNotFoundError(f"Piper executable not found at {PIPER_PATH}")
        if not MODEL_PATH.exists():
            raise FileNotFoundError(f"Piper voice model not found at {MODEL_PATH}")

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as wav_file:
            wav_path = Path(wav_file.name)

        try:
            subprocess.run(
                [str(PIPER_PATH), "--model", str(MODEL_PATH), "--output_file", str(wav_path), "--quiet"],
                input=text,
                text=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
            )
            play_audio_file(wav_path)
        finally:
            try:
                wav_path.unlink(missing_ok=True)
            except Exception:
                pass

    except FileNotFoundError as exc:
        print(f"TTS playback failed; missing file: {exc}", flush=True)
    except Exception as exc:
        print(f"TTS playback failed: {exc}", flush=True)


def listen():
    """Listen for a voice command and return it as lowercase text."""
    recognizer = sr.Recognizer()

    # These make Koja less eager to stop listening during natural pauses.
    recognizer.pause_threshold = LISTEN_PAUSE_THRESHOLD
    recognizer.non_speaking_duration = LISTEN_NON_SPEAKING_DURATION
    recognizer.dynamic_energy_threshold = True

    try:
        with sr.Microphone() as source:
            print("\nListening...", flush=True)
            recognizer.adjust_for_ambient_noise(source, duration=AMBIENT_NOISE_DURATION)
            print("Speak now. I’ll wait through pauses.", flush=True)
            audio = recognizer.listen(
                source,
                timeout=LISTEN_TIMEOUT,
                phrase_time_limit=LISTEN_PHRASE_TIME_LIMIT,
            )

        command = recognizer.recognize_google(audio)
        print(f"You: {command}", flush=True)
        return command.lower()
    except sr.UnknownValueError:
        return ""
    except sr.RequestError:
        print("Network error with speech recognition.", flush=True)
        return ""
    except Exception as exc:
        print(f"Listening failed: {exc}", flush=True)
        return ""


# --- SPOTIFY ---
def get_spotify_client():
    """Return an authenticated Spotify client, or None if not configured."""
    if spotipy is None or SpotifyOAuth is None:
        return None

    required = ["SPOTIPY_CLIENT_ID", "SPOTIPY_CLIENT_SECRET", "SPOTIPY_REDIRECT_URI"]
    if any(not os.environ.get(name) for name in required):
        return None

    return spotipy.Spotify(
        auth_manager=SpotifyOAuth(
            scope=SPOTIFY_SCOPES,
            cache_path=str(BASE_DIR / ".spotify-token-cache"),
            open_browser=True,
        )
    )


def spotify_ready_message():
    if spotipy is None:
        return "Spotify support needs the Python package spotipy installed. Run: python -m pip install spotipy"

    missing = [
        name
        for name in ["SPOTIPY_CLIENT_ID", "SPOTIPY_CLIENT_SECRET", "SPOTIPY_REDIRECT_URI"]
        if not os.environ.get(name)
    ]
    if missing:
        return "Spotify API needs credentials first. Set SPOTIPY_CLIENT_ID, SPOTIPY_CLIENT_SECRET, and SPOTIPY_REDIRECT_URI."

    return "Spotify API is configured."


def active_spotify_device(sp):
    devices = sp.devices().get("devices", [])
    if not devices:
        return None

    active = next((device for device in devices if device.get("is_active")), None)
    return active or devices[0]


def execute_spotify_command(command_text):
    """Handle Spotify voice commands. Returns True if command was Spotify-related."""
    cmd = command_text.lower().strip()

    spotify_phrases = (
        "spotify",
        "play music",
        "pause music",
        "resume music",
        "next song",
        "previous song",
        "skip song",
        "what's playing",
        "what is playing",
    )
    if not any(phrase in cmd for phrase in spotify_phrases):
        return False

    if "spotify status" in cmd or "spotify setup" in cmd:
        speak(spotify_ready_message())
        return True

    sp = get_spotify_client()
    if sp is None:
        # Fallback: at least open Spotify if API credentials are not ready yet.
        if "play music" in cmd or "open spotify" in cmd or cmd == "spotify":
            speak("Opening Spotify. The API is not configured yet, so I can only launch it for now.")
            launch_spotify_app()
        else:
            speak(spotify_ready_message())
        return True

    try:
        device = active_spotify_device(sp)
        device_id = device.get("id") if device else None

        if "pause" in cmd:
            sp.pause_playback(device_id=device_id)
            speak("Paused Spotify.")
            return True

        if "resume" in cmd or cmd == "play music" or "continue music" in cmd:
            sp.start_playback(device_id=device_id)
            speak("Resuming Spotify.")
            return True

        if "next" in cmd or "skip" in cmd:
            sp.next_track(device_id=device_id)
            speak("Skipping.")
            return True

        if "previous" in cmd or "back" in cmd:
            sp.previous_track(device_id=device_id)
            speak("Going back.")
            return True

        if "what's playing" in cmd or "what is playing" in cmd:
            current = sp.current_playback()
            item = current.get("item") if current else None
            if not item:
                speak("Nothing is playing on Spotify right now.")
                return True
            artists = ", ".join(artist["name"] for artist in item.get("artists", []))
            speak(f"You're listening to {item.get('name')} by {artists}.")
            return True

        # Search/play: "play X on spotify" or "spotify play X"
        query = cmd
        for phrase in ["play", "on spotify", "spotify"]:
            query = query.replace(phrase, " ")
        query = " ".join(query.split())

        if query:
            results = sp.search(q=query, type="track", limit=1)
            tracks = results.get("tracks", {}).get("items", [])
            if not tracks:
                speak(f"I couldn't find {query} on Spotify.")
                return True

            track = tracks[0]
            sp.start_playback(device_id=device_id, uris=[track["uri"]])
            artists = ", ".join(artist["name"] for artist in track.get("artists", []))
            speak(f"Playing {track['name']} by {artists}.")
            return True

        speak("What would you like me to play on Spotify?")
        return True

    except Exception as exc:
        speak(f"Spotify had a problem: {exc}")
        return True


# --- CROSS-PLATFORM SYSTEM HELPERS ---
def launch_terminal():
    """Open a terminal on Linux, macOS, or Windows."""
    if IS_WINDOWS:
        subprocess.Popen(["cmd.exe"])
        return
    if IS_MACOS:
        subprocess.Popen(["open", "-a", "Terminal"])
        return

    for terminal in ("kitty", "gnome-terminal", "konsole", "xfce4-terminal", "xterm"):
        if shutil.which(terminal):
            subprocess.Popen([terminal])
            return
    raise RuntimeError("No supported terminal emulator found")


def launch_browser():
    """Open the default browser cross-platform."""
    webbrowser.open("https://www.google.com")


def launch_spotify_app():
    """Open Spotify if installed; otherwise fall back to the web player."""
    if IS_WINDOWS:
        try:
            os.startfile("spotify:")  # type: ignore[attr-defined]
            return
        except Exception:
            pass
    elif IS_MACOS:
        try:
            subprocess.Popen(["open", "-a", "Spotify"])
            return
        except Exception:
            pass
    else:
        for command in (["spotify"], ["flatpak", "run", "com.spotify.Client"]):
            if shutil.which(command[0]):
                subprocess.Popen(command)
                return

    webbrowser.open("https://open.spotify.com")


def take_screenshot(path):
    """Take a screenshot where possible. Returns True if captured."""
    path = Path(path)

    if IS_MACOS and shutil.which("screencapture"):
        subprocess.run(["screencapture", str(path)], check=True)
        return True

    if IS_LINUX:
        commands = [
            ["gnome-screenshot", "-f", str(path)],
            ["spectacle", "-b", "-n", "-o", str(path)],
            ["scrot", str(path)],
        ]
        for command in commands:
            if shutil.which(command[0]):
                subprocess.run(command, check=True)
                return True

    if IS_WINDOWS:
        try:
            from PIL import ImageGrab

            image = ImageGrab.grab()
            image.save(path)
            return True
        except Exception:
            pass

    return False


def system_summary():
    """Return a short cross-platform system summary."""
    return f"{platform.system()} {platform.release()} on {platform.machine()}"


# --- THE HANDS ---
def execute_command(command_text):
    """Map voice commands to system actions. Returns True if handled."""
    cmd = command_text.lower()

    if execute_spotify_command(command_text):
        return True

    if "open terminal" in cmd:
        speak("Opening terminal.")
        try:
            launch_terminal()
        except Exception as exc:
            speak(f"I couldn't open a terminal: {exc}")
        return True

    if "open browser" in cmd:
        speak("Opening web browser.")
        launch_browser()
        return True

    if "take a screenshot" in cmd:
        screenshot_path = BASE_DIR / "koja_snap.png"
        speak("Taking a screenshot.")
        if take_screenshot(screenshot_path):
            speak(f"Screenshot saved to {screenshot_path.name}.")
        else:
            speak("I couldn't take a screenshot on this operating system yet.")
        return True

    if "what's the time" in cmd or "what is the time" in cmd:
        now = datetime.now().strftime("%H:%M")
        speak(f"The current time is {now}.")
        return True

    if "what's the date" in cmd or "what is the date" in cmd:
        today = datetime.now().strftime("%B %d, %Y")
        speak(f"Today's date is {today}.")
        return True

    if "how is my system" in cmd or "how is my computer" in cmd:
        speak("Checking system status.")
        speak(f"System status: {system_summary()}")
        return True

    return False


def ask_openclaw_koja(query):
    """Ask the real OpenClaw agent so Koja can use workspace memory and tools."""
    prompt = (
        "You are Koja speaking through a local TTS voice bridge. "
        "Reply naturally and concisely because your answer will be spoken aloud. "
        "If tools are useful, use them. User said: "
        f"{query}"
    )

    result = subprocess.run(
        [
            "openclaw",
            "agent",
            "--local",
            "--session-id",
            OPENCLAW_SESSION_ID,
            "--message",
            prompt,
            "--json",
            "--timeout",
            str(OPENCLAW_TIMEOUT_SECONDS),
        ],
        cwd=str(OPENCLAW_WORKSPACE),
        text=True,
        capture_output=True,
        timeout=OPENCLAW_TIMEOUT_SECONDS + 30,
    )

    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "OpenClaw agent failed").strip())

    data = json.loads(result.stdout)
    payloads = data.get("payloads") or []
    spoken_parts = [payload.get("text", "").strip() for payload in payloads if payload.get("text")]
    reply = "\n".join(part for part in spoken_parts if part).strip()

    if not reply:
        raise RuntimeError("OpenClaw returned no speakable text")

    return reply


def ask_local_koja(query):
    """Ask the local LLM-backed Koja brain once as a fallback."""
    response = chat_engine.chat(query)
    return str(response)


def ask_koja(query):
    """Ask Koja, preferring the real OpenClaw tool-enabled agent."""
    print("[THINKING...]", flush=True)

    if USE_OPENCLAW_AGENT:
        try:
            return ask_openclaw_koja(query)
        except Exception as exc:
            print(f"OpenClaw agent failed, falling back to local brain: {exc}", flush=True)

    return ask_local_koja(query)


# --- MAIN LOOP ---
if __name__ == "__main__":
    speak("Systems initialized. I am Koja. How can I help, L?")

    while True:
        query = listen()

        if not query:
            continue

        if "go to sleep" in query or "exit" in query or "goodbye" in query:
            speak("Understood. Powering down. Goodbye, L.")
            break

        if execute_command(query):
            continue

        response = ask_koja(query)
        speak(response)
        gc.collect()
