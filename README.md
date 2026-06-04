# OllamaTicker

A small always-on-top desktop widget that shows live stats for a running [Ollama](https://ollama.com) instance — loaded model, VRAM usage, context window size, expiry countdown, and a live VRAM sparkline.

![Dracula theme](https://img.shields.io/badge/themes-6-blueviolet) ![Python](https://img.shields.io/badge/python-3.9%2B-blue)

---

## Features

- **Live stats** — model name, family, parameter size, quantization, VRAM, context tokens, expiry countdown
- **VRAM sparkline** — scrolling history graph with current and peak labels
- **6 themes** — Midnight, Dracula, Nord, Monokai, Synthwave, Solarized + random picker
- **Resizable** — drag the corner grip; fonts scale with the window
- **Mini mode** — double-click the title bar to collapse to a slim status strip
- **Edge snapping** — snaps to screen edges on release (toggleable for multi-monitor)
- **Opacity control** — 60%–100%
- **Font picker** — family and size adjustable from the right-click menu
- **Click model name** — copies to clipboard with a green flash confirmation
- **Works locally or remotely** — point it at any Ollama instance on the network

---

## Requirements

- Python 3.9+
- Ollama running locally or on a remote machine

---

## Quick Start

```bash
pip install -r requirements.txt
python ollama_stats.pyw                        # localhost:11434
python ollama_stats.pyw http://192.168.1.x:11434  # remote instance
```

Or use the included launchers:

| File | Target |
|------|--------|
| `run_local.bat` | `localhost:11434` |
| `run_remote.bat` | edit to set your remote IP |

The `.bat` files run `pip install` automatically on first launch.

---

## Remote Setup

By default Ollama only listens on `127.0.0.1`. To expose it on your network, set this environment variable on the **host machine** before starting Ollama:

```
OLLAMA_HOST=0.0.0.0
```

Then point OllamaTicker at `http://<host-ip>:11434`.

---

## Controls

| Action | Result |
|--------|--------|
| Drag title bar | Move window |
| Double-click title bar | Toggle mini mode |
| Drag `◢` grip | Resize window |
| Click model name | Copy to clipboard |
| Right-click anywhere | Open menu |

### Right-click menu

- **Always on Top** — toggle
- **Snap to Edges** — disable for multi-monitor use
- **Theme** — pick or randomize
- **Font** — family and size
- **Opacity** — 60%–100%
- **Reset Position** — centers window at default size
- **Quit**

---

## Config

Settings are saved automatically to `config.json` next to the script (excluded from git). Delete it to reset everything to defaults.

---

## States

| Status | Dot | Meaning |
|--------|-----|---------|
| 🟢 Active | Green | Model loaded and running |
| 🟡 Idle | Yellow | Ollama running, no model loaded |
| 🔴 Offline | Red | Can't reach Ollama |
