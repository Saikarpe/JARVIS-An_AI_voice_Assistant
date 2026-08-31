# Jarvis

A desktop voice assistant with a real agentic core: a single Groq-hosted
LLM decides, per turn, whether it needs a tool, calls it with typed
arguments, reads the result, and can chain into another tool before
answering — not a fixed keyword router. Wake word, streaming replies,
barge-in, long-term memory, and reminders run on top of that core, all
behind a PyQt5 desktop UI.

This repository was rebuilt against [`ENHANCEMENT_PLAN.md`](ENHANCEMENT_PLAN.md),
which is worth reading if you want the *why* behind any particular design
choice — most non-obvious decisions in the code point back to a section of
it in a comment.

## Features

- **Agentic tool calling.** One LLM call routes and answers — no separate
  intent-classification step. A request needing two or more tools in
  sequence ("search the weather, then set a reminder if it's raining")
  completes without re-prompting, up to a hard 6-step ceiling.
- **Wake word.** "Hey Jarvis" (via [openWakeWord](https://github.com/dscripka/openWakeWord))
  gates listening — the mic isn't hot the instant the app launches. Falls
  back to always-on listening if the model can't load.
- **Streaming, interruptible speech.** Replies stream token-by-token into
  the chat pane as they're generated. While Jarvis is speaking, talking
  over it (barge-in, via VAD) cuts the reply short and starts listening.
- **Long-term memory.** Facts the model decides are worth keeping persist
  across restarts (local embeddings, no cloud vector DB) and get pulled
  back into context when relevant on a later turn.
- **Reminders & proactive briefings.** "Remind me to call mom at 6pm"
  actually fires and speaks, on its own background thread. An optional
  daily briefing (off by default) can summarize the day at a set time.
- **A UI that shows its work.** Every tool call appears inline, live, as
  it happens — not just the final answer. The circular visualizer reacts
  to real mic/speaker audio levels, not a decorative animation.
- **Settings, history, and stats panels**, a system tray icon (closing
  the window minimizes it, since a wake-word assistant is meant to keep
  listening), keyboard shortcuts, and light/dark themes.

## Architecture

```
main.py
  └─ QApplication (main thread)
       ├─ MainWindow ─────────────────── Frontend/
       │    ├─ ChatSection                 theme.py         design tokens
       │    ├─ MessageBubble + ToolTimeline widgets/         bubbles, toasts,
       │    ├─ CircularVisualizer                            settings/history/
       │    └─ HistorySidebar, dialogs                       stats panels
       │
       ├─ AgentWorker (QThread) ───────── Backend/
       │    ├─ wake word detection         agent.py          the tool-calling
       │    ├─ STT (Groq Whisper)                             loop
       │    ├─ agent loop                  agent_worker.py   Qt bridge
       │    └─ streaming TTS               tools/            registry.py +
       │                                                      web, system,
       └─ SchedulerWorker (QThread)                           files, media,
            └─ due reminders, briefings                       images, memory,
                                                                reminders
                                           Database.py        SQLite
                                           config.py          settings
```

Signals flow worker → GUI; slots flow GUI → worker. There is no file-based
IPC anywhere in this codebase — `AgentWorker` and `SchedulerWorker` each
run on their own `QThread` and talk to the UI exclusively through Qt
signals, and to each other's state through SQLite.

A user turn, end to end:

```
mic / typed text
  → AgentWorker._process_query()
      → Backend.agent.run_agent()
          → Groq chat completion, streamed, tools=<registry schemas>
          → if the model calls a tool: Backend.tools.registry.call_tool()
            → result fed back to the model; loop (up to 6 steps)
          → final answer streamed to the UI token-by-token
      → spoken via edge-tts, interruptible mid-sentence
```

## Tools

Every tool below is a plain Python function with type-hinted parameters —
`Backend/tools/registry.py` derives its JSON schema straight from the
signature, so the schema the model sees can never drift from what the
function actually does.

| Tool | What it does |
|---|---|
| `web_search` | Searches the web (DuckDuckGo) for current information |
| `open_app` / `close_app` | Opens or closes an installed app or website |
| `google_search` | Opens a Google search results page |
| `play_youtube` / `search_youtube` | Plays or searches YouTube |
| `control_audio` | Mute/unmute/volume up/down |
| `write_document` | Drafts content (letter, code, email, ...) and opens it in Notepad |
| `generate_image` | Generates image(s) from a prompt (Stability AI) |
| `remember_fact` / `recall_memory` | Long-term memory: save/search durable facts about the user |
| `create_reminder` | Sets a reminder that fires and is spoken aloud later |

## Setup

**Requires:** Python 3.11, Windows (the target platform — see
`ENHANCEMENT_PLAN.md` rule 6), a working microphone for the voice features.

```bash
git clone <this-repo>
cd JARVIS-An_AI_voice_Assistant
python -m venv venv
venv\Scripts\activate
pip install -r Requirements.txt
copy .env.example .env
```

Edit `.env` and fill in at least `GroqAPIKey` (free at
[console.groq.com](https://console.groq.com)) — everything else has a
working default. `STABILITY_API_KEY` is only needed for `generate_image`;
without it, that one tool reports itself unavailable and everything else
still works.

```bash
python main.py
```

First launch downloads the wake-word model and, on first use of memory,
a small local embedding model — both cached locally after that.

## Usage

Click the mic icon or just say "Hey Jarvis" and speak. Typing works too —
switch to the Chat tab, or use the input box there.

**Keyboard shortcuts:**

| Shortcut | Action |
|---|---|
| `Ctrl+Space` | Toggle the mic on/off |
| `Ctrl+K` | Command palette — browse available tools |
| `Esc` | Stop Jarvis mid-sentence |
| `Ctrl+L` | Clear the current conversation |
| `Ctrl+,` | Open settings |
| `F11` | Toggle fullscreen |
| `Ctrl+Q` | Quit (closing the window normally just minimizes to the tray) |

Settings (mic device, voice, wake word sensitivity, STT backend, theme,
proactive behaviors) are under the settings icon in the top bar; most
take effect on the next launch (see `Backend/config.py`'s docstring for
which).

## Development

```bash
pip install -r Requirements.txt   # pytest/ruff/mypy/PyQt5-stubs are included
pytest                             # unit tests — the agent core, not the UI; network mocked
ruff check .                       # lint
mypy Backend Frontend main.py      # type check
```

Logs go to both the console and a rotating file under `Data/logs/`
(`Backend/logging_config.py`). Tests never touch your real `Data/jarvis.db`
— `tests/conftest.py` points them at a throwaway file.

## Project layout

```
main.py                  Entry point — builds the QApplication, wires everything up
Backend/
  agent.py                The tool-calling agent loop
  agent_worker.py          Qt bridge — runs the pipeline off the GUI thread
  scheduler_worker.py      Reminders + proactive briefings, own QThread
  Database.py              SQLite: conversations, memory, reminders, usage stats, prefs
  config.py                Settings dataclass (.env defaults + DB overrides)
  SpeechToText.py / TextToSpeech.py / wake_word.py / barge_in.py   Voice pipeline
  tools/                    Every agent tool, one module per domain
Frontend/
  GUI.py                    Main window, chat pane, visualizer, top bar
  theme.py                  Design tokens (dark/light)
  widgets/                  Message bubbles, settings/history/stats panels, toasts
tests/                     pytest suite (tool registry, agent loop, database, memory recall)
```
