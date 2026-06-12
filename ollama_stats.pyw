"""OllamaTicker v2 — live Ollama stats widget"""

import sys, json, time, threading, random
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
import tkinter as tk
import tkinter.font as tkFont
from tkinter import Menu
import customtkinter as ctk
import requests

# ── Constants ─────────────────────────────────────────────────────────────────
CONFIG_PATH = Path(sys.argv[0]).parent / "config.json"
POLL_SECS   = 2
PANEL_W     = 300
PANEL_MIN_W = 200
BASE_H      = 260
MIN_H       = 160
SNAP_PX     = 18
HISTORY     = 80

DEFAULT_SERVERS = [
    {"name": "Ollama", "type": "ollama", "url": "http://localhost:11434"},
]

DEFAULT_CFG = {
    "theme": "Midnight", "always_on_top": True,
    "x": None, "y": None, "w": None, "h": BASE_H,
    "opacity": 1.0, "mini": False, "snap": True,
    "font_scale": 1.0, "font_family": "Consolas",
    "servers": DEFAULT_SERVERS,
}

FONT_FAMILIES = [
    "Consolas", "Cascadia Code", "JetBrains Mono",
    "Lucida Console", "Courier New", "Segoe UI",
]

FONT_SIZES = [
    ("Small",   0.75),
    ("Normal",  1.0),
    ("Large",   1.25),
    ("X-Large", 1.5),
    ("Huge",    1.75),
]

# ── Themes ────────────────────────────────────────────────────────────────────
THEMES = {
    "Midnight":  dict(bg="#0d1117", card="#161b22", bar="#080c11",
                      accent="#00d4ff", text="#e6edf3", sub="#8b949e",
                      ok="#3fb950", warn="#d29922", err="#f85149"),
    "Dracula":   dict(bg="#282a36", card="#313442", bar="#21222c",
                      accent="#ff79c6", text="#f8f8f2", sub="#6272a4",
                      ok="#50fa7b", warn="#f1fa8c", err="#ff5555"),
    "Nord":      dict(bg="#2e3440", card="#3b4252", bar="#242933",
                      accent="#88c0d0", text="#eceff4", sub="#616e88",
                      ok="#a3be8c", warn="#ebcb8b", err="#bf616a"),
    "Monokai":   dict(bg="#272822", card="#2f3129", bar="#1e1f1c",
                      accent="#a6e22e", text="#f8f8f2", sub="#75715e",
                      ok="#a6e22e", warn="#e6db74", err="#f92672"),
    "Synthwave": dict(bg="#0d0d1a", card="#150d2e", bar="#0a0a14",
                      accent="#ff00ff", text="#e0d7ff", sub="#7b6fa0",
                      ok="#00ff88", warn="#ffcc00", err="#ff003c"),
    "Solarized": dict(bg="#002b36", card="#073642", bar="#001e25",
                      accent="#b58900", text="#fdf6e3", sub="#657b83",
                      ok="#859900", warn="#b58900", err="#dc322f"),
}

# ── Helpers ───────────────────────────────────────────────────────────────────
def load_cfg():
    try:
        cfg = {**DEFAULT_CFG, **json.loads(CONFIG_PATH.read_text())}
    except Exception:
        cfg = DEFAULT_CFG.copy()
    if not cfg.get("servers"):
        cfg["servers"] = [s.copy() for s in DEFAULT_SERVERS]
    # argv override: first ollama server's URL (for run_local.bat / run_remote.bat)
    if len(sys.argv) > 1:
        url = sys.argv[1].rstrip("/")
        for s in cfg["servers"]:
            if s.get("type", "ollama") == "ollama":
                s["url"] = url
                break
    return cfg

def save_cfg(cfg):
    try:
        CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
    except Exception:
        pass

def fmt_gb(b):
    return f"{b / 1_073_741_824:.2f} GB"

def fmt_countdown(iso):
    try:
        exp  = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        secs = int((exp - datetime.now(timezone.utc)).total_seconds())
        if secs <= 0:
            return "Expired"
        m, s = divmod(secs, 60)
        h, m = divmod(m, 60)
        return f"{h}h {m}m" if h else f"{m}m {s:02d}s"
    except Exception:
        return "—"

def fmt_val(key, val):
    if not val:
        return "—"
    if key == "vram":
        return fmt_gb(val)
    if key == "context":
        return f"{val:,} tokens"
    if key == "expires":
        return fmt_countdown(val)
    return str(val)

def lighten(hex_color, amt=45):
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"#{min(255,r+amt):02x}{min(255,g+amt):02x}{min(255,b+amt):02x}"

def dim(hex_color, factor=0.25):
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"#{int(r*factor):02x}{int(g*factor):02x}{int(b*factor):02x}"

# ── Tooltip ───────────────────────────────────────────────────────────────────
class Tooltip:
    def __init__(self, widget, text_fn, bg="#1c2128", fg="#e6edf3"):
        self._widget = widget
        self._fn     = text_fn
        self._tip    = None
        self._bg, self._fg = bg, fg
        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")

    def _show(self, e):
        text = self._fn()
        if not text:
            return
        x = self._widget.winfo_rootx()
        y = self._widget.winfo_rooty() + self._widget.winfo_height() + 3
        self._tip = tk.Toplevel(self._widget)
        self._tip.wm_overrideredirect(True)
        self._tip.wm_geometry(f"+{x}+{y}")
        tk.Label(self._tip, text=text, bg=self._bg, fg=self._fg,
                 relief="flat", padx=7, pady=3,
                 font=("Consolas", 8)).pack()

    def _hide(self, e):
        if self._tip:
            self._tip.destroy()
            self._tip = None

# ── Backend ───────────────────────────────────────────────────────────────────
class Backend:
    """Polls one server (Ollama or an OpenAI-compatible API) for stats."""

    EMPTY_STATE = {"status": "connecting", "model": None, "vram": None,
                    "context": None, "expires": None, "details": None}

    def __init__(self, cfg):
        self.name    = cfg.get("name", "Server")
        self.url     = cfg.get("url", "").rstrip("/")
        self.kind    = cfg.get("type", "ollama")
        self.api_key = cfg.get("api_key")
        self.headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        self.lock    = threading.Lock()
        self.state   = self.EMPTY_STATE.copy()
        self.seen    = None
        self.hist    = deque(maxlen=HISTORY)
        self.full_model = ""

    def start(self):
        threading.Thread(target=self._poll_loop, daemon=True).start()

    def _set_state(self, **kwargs):
        with self.lock:
            self.state = {**self.EMPTY_STATE, **kwargs}

    def _poll_loop(self):
        while True:
            try:
                if self.kind == "openai":
                    self._poll_openai()
                else:
                    self._poll_ollama()
            except requests.ConnectionError:
                self._set_state(status="offline")
                self.seen = None
            except Exception:
                pass
            time.sleep(POLL_SECS)

    def _poll_ollama(self):
        r = requests.get(f"{self.url}/api/ps", timeout=3)
        r.raise_for_status()
        models = r.json().get("models", [])

        if not models:
            self._set_state(status="idle")
            self.seen = None
            return

        m       = models[0]
        name    = m.get("name", "unknown")
        vram    = m.get("size_vram") or m.get("size", 0)
        expires = m.get("expires_at", "")
        details = m.get("details", {})

        if name != self.seen:
            ctx = self._fetch_context(name)
            self.seen = name
        else:
            with self.lock:
                ctx = self.state.get("context")

        self._set_state(status="active", model=name, vram=vram,
                         context=ctx, expires=expires, details=details)

    def _fetch_context(self, name):
        try:
            r = requests.post(f"{self.url}/api/show",
                              json={"name": name}, timeout=5)
            r.raise_for_status()
            for k, v in r.json().get("model_info", {}).items():
                if "context_length" in k:
                    return int(v)
        except Exception:
            pass
        return None

    def _poll_openai(self):
        r = requests.get(f"{self.url}/v1/models", headers=self.headers, timeout=3)
        r.raise_for_status()
        models = r.json().get("data", [])

        if not models:
            self._set_state(status="idle")
            return

        m = models[0]
        self._set_state(status="active", model=m.get("id", "unknown"),
                         context=m.get("context_length"))

    def list_models(self):
        """Returns (header_columns, rows) for the 'List Models' popup."""
        if self.kind == "openai":
            try:
                r = requests.get(f"{self.url}/v1/models", headers=self.headers, timeout=5)
                r.raise_for_status()
                models = sorted(r.json().get("data", []), key=lambda m: m.get("id", ""))
            except Exception:
                return []
            return [{"name": m.get("id", ""),
                     "params": "—", "quant": "—",
                     "size": f"{(m.get('context_length') or 0):,} ctx"}
                    for m in models]
        else:
            try:
                r = requests.get(f"{self.url}/api/tags", timeout=5)
                r.raise_for_status()
                models = sorted(r.json().get("models", []), key=lambda m: m.get("name", ""))
            except Exception:
                return []
            out = []
            for m in models:
                d = m.get("details", {})
                out.append({"name": m.get("name", ""),
                            "params": d.get("parameter_size", "—"),
                            "quant": d.get("quantization_level", "—"),
                            "size": f"{m.get('size', 0) / 1_073_741_824:.1f} GB"})
            return out

# ── App ───────────────────────────────────────────────────────────────────────
class OllamaTicker(ctk.CTk):

    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("dark")

        self.cfg    = load_cfg()
        self.t      = THEMES[self.cfg.get("theme", "Midnight")]
        self.backends = [Backend(s) for s in self.cfg["servers"]]
        n           = len(self.backends)
        self._base_w = PANEL_W * n
        self._min_w  = PANEL_MIN_W * n

        self._mini      = self.cfg.get("mini", False)
        self._full_h    = self.cfg.get("h", BASE_H)
        self._resize_job = None
        self._dx = self._dy = 0
        self._rw = self._rh = self._rsx = self._rsy = 0
        self._panels = []
        self._mini_widgets = []

        self.title("OllamaTicker")
        self.overrideredirect(True)

        w = self.cfg.get("w") or self._base_w
        h = 30 if self._mini else self._full_h
        self.geometry(f"{w}x{h}")
        x, y = self.cfg.get("x"), self.cfg.get("y")
        if x is not None and y is not None:
            sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
            if x < 0 or y < 0 or x + w > sw or y + h > sh:
                x = min(max(0, x), sw - w)
                y = min(max(0, y), sh - h)
            self.geometry(f"+{x}+{y}")
        self.wm_attributes("-topmost", self.cfg.get("always_on_top", True))
        self.wm_attributes("-alpha",   self.cfg.get("opacity", 1.0))

        self._fonts = {}
        self._setup_fonts(w, h)
        self._build_ui()

        for b in self.backends:
            b.start()
        self.after(500, self._refresh_ui)

    # ── Fonts (dynamic) ───────────────────────────────────────────────────────
    def _setup_fonts(self, w, h):
        if self._mini:
            win_scale = 1.0
        else:
            n = len(self.backends)
            win_scale = max(0.65, min(2.0, min((w / n) / PANEL_W, h / BASE_H)))

        scale  = win_scale * self.cfg.get("font_scale", 1.0)
        family = self.cfg.get("font_family", "Consolas")

        def sz(base):
            return max(7, int(base * scale))

        specs = {
            "title":  (10, "bold"),
            "model":  (15, "bold"),
            "label":  (10, "normal"),
            "value":  (10, "bold"),
            "status": (9,  "normal"),
            "close":  (13, "normal"),
        }
        for name, (base, weight) in specs.items():
            if name in self._fonts:
                self._fonts[name].configure(
                    family=family, size=sz(base), weight=weight)
            else:
                self._fonts[name] = tkFont.Font(
                    family=family, size=sz(base), weight=weight)

    # ── Build UI ──────────────────────────────────────────────────────────────
    def _build_ui(self):
        for w in self.winfo_children():
            w.destroy()
        self._panels = []
        self._mini_widgets = []

        t = self.t
        self.configure(fg_color=t["bg"])

        # ─ Title bar ──────────────────────────────────────────────────────────
        bar = tk.Frame(self, bg=t["bar"], height=30)
        bar.pack(fill="x")
        bar.pack_propagate(False)

        lbl = tk.Label(bar, text="⬡  OLLAMA TICKER",
                       bg=t["bar"], fg=t["accent"],
                       font=self._fonts["title"], anchor="w", padx=10)
        lbl.pack(side="left", fill="y")

        close_btn = tk.Label(bar, text=" × ", bg=t["bar"], fg=t["sub"],
                              font=self._fonts["close"], cursor="hand2")
        close_btn.pack(side="right", padx=2)
        close_btn.bind("<Button-1>", lambda e: self._on_close())
        close_btn.bind("<Enter>",    lambda e: close_btn.configure(fg=t["err"]))
        close_btn.bind("<Leave>",    lambda e: close_btn.configure(fg=t["sub"]))

        for widget in (bar, lbl):
            widget.bind("<Button-1>",        self._drag_start)
            widget.bind("<B1-Motion>",       self._drag_move)
            widget.bind("<ButtonRelease-1>", self._drag_end)
            widget.bind("<Double-Button-1>", self._toggle_mini)
            widget.bind("<Button-3>",        self._show_menu)

        if self._mini:
            # Mini: a slim status strip per server inside the bar
            for idx, b in enumerate(self.backends):
                with b.lock:
                    s = b.state.copy()
                dot = tk.Label(bar, text="●", bg=t["bar"],
                                fg=self._dot_color(s["status"]),
                                font=self._fonts["status"])
                dot.pack(side="left", padx=(10 if idx > 0 else 0, 0))
                lbl_text = (s["model"] or b.name)[:16]
                tk.Label(bar, text=lbl_text, bg=t["bar"], fg=t["sub"],
                         font=self._fonts["status"]).pack(side="left", padx=4)
                self._mini_widgets.append({"dot": dot})
            return

        # ─ Panels ─────────────────────────────────────────────────────────────
        panels_frame = tk.Frame(self, bg=t["bg"])
        panels_frame.pack(fill="both", expand=True)

        for idx, b in enumerate(self.backends):
            if idx > 0:
                tk.Frame(panels_frame, bg=t["card"], width=1).pack(side="left", fill="y")
            self._build_panel(panels_frame, idx, b, is_last=(idx == len(self.backends) - 1))

        self.bind_all("<Button-3>", self._show_menu)

    def _build_panel(self, parent, idx, b, is_last):
        t = self.t
        panel = tk.Frame(parent, bg=t["bg"])
        panel.pack(side="left", fill="both", expand=True)

        body = tk.Frame(panel, bg=t["bg"], padx=14, pady=8)
        body.pack(fill="both", expand=True)

        # Model name — click to copy, hover to see full name
        name_lbl = tk.Label(body, text="Connecting…",
                             bg=t["bg"], fg=t["accent"],
                             font=self._fonts["model"],
                             anchor="w", cursor="hand2")
        name_lbl.pack(fill="x")
        name_lbl.bind("<Button-1>", lambda e, i=idx: self._copy_model_name(i))
        name_lbl.bind("<Enter>", lambda e, w=name_lbl: w.configure(fg=lighten(self.t["accent"])))
        name_lbl.bind("<Leave>", lambda e, w=name_lbl: w.configure(fg=self.t["accent"]))
        Tooltip(name_lbl,
                lambda i=idx: self.backends[i].full_model if len(self.backends[i].full_model) > 28 else "",
                bg=t["card"], fg=t["text"])

        # Detail line (family · params · quant)
        detail_lbl = tk.Label(body, text="", bg=t["bg"], fg=t["sub"],
                               font=self._fonts["label"], anchor="w")
        detail_lbl.pack(fill="x")

        # Separator
        tk.Frame(body, bg=t["card"], height=1).pack(fill="x", pady=(7, 6))

        # Stats grid
        grid = tk.Frame(body, bg=t["bg"])
        grid.pack(fill="x")

        fields = [("VRAM", "vram"), ("Context", "context"), ("Expires", "expires")] \
            if b.kind != "openai" else [("Context", "context")]

        vals = {}
        for i, (label, key) in enumerate(fields):
            tk.Label(grid, text=label, bg=t["bg"], fg=t["sub"],
                     font=self._fonts["label"], width=9, anchor="w"
                     ).grid(row=i, column=0, sticky="w", pady=2)
            v = tk.Label(grid, text="—", bg=t["bg"], fg=t["text"],
                         font=self._fonts["value"], anchor="w")
            v.grid(row=i, column=1, sticky="w", padx=(6, 0), pady=2)
            vals[key] = v

        # Sparkline (VRAM history) — only meaningful for Ollama
        tk.Frame(body, bg=t["card"], height=1).pack(fill="x", pady=(7, 4))
        spark = None
        if b.kind != "openai":
            spark = tk.Canvas(body, bg=t["bg"], highlightthickness=0, bd=0)
            spark.pack(fill="both", expand=True)
        else:
            tk.Frame(body, bg=t["bg"]).pack(fill="both", expand=True)

        # ─ Footer ─────────────────────────────────────────────────────────────
        footer = tk.Frame(panel, bg=t["card"], height=22)
        footer.pack(fill="x", side="bottom")
        footer.pack_propagate(False)

        dot = tk.Label(footer, text="●", bg=t["card"], fg=t["warn"],
                       font=self._fonts["status"])
        dot.pack(side="left", padx=(8, 2))

        status_lbl = tk.Label(footer, text="Connecting…", bg=t["card"],
                               fg=t["sub"], font=self._fonts["status"])
        status_lbl.pack(side="left")

        if is_last:
            grip = tk.Label(footer, text="◢", bg=t["card"], fg=t["sub"],
                            font=self._fonts["status"], cursor="size_nw_se")
            grip.pack(side="right", padx=(0, 3))
            grip.bind("<Button-1>",        self._resize_start)
            grip.bind("<B1-Motion>",       self._resize_move)
            grip.bind("<ButtonRelease-1>", self._resize_end)

        tk.Label(footer, text=b.name, bg=t["card"], fg=t["sub"],
                 font=self._fonts["status"]).pack(side="right", padx=4)

        self._panels.append({"name_lbl": name_lbl, "detail_lbl": detail_lbl,
                              "vals": vals, "spark": spark,
                              "dot": dot, "status_lbl": status_lbl})

    # ── Polling ───────────────────────────────────────────────────────────────
    # (handled by Backend instances)

    # ── UI Refresh ────────────────────────────────────────────────────────────
    def _dot_color(self, status):
        return {"active": self.t["ok"],
                "idle":   self.t["warn"],
                "offline":self.t["err"]}.get(status, self.t["warn"])

    def _refresh_ui(self):
        if self._mini:
            self.after(1000, self._refresh_ui)
            return

        t = self.t
        for b, p in zip(self.backends, self._panels):
            with b.lock:
                s = b.state.copy()

            if s["status"] == "offline":
                p["name_lbl"].configure(text="Offline", fg=t["err"])
                p["detail_lbl"].configure(text="")
                for v in p["vals"].values():
                    v.configure(text="—")
                p["dot"].configure(fg=t["err"])
                p["status_lbl"].configure(text="Offline")
                b.full_model = ""
                if p["spark"]:
                    p["spark"].delete("all")

            elif s["status"] == "idle":
                p["name_lbl"].configure(text="No model loaded", fg=t["sub"])
                p["detail_lbl"].configure(text="")
                for v in p["vals"].values():
                    v.configure(text="—")
                p["dot"].configure(fg=t["warn"])
                p["status_lbl"].configure(text="Idle")
                b.full_model = ""
                if p["spark"]:
                    p["spark"].delete("all")

            elif s["status"] == "active":
                name = s["model"] or "—"
                b.full_model = name
                disp = name if len(name) <= 28 else name[:25] + "…"
                p["name_lbl"].configure(text=disp, fg=t["accent"])

                d = s["details"] or {}
                parts = [x for x in [
                    (d.get("family") or "").capitalize(),
                    d.get("parameter_size", ""),
                    d.get("quantization_level", ""),
                ] if x]
                p["detail_lbl"].configure(text=" · ".join(parts))

                for key, v in p["vals"].items():
                    v.configure(text=fmt_val(key, s.get(key)))

                p["dot"].configure(fg=t["ok"])
                p["status_lbl"].configure(text="Active")

                if p["spark"]:
                    if s["vram"]:
                        b.hist.append(s["vram"] / 1_073_741_824)
                    self._draw_sparkline(b, p["spark"])

            else:
                p["dot"].configure(fg=t["warn"])
                p["status_lbl"].configure(text="Connecting…")

        self.after(500, self._refresh_ui)

    # ── Sparkline ─────────────────────────────────────────────────────────────
    def _draw_sparkline(self, backend, c):
        c.delete("all")
        w = c.winfo_width()
        h = c.winfo_height()
        readings = list(backend.hist)
        if len(readings) < 2 or w < 4 or h < 4:
            return

        mn  = min(readings) * 0.9
        mx  = max(readings) * 1.1 or 1.0
        rng = mx - mn or 1
        pad = 3

        pts = []
        for i, v in enumerate(readings):
            x = pad + i * (w - pad * 2) / (len(readings) - 1)
            y = (h - pad) - (v - mn) / rng * (h - pad * 2)
            pts.extend([x, y])

        # Area fill
        area = [pad, h] + pts + [w - pad, h]
        c.create_polygon(area, fill=dim(self.t["accent"], 0.22), outline="")
        # Sparkline
        c.create_line(pts, fill=self.t["accent"], width=1.5, smooth=True)

        # Axis labels
        c.create_text(w - pad, pad + 1,
                      text=f"{readings[-1]:.2f}G",
                      fill=self.t["sub"], anchor="ne",
                      font=("Consolas", 7))
        if len(readings) > 5:
            peak = max(readings)
            c.create_text(pad, pad + 1,
                          text=f"peak {peak:.2f}G",
                          fill=self.t["sub"], anchor="nw",
                          font=("Consolas", 7))

    # ── Drag + Edge Snap ──────────────────────────────────────────────────────
    def _drag_start(self, e):
        self._dx = e.x_root - self.winfo_x()
        self._dy = e.y_root - self.winfo_y()

    def _drag_move(self, e):
        self.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    def _drag_end(self, e):
        if not self.cfg.get("snap", True):
            return
        x  = self.winfo_x()
        y  = self.winfo_y()
        ww = self.winfo_width()
        wh = self.winfo_height()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()

        if x < SNAP_PX:              x = 0
        elif x + ww > sw - SNAP_PX:  x = sw - ww
        if y < SNAP_PX:              y = 0
        elif y + wh > sh - SNAP_PX:  y = sh - wh

        self.geometry(f"+{x}+{y}")

    # ── Resize ────────────────────────────────────────────────────────────────
    def _resize_start(self, e):
        self._rw, self._rh   = self.winfo_width(), self.winfo_height()
        self._rsx, self._rsy = e.x_root, e.y_root

    def _resize_move(self, e):
        nw = max(self._min_w, self._rw + (e.x_root - self._rsx))
        nh = max(MIN_H, self._rh + (e.y_root - self._rsy))
        self.geometry(f"{nw}x{nh}")
        # Debounced font scale update
        if self._resize_job:
            self.after_cancel(self._resize_job)
        self._resize_job = self.after(60, lambda: self._setup_fonts(nw, nh))

    def _resize_end(self, e):
        w, h = self.winfo_width(), self.winfo_height()
        self._full_h = h
        self.cfg["w"], self.cfg["h"] = w, h
        save_cfg(self.cfg)

    # ── Mini mode ─────────────────────────────────────────────────────────────
    def _toggle_mini(self, e=None):
        self._mini = not self._mini
        self.cfg["mini"] = self._mini
        if self._mini:
            self._full_h  = self.winfo_height()
            self.cfg["h"] = self._full_h
            self.geometry(f"{self.winfo_width()}x30")
        else:
            self.geometry(f"{self.winfo_width()}x{self._full_h}")
        save_cfg(self.cfg)
        self._build_ui()

    # ── Context Menu ──────────────────────────────────────────────────────────
    def _show_menu(self, e):
        t   = self.t
        top = self.cfg.get("always_on_top", True)

        m = Menu(self, tearoff=0, bg=t["card"], fg=t["text"],
                 activebackground=t["accent"], activeforeground=t["bg"],
                 bd=0, relief="flat", font=("Consolas", 9))

        snap = self.cfg.get("snap", True)
        m.add_command(
            label=f"{'✓' if top else '  '}  Always on Top",
            command=self._toggle_topmost)
        m.add_command(
            label=f"{'✓' if snap else '  '}  Snap to Edges",
            command=self._toggle_snap)
        m.add_separator()

        # Theme submenu
        tm = Menu(m, tearoff=0, bg=t["card"], fg=t["text"],
                  activebackground=t["accent"], activeforeground=t["bg"],
                  bd=0, font=("Consolas", 9))
        cur = self.cfg.get("theme", "Midnight")
        for name in THEMES:
            tm.add_command(
                label=f"{'✓' if name == cur else '  '}  {name}",
                command=lambda n=name: self.apply_theme(n))
        m.add_cascade(label="   Theme  ▶", menu=tm)
        m.add_command(label="   Random Theme", command=self._random_theme)

        # Font submenu
        fm = Menu(m, tearoff=0, bg=t["card"], fg=t["text"],
                  activebackground=t["accent"], activeforeground=t["bg"],
                  bd=0, font=("Consolas", 9))

        cur_family = self.cfg.get("font_family", "Consolas")
        fam_m = Menu(fm, tearoff=0, bg=t["card"], fg=t["text"],
                     activebackground=t["accent"], activeforeground=t["bg"],
                     bd=0, font=("Consolas", 9))
        for fam in FONT_FAMILIES:
            fam_m.add_command(
                label=f"{'✓' if fam == cur_family else '  '}  {fam}",
                command=lambda f=fam: self._set_font_family(f))
        fm.add_cascade(label="   Family ▶", menu=fam_m)

        cur_scale = self.cfg.get("font_scale", 1.0)
        sz_m = Menu(fm, tearoff=0, bg=t["card"], fg=t["text"],
                    activebackground=t["accent"], activeforeground=t["bg"],
                    bd=0, font=("Consolas", 9))
        for label, val in FONT_SIZES:
            sz_m.add_command(
                label=f"{'✓' if abs(cur_scale - val) < 0.05 else '  '}  {label}",
                command=lambda v=val: self._set_font_scale(v))
        fm.add_cascade(label="   Size ▶", menu=sz_m)
        m.add_cascade(label="   Font  ▶", menu=fm)

        # Opacity submenu
        om = Menu(m, tearoff=0, bg=t["card"], fg=t["text"],
                  activebackground=t["accent"], activeforeground=t["bg"],
                  bd=0, font=("Consolas", 9))
        cur_op = self.cfg.get("opacity", 1.0)
        for label, val in [("100%", 1.0), ("90%", 0.9), ("80%", 0.8),
                            ("70%", 0.7), ("60%", 0.6)]:
            om.add_command(
                label=f"{'✓' if abs(cur_op - val) < 0.05 else '  '}  {label}",
                command=lambda v=val: self._set_opacity(v))
        m.add_cascade(label="   Opacity ▶", menu=om)

        m.add_separator()
        m.add_command(label="   List Models", command=self._show_models)
        m.add_command(label="   Reset Position", command=self._reset_position)
        m.add_command(label="   Quit", command=self._on_close)

        try:
            m.tk_popup(e.x_root, e.y_root)
        finally:
            m.grab_release()

    def _toggle_topmost(self):
        self.cfg["always_on_top"] = not self.cfg.get("always_on_top", True)
        self.wm_attributes("-topmost", self.cfg["always_on_top"])
        save_cfg(self.cfg)

    def _toggle_snap(self):
        self.cfg["snap"] = not self.cfg.get("snap", True)
        save_cfg(self.cfg)

    def _show_models(self):
        t = self.t

        popup = tk.Toplevel(self)
        popup.overrideredirect(True)
        popup.configure(bg=t["bg"])
        popup.wm_attributes("-topmost", True)

        # Position to the right of the main window, or below if no room
        wx, wy  = self.winfo_x(), self.winfo_y()
        ww      = self.winfo_width()
        sw      = self.winfo_screenwidth()
        pw      = 480
        px      = wx + ww + 8 if wx + ww + 8 + pw < sw else wx - pw - 8
        popup.geometry(f"{pw}x320+{px}+{wy}")

        # ── Title bar ──────────────────────────────────────────────────────
        bar = tk.Frame(popup, bg=t["bar"], height=30)
        bar.pack(fill="x")
        bar.pack_propagate(False)

        tk.Label(bar, text="⬡  INSTALLED MODELS",
                 bg=t["bar"], fg=t["accent"],
                 font=self._fonts["title"], anchor="w", padx=10
                 ).pack(side="left", fill="y")

        close = tk.Label(bar, text=" × ", bg=t["bar"], fg=t["sub"],
                          font=self._fonts["close"], cursor="hand2")
        close.pack(side="right", padx=2)
        close.bind("<Button-1>", lambda e: popup.destroy())
        close.bind("<Enter>",    lambda e: close.configure(fg=t["err"]))
        close.bind("<Leave>",    lambda e: close.configure(fg=t["sub"]))

        # Drag
        _d = [0, 0]
        def _ds(e): _d[0] = e.x_root - popup.winfo_x(); _d[1] = e.y_root - popup.winfo_y()
        def _dm(e): popup.geometry(f"+{e.x_root-_d[0]}+{e.y_root-_d[1]}")
        bar.bind("<Button-1>",  _ds)
        bar.bind("<B1-Motion>", _dm)

        # ── Content ────────────────────────────────────────────────────────
        body = tk.Frame(popup, bg=t["bg"], padx=10, pady=8)
        body.pack(fill="both", expand=True)

        family = self.cfg.get("font_family", "Consolas")

        all_rows = []
        for b in self.backends:
            rows = b.list_models()
            all_rows.append((b.name, rows))

        col = 4
        for _, rows in all_rows:
            for r in rows:
                col = max(col, len(r["name"]))
        col += 2

        lines = ""
        total = 0
        for name, rows in all_rows:
            lines += f"── {name} " + "─" * max(0, col + 23 - len(name)) + "\n"
            if rows:
                lines += f"{'NAME':<{col}} {'PARAMS':<8} {'QUANT':<9} {'SIZE':>9}\n"
                for r in rows:
                    lines += f"{r['name']:<{col}} {r['params']:<8} {r['quant']:<9} {r['size']:>9}\n"
                total += len(rows)
            else:
                lines += "  (no models found / unreachable)\n"
            lines += "\n"

        if not lines.strip():
            lines = "No models found — is the server running?"

        txt = tk.Text(body, bg=t["card"], fg=t["text"],
                      font=(family, 9), relief="flat", wrap="none",
                      bd=0, padx=8, pady=6, cursor="arrow",
                      selectbackground=t["accent"],
                      selectforeground=t["bg"])
        txt.pack(fill="both", expand=True)

        sb = tk.Scrollbar(body, orient="horizontal", command=txt.xview,
                          bg=t["card"], troughcolor=t["bg"],
                          highlightthickness=0, bd=0)
        txt.configure(xscrollcommand=sb.set)
        sb.pack(fill="x")

        txt.insert("1.0", lines)
        txt.configure(state="disabled")

        # ── Footer ─────────────────────────────────────────────────────────
        footer = tk.Frame(popup, bg=t["card"], height=26)
        footer.pack(fill="x", side="bottom")
        footer.pack_propagate(False)

        tk.Label(footer, text=f"{total} model{'s' if total != 1 else ''}",
                 bg=t["card"], fg=t["sub"],
                 font=(family, 8)).pack(side="left", padx=10)

        def _copy():
            popup.clipboard_clear()
            popup.clipboard_append(txt.get("1.0", "end-1c"))
            copy_lbl.configure(fg=t["ok"])
            popup.after(500, lambda: copy_lbl.configure(fg=t["sub"]))

        copy_lbl = tk.Label(footer, text="Copy All", bg=t["card"],
                             fg=t["sub"], font=(family, 8), cursor="hand2")
        copy_lbl.pack(side="right", padx=10)
        copy_lbl.bind("<Button-1>", lambda e: _copy())
        copy_lbl.bind("<Enter>",    lambda e: copy_lbl.configure(fg=t["accent"]))
        copy_lbl.bind("<Leave>",    lambda e: copy_lbl.configure(fg=t["sub"]))

    def _reset_position(self):
        self._full_h = BASE_H
        self.cfg["w"], self.cfg["h"] = self._base_w, BASE_H
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        x  = (sw - self._base_w) // 2
        y  = (sh - BASE_H) // 2
        self.geometry(f"{self._base_w}x{BASE_H}+{x}+{y}")
        self._setup_fonts(self._base_w, BASE_H)
        save_cfg(self.cfg)

    def _set_font_family(self, family):
        available = tkFont.families()
        if family not in available:
            family = "Consolas"
        self.cfg["font_family"] = family
        save_cfg(self.cfg)
        # Defer past menu close — calling _build_ui while menu has grab crashes tkinter
        self.after(10, lambda: (
            self._setup_fonts(self.winfo_width(), self.winfo_height()),
            self._build_ui()
        ))

    def _set_font_scale(self, val):
        self.cfg["font_scale"] = val
        save_cfg(self.cfg)
        self.after(10, lambda: self._setup_fonts(
            self.winfo_width(), self.winfo_height()))

    def _set_opacity(self, val):
        self.cfg["opacity"] = val
        self.wm_attributes("-alpha", val)
        save_cfg(self.cfg)

    def apply_theme(self, name):
        self.cfg["theme"] = name
        self.t = THEMES[name]
        save_cfg(self.cfg)
        self.after(10, self._build_ui)

    def _random_theme(self):
        cur     = self.cfg.get("theme", "Midnight")
        choices = [n for n in THEMES if n != cur]
        self.apply_theme(random.choice(choices))

    # ── Copy model name ───────────────────────────────────────────────────────
    def _copy_model_name(self, idx):
        b = self.backends[idx]
        if not b.full_model:
            return
        self.clipboard_clear()
        self.clipboard_append(b.full_model)
        # Flash green as confirmation
        name_lbl = self._panels[idx]["name_lbl"]
        name_lbl.configure(fg=self.t["ok"])
        self.after(400, lambda: name_lbl.configure(fg=self.t["accent"]))

    # ── Close ─────────────────────────────────────────────────────────────────
    def _on_close(self):
        self.cfg.update({
            "x": self.winfo_x(), "y": self.winfo_y(),
            "w": self.winfo_width(), "h": self._full_h,
        })
        save_cfg(self.cfg)
        self.destroy()


if __name__ == "__main__":
    app = OllamaTicker()
    app.mainloop()
