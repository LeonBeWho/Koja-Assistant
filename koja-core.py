# Koja core

# libraries
import gc
import json
import os
import subprocess
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

# Path to your piper executable
PIPER_PATH = BASE_DIR / "piper" / "piper"
# Path to your voice model (.onnx file)
MODEL_PATH = BASE_DIR / "models" / "en_US-hfc_male-medium.onnx"

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


def speak(text):
    """Send text to Piper and play it through aplay."""
    text = str(text).strip()
    if not text:
        return

    print(f"Koja: {text}", flush=True)

    try:
        piper = subprocess.Popen(
            [str(PIPER_PATH), "--model", str(MODEL_PATH), "--output_raw", "--quiet"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        aplay = subprocess.Popen(
            ["aplay", "-r", "22050", "-f", "S16_LE", "-t", "raw"],
            stdin=piper.stdout,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        if piper.stdin:
            piper.stdin.write(text)
            piper.stdin.close()

        if piper.stdout:
            piper.stdout.close()

        aplay.wait()
        piper.wait()
    except FileNotFoundError as exc:
        print(f"TTS playback failed; missing executable: {exc}", flush=True)
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
            subprocess.Popen(["spotify"])
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


# --- THE HANDS ---
def execute_command(command_text):
    """Map voice commands to system actions. Returns True if handled."""
    cmd = command_text.lower()

    if execute_spotify_command(command_text):
        return True

    if "open terminal" in cmd:
        speak("Opening terminal.")
        subprocess.Popen(["kitty"])
        return True

    if "open browser" in cmd:
        speak("Opening web browser.")
        subprocess.Popen(["flatpak", "run", "com.opera.GX"])
        return True

    if "take a screenshot" in cmd:
        speak("Taking a screenshot.")
        subprocess.run(["gnome-screenshot", "-f", str(BASE_DIR / "koja_snap.png")])
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
        res = subprocess.check_output(["hostnamectl"]).decode("utf-8").split("\n")[0]
        speak(f"System status: {res}")
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
