"""Koja desktop app.

A Jarvis-inspired, neon HUD wrapper around koja-core.py.
The core module is loaded lazily so the UI appears before the AI stack finishes
initialising.
"""

from __future__ import annotations

import importlib.util
import queue
import threading
import time
from pathlib import Path
import tkinter as tk
from tkinter import scrolledtext, ttk

BASE_DIR = Path(__file__).resolve().parent
CORE_PATH = BASE_DIR / "koja-core.py"


class KojaApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Koja Core // Voice HUD")
        self.geometry("980x680")
        self.minsize(820, 560)
        self.configure(bg="#050b14")

        self.core = None
        self.user_name = "User"
        self.core_lock = threading.Lock()
        self.listening = False
        self.worker = None
        self.ui_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.pulse_angle = 0

        self._setup_style()
        self._build_ui()
        self._drain_ui_queue()
        self._animate_hud()

    def _setup_style(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(
            "Hud.TButton",
            background="#082136",
            foreground="#9eefff",
            bordercolor="#12d9ff",
            focusthickness=2,
            focuscolor="#12d9ff",
            padding=(14, 8),
            font=("DejaVu Sans", 10, "bold"),
        )
        style.map(
            "Hud.TButton",
            background=[("active", "#0d3b5c"), ("disabled", "#101820")],
            foreground=[("disabled", "#54707a")],
        )
        style.configure(
            "Danger.TButton",
            background="#3a1018",
            foreground="#ff9eb1",
            bordercolor="#ff4068",
            padding=(14, 8),
            font=("DejaVu Sans", 10, "bold"),
        )

    def _build_ui(self):
        header = tk.Frame(self, bg="#050b14")
        header.pack(fill="x", padx=24, pady=(18, 8))

        title = tk.Label(
            header,
            text="KOJA CORE",
            fg="#8ff7ff",
            bg="#050b14",
            font=("DejaVu Sans", 28, "bold"),
        )
        title.pack(side="left")

        subtitle = tk.Label(
            header,
            text="// LOCAL VOICE ASSISTANT HUD",
            fg="#3bb9d6",
            bg="#050b14",
            font=("DejaVu Sans Mono", 11),
        )
        subtitle.pack(side="left", padx=(16, 0), pady=(10, 0))

        self.status_var = tk.StringVar(value="STANDBY // core not loaded")
        status = tk.Label(
            header,
            textvariable=self.status_var,
            fg="#ffdf6e",
            bg="#050b14",
            font=("DejaVu Sans Mono", 11, "bold"),
        )
        status.pack(side="right", pady=(10, 0))

        body = tk.Frame(self, bg="#050b14")
        body.pack(fill="both", expand=True, padx=24, pady=12)

        left = tk.Frame(body, bg="#07111f", highlightbackground="#0e6f8f", highlightthickness=1)
        left.pack(side="left", fill="both", expand=True, padx=(0, 12))

        self.canvas = tk.Canvas(left, bg="#07111f", height=260, highlightthickness=0)
        self.canvas.pack(fill="x", padx=14, pady=14)

        self.transcript = scrolledtext.ScrolledText(
            left,
            bg="#02070d",
            fg="#baf7ff",
            insertbackground="#8ff7ff",
            relief="flat",
            wrap="word",
            font=("DejaVu Sans Mono", 10),
            height=14,
        )
        self.transcript.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        self.transcript.configure(state="disabled")

        right = tk.Frame(body, bg="#07111f", width=280, highlightbackground="#0e6f8f", highlightthickness=1)
        right.pack(side="right", fill="y")
        right.pack_propagate(False)

        tk.Label(
            right,
            text="CONTROL PANEL",
            fg="#8ff7ff",
            bg="#07111f",
            font=("DejaVu Sans Mono", 14, "bold"),
        ).pack(anchor="w", padx=18, pady=(18, 10))

        self.start_btn = ttk.Button(right, text="START VOICE LOOP", style="Hud.TButton", command=self.start_voice_loop)
        self.start_btn.pack(fill="x", padx=18, pady=6)

        self.stop_btn = ttk.Button(right, text="STOP LISTENING", style="Danger.TButton", command=self.stop_voice_loop)
        self.stop_btn.pack(fill="x", padx=18, pady=6)

        self.load_btn = ttk.Button(right, text="LOAD CORE", style="Hud.TButton", command=self.load_core_async)
        self.load_btn.pack(fill="x", padx=18, pady=6)

        tk.Label(
            right,
            text="TEXT OVERRIDE",
            fg="#3bb9d6",
            bg="#07111f",
            font=("DejaVu Sans Mono", 11, "bold"),
        ).pack(anchor="w", padx=18, pady=(24, 8))

        self.entry = tk.Text(
            right,
            height=5,
            bg="#02070d",
            fg="#baf7ff",
            insertbackground="#8ff7ff",
            relief="flat",
            wrap="word",
            font=("DejaVu Sans", 10),
        )
        self.entry.pack(fill="x", padx=18, pady=(0, 8))
        self.entry.bind("<Control-Return>", lambda _event: self.ask_text())

        ttk.Button(right, text="ASK KOJA", style="Hud.TButton", command=self.ask_text).pack(fill="x", padx=18, pady=6)

        tk.Label(
            right,
            text="Tip: Ctrl+Enter sends text.\nVoice stop takes effect after\nthe current listen finishes.",
            fg="#6caec0",
            bg="#07111f",
            justify="left",
            font=("DejaVu Sans Mono", 9),
        ).pack(anchor="w", padx=18, pady=(22, 0))

        self.log("SYSTEM", "HUD online. Load core, then start voice loop or type a message.")

    def _load_core(self):
        with self.core_lock:
            if self.core is not None:
                return self.core

            if not CORE_PATH.exists():
                raise FileNotFoundError(f"Missing core file: {CORE_PATH}")

            spec = importlib.util.spec_from_file_location("koja_core_runtime", CORE_PATH)
            if spec is None or spec.loader is None:
                raise RuntimeError("Could not load Koja core module")

            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self.user_name = getattr(module, "USER_NAME", "User") or "User"
            self.core = module
            return module

    def load_core_async(self):
        def job():
            self.ui_queue.put(("status", "LOADING // initialising Koja brain"))
            try:
                self._load_core()
                self.ui_queue.put(("status", "READY // Koja core loaded"))
                self.ui_queue.put(("log", ("SYSTEM", "Koja core loaded.")))
            except Exception as exc:
                self.ui_queue.put(("status", "ERROR // core load failed"))
                self.ui_queue.put(("log", ("ERROR", str(exc))))

        threading.Thread(target=job, daemon=True).start()

    def start_voice_loop(self):
        if self.listening:
            return
        self.listening = True
        self.worker = threading.Thread(target=self._voice_loop, daemon=True)
        self.worker.start()
        self.status_var.set("LISTENING // voice loop active")
        self.log("SYSTEM", "Voice loop started.")

    def stop_voice_loop(self):
        self.listening = False
        self.status_var.set("STOPPING // waiting for current listen")
        self.log("SYSTEM", "Voice loop stop requested.")

    def _voice_loop(self):
        try:
            core = self._load_core()
            self.ui_queue.put(("status", "LISTENING // voice loop active"))
        except Exception as exc:
            self.ui_queue.put(("status", "ERROR // core load failed"))
            self.ui_queue.put(("log", ("ERROR", str(exc))))
            self.listening = False
            return

        while self.listening:
            query = core.listen()
            if not self.listening:
                break
            if not query:
                continue

            self.ui_queue.put(("log", (self.user_name, query)))

            if any(phrase in query for phrase in ("go to sleep", "exit", "goodbye")):
                core.speak(f"Understood. Powering down. Goodbye, {self.user_name}.")
                self.listening = False
                break

            if core.execute_command(query):
                continue

            self.ui_queue.put(("status", "THINKING // asking Koja"))
            response = core.ask_koja(query)
            self.ui_queue.put(("log", ("KOJA", response)))
            core.speak(response)
            self.ui_queue.put(("status", "LISTENING // voice loop active"))

        self.ui_queue.put(("status", "STANDBY // voice loop stopped"))
        self.ui_queue.put(("log", ("SYSTEM", "Voice loop stopped.")))

    def ask_text(self):
        text = self.entry.get("1.0", "end").strip()
        if not text:
            return
        self.entry.delete("1.0", "end")
        self.log(self.user_name, text)

        def job():
            try:
                core = self._load_core()
                self.ui_queue.put(("status", "THINKING // asking Koja"))
                if core.execute_command(text):
                    self.ui_queue.put(("status", "READY // command handled"))
                    return
                response = core.ask_koja(text)
                self.ui_queue.put(("log", ("KOJA", response)))
                core.speak(response)
                self.ui_queue.put(("status", "READY // Koja core loaded"))
            except Exception as exc:
                self.ui_queue.put(("status", "ERROR // request failed"))
                self.ui_queue.put(("log", ("ERROR", str(exc))))

        threading.Thread(target=job, daemon=True).start()

    def log(self, speaker: str, message: str):
        self.transcript.configure(state="normal")
        self.transcript.insert("end", f"[{speaker}] {message}\n\n")
        self.transcript.see("end")
        self.transcript.configure(state="disabled")

    def _drain_ui_queue(self):
        try:
            while True:
                kind, payload = self.ui_queue.get_nowait()
                if kind == "status":
                    self.status_var.set(str(payload))
                elif kind == "log":
                    speaker, message = payload
                    self.log(str(speaker), str(message))
        except queue.Empty:
            pass
        self.after(100, self._drain_ui_queue)

    def _animate_hud(self):
        canvas = self.canvas
        canvas.delete("all")
        w = max(canvas.winfo_width(), 400)
        h = max(canvas.winfo_height(), 220)
        cx, cy = w // 2, h // 2
        r = min(w, h) // 3

        # Grid lines
        for x in range(0, w, 36):
            canvas.create_line(x, 0, x, h, fill="#08263a")
        for y in range(0, h, 36):
            canvas.create_line(0, y, w, y, fill="#08263a")

        # Reticle rings
        for i, color in enumerate(("#0e6f8f", "#12d9ff", "#8ff7ff")):
            pad = i * 22
            canvas.create_oval(cx - r - pad, cy - r - pad, cx + r + pad, cy + r + pad, outline=color, width=1)

        # Animated arcs
        angle = self.pulse_angle
        canvas.create_arc(cx - r - 34, cy - r - 34, cx + r + 34, cy + r + 34, start=angle, extent=80, outline="#12d9ff", width=4, style="arc")
        canvas.create_arc(cx - r + 10, cy - r + 10, cx + r - 10, cy + r - 10, start=-angle * 1.4, extent=120, outline="#ffdf6e", width=2, style="arc")
        canvas.create_text(cx, cy - 8, text="KOJA", fill="#baf7ff", font=("DejaVu Sans Mono", 26, "bold"))
        canvas.create_text(cx, cy + 24, text="VOICE LINK ACTIVE", fill="#3bb9d6", font=("DejaVu Sans Mono", 10))

        self.pulse_angle = (self.pulse_angle + 4) % 360
        self.after(50, self._animate_hud)


if __name__ == "__main__":
    app = KojaApp()
    app.mainloop()
