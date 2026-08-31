# JARVIS — Enhancement Plan

**Purpose:** implementation spec for upgrading this project from a keyword-routed voice assistant into an agentic, tool-using assistant with a modern desktop UI.

**Audience:** the AI model implementing these changes. Read the whole file before writing code. Work phase by phase, in order. Do not skip Phase 0 or Phase 1 — later phases depend on them.

**Current state (verified 2026-08-25):** ~2,180 LOC. `main.py` runs a voice loop on a daemon thread; `Frontend/GUI.py` runs PyQt5 on the main thread; they communicate by writing flat files in `Frontend/Files/` that three separate `QTimer`s poll on an interval. Intent routing is a Cohere prompt that returns a comma-separated string (`"open chrome, general who is akbar"`) parsed with `str.startswith()`. Answers come from Groq `llama-3.3-70b-versatile`. SQLite exists and is well built, but half its tables are unused.

---

## Rules for the implementing model

1. **Do not rewrite the whole repo in one pass.** One phase per commit; the app must run at the end of each phase.
2. **`Backend/Database.py` is the best code in the project.** Extend it, do not replace it. It already has thread-local connections, WAL mode, and parameterized queries. Keep that pattern.
3. **Verify model IDs against provider docs before using them.** IDs in this document were correct when written and deprecate frequently. If a call 404s, check the provider's model list rather than guessing a variant.
4. **Every network call gets a timeout and a bounded retry.** No unbounded recursion, ever (see P0-1 for why this rule exists).
5. **Never commit `.env`.** It has leaked from this repo once already.
6. **Windows is the target platform.** Test on Windows. Prefer packages with prebuilt Windows wheels; avoid anything needing a C compiler or CUDA.
7. Keep the public function names in `Frontend/GUI.py` (`SetAssistantStatus`, `ShowTextToScreen`, `TempDirectoryPath`, …) as thin shims until Phase 1 is complete, so nothing breaks mid-refactor.

---

## Phase 0 — Stabilize (do this first, it is small)

These are live bugs. Fix them before building anything on top.

### P0-1 · Infinite recursion in the chat error path

`Backend/Chatbot.py:100` — the `except` block calls `clear_chat_history()` then `return ChatBot(Query)`. A bad API key or dropped connection recurses until `RecursionError`.

Replace with a bounded retry:

```python
def ChatBot(Query, _attempt=0):
    try:
        ...
    except Exception as e:
        print(f"[ChatBot] error (attempt {_attempt}): {e}")
        if _attempt >= 2:
            return "I'm having trouble reaching my language model right now."
        if _attempt == 1:
            clear_chat_history()          # only wipe history on the last retry
        time.sleep(1.5 * (_attempt + 1))  # backoff
        return ChatBot(Query, _attempt + 1)
```

Apply the same guard to `Backend/Model.py:FirstLayerDMM`, which self-recurses on `if "(query)" in response`.

### P0-2 · Orphaned image-generation subprocesses

`main.py` appends to `subprocesses[]` but exits via `os._exit(0)` (line 254), so every `ImageGeneration.py` child survives forever, each spinning a 0.1 s file-poll loop.

Register cleanup and stop using `os._exit`:

```python
import atexit

def _cleanup():
    for p in subprocesses:
        try:
            p.terminate(); p.wait(timeout=3)
        except Exception:
            p.kill()

atexit.register(_cleanup)
```

Phase 2 removes the subprocess entirely (image generation becomes an in-process tool), but fix it now so development is not leaking processes.

### P0-3 · Requirements.txt does not describe the project

Missing, and the app will not start from a clean install without them: `duckduckgo-search`, `SpeechRecognition`, `PyAudio`.

Unused, remove: `selenium`, `googlesearch-python`, `webdriver-manager`.

Also **pin versions** — an unpinned requirements file on a project this age will not resolve cleanly. Rename to lowercase `requirements.txt` and update the README to match.

### P0-4 · Dead code and dead assets

- `Data/Voice.html` — leftover from a Selenium STT approach that no longer exists. Delete.
- `get_usage_summary` imported at `main.py:18`, never called. Either surface it in the Phase 5 stats panel or drop the import.
- `Frontend/Files/Query.data` and `UploadedFile.data` are written by the GUI and read by nothing — the text input and file-upload buttons are dead. Phase 1 makes them work.
- `Frontend/Graphics/Typing.gif` is referenced at `GUI.py:105` but does not exist, and its trigger checks for status `"Processing"`, a value nothing ever writes. Phase 5 replaces this with a real streaming indicator.
- `Frontend/Graphics/Settings.png` exists but is wired to nothing. Phase 5 adds the panel.

### P0-5 · Rotate and verify leaked keys

`.env` was committed in `775ce6f` and removed in `81cc1b9`. Removing a file does not remove it from history — the Cohere, Groq, and Stability keys from that commit are readable in the public GitHub repo. The current `.env` holds different values, so rotation already happened, but **confirm the old keys are revoked at each provider's dashboard.** Optionally scrub history with `git filter-repo --path .env --invert-paths` and force-push (coordinate first — it rewrites every SHA).

### P0-6 · Stop tracking conversation data

`Data/ChatLog.json` is committed and contains real conversation content. Run `git rm --cached Data/ChatLog.json` and add it to `.gitignore`.

**Phase 0 acceptance:** clean `pip install -r requirements.txt` in a fresh venv, `python main.py` starts, no orphan processes after close, and killing the network produces an error message instead of a crash.

---

## Phase 1 — Replace file-based IPC with Qt signals

**This is the highest-leverage change in the document.** Every UX complaint about this app — lag, stale text, the visualizer not matching what is happening — traces back to the fact that the GUI learns about state by polling files on a timer.

### The problem

```
main.py thread ──write──> Files/Status.data    ──poll every 1000ms──> GUI
               ──write──> Files/Responses.data ──poll every  100ms──> GUI
```

Three `QTimer`s (`GUI.py:180`, `:448`, `:715`), file I/O on every tick, up to a full second of latency, no way to stream a response, and a race where a fast second response overwrites the first before the poll reads it.

### The fix — worker thread plus signals

Create `Backend/agent_worker.py`:

```python
from PyQt5.QtCore import QObject, pyqtSignal, pyqtSlot

class AgentWorker(QObject):
    """Runs the full voice/agent pipeline off the GUI thread.
    Owns no widgets. Communicates only via signals."""

    state_changed      = pyqtSignal(str)        # idle|listening|thinking|tool|speaking
    partial_transcript = pyqtSignal(str)        # live STT text as the user speaks
    user_message       = pyqtSignal(str)        # finalized user turn
    token              = pyqtSignal(str)        # one streamed token of the reply
    response_done      = pyqtSignal(str)        # full reply text
    tool_started       = pyqtSignal(str, dict)  # tool name, arguments
    tool_finished      = pyqtSignal(str, str)   # tool name, short result summary
    audio_level        = pyqtSignal(float)      # 0.0-1.0 mic RMS, for the visualizer
    error              = pyqtSignal(str)

    @pyqtSlot(str)
    def handle_text_query(self, text): ...   # wired to the GUI input box

    @pyqtSlot()
    def start_listening(self): ...

    @pyqtSlot()
    def stop_speaking(self): ...             # barge-in
```

Wire it up in `main.py`:

```python
app = QApplication(sys.argv)
window = MainWindow()

thread = QThread()
worker = AgentWorker()
worker.moveToThread(thread)

worker.token.connect(window.chat.append_token)
worker.state_changed.connect(window.visualizer.setState)
worker.audio_level.connect(window.visualizer.setLevel)
worker.tool_started.connect(window.chat.show_tool_call)
window.chat.query_submitted.connect(worker.handle_text_query)

thread.started.connect(worker.run)
thread.start()
window.show()
sys.exit(app.exec_())
```

### What to delete once this works

- All three polling `QTimer`s in `Frontend/GUI.py`.
- `SetAssistantStatus` / `GetAssistantStatus` / `ShowTextToScreen` / `SetMicrophoneStatus` / `GetMicrophoneStatus`, and every `Frontend/Files/*.data` read and write.
- The `Frontend/Files/` directory itself, except `ImageGeneration.data` until Phase 2 removes it.

### Bonus this unlocks for free

The dead text input (`GUI.py:243`) starts working — emit `query_submitted` instead of writing `Query.data`. Same for file upload.

**Phase 1 acceptance:** no file in `Frontend/Files/` is read or written during a conversation; status text updates within one frame of the state actually changing; typing in the input box produces a real answer.

---

## Phase 2 — Agentic core: native tool calling

This is what turns the project from "voice-controlled macro runner" into an actual agent.

### What is wrong today

`Backend/Model.py` asks Cohere to emit a comma-separated command string, which `main.py` parses with `startswith()` against a hardcoded `Functions` list. Consequences:

- **Single-step only.** It cannot search, read the result, and then decide to search again.
- **No arguments.** Everything is a string suffix, so a tool cannot take typed parameters.
- **Brittle parsing.** A comma inside a user's prompt corrupts the command list.
- **Duplicated logic.** The `write` → `content` remap exists twice and differently (`main.py:150` replaces the verb, `Automation.py:171` prepends, producing `"content write ..."`).
- **A whole extra API call and provider** just to classify intent.

### The fix — a tool registry with real function calling

Groq's chat completions API is OpenAI-compatible and supports native tool calling on `llama-3.3-70b-versatile`. That removes Cohere from the hot path entirely: one model both routes and answers.

Create `Backend/tools/registry.py`:

```python
import inspect
from typing import get_type_hints

_TOOLS = {}

def tool(description: str):
    """Register a function as an agent tool. The JSON schema is derived
    from the signature and type hints, so there is one source of truth."""
    def decorator(fn):
        hints = get_type_hints(fn)
        sig = inspect.signature(fn)
        props, required = {}, []
        for name, param in sig.parameters.items():
            py = hints.get(name, str)
            props[name] = {
                "type": {str: "string", int: "integer",
                         float: "number", bool: "boolean"}.get(py, "string"),
                "description": f"{name} parameter",
            }
            if param.default is inspect.Parameter.empty:
                required.append(name)
        _TOOLS[fn.__name__] = {
            "fn": fn,
            "schema": {
                "type": "function",
                "function": {
                    "name": fn.__name__,
                    "description": description,
                    "parameters": {"type": "object",
                                   "properties": props,
                                   "required": required},
                },
            },
        }
        return fn
    return decorator

def get_schemas():
    return [t["schema"] for t in _TOOLS.values()]

def call_tool(name, arguments: dict) -> str:
    if name not in _TOOLS:
        return f"Error: unknown tool {name}"
    try:
        return str(_TOOLS[name]["fn"](**arguments))
    except Exception as e:
        return f"Error running {name}: {e}"   # errors go back to the model, not the user
```

Then port every capability in `Backend/Automation.py` into `Backend/tools/`, one module per domain:

```python
# Backend/tools/web.py
@tool("Search the web for current information. Use for news, prices, "
      "weather, or anything after your training cutoff.")
def web_search(query: str, num_results: int = 5) -> str:
    ...

@tool("Open a website or installed desktop application by name.")
def open_app(name: str) -> str:
    ...

# Backend/tools/system.py
@tool("Control system audio. action must be one of: "
      "mute, unmute, volume_up, volume_down.")
def control_audio(action: str) -> str:
    ...

# Backend/tools/files.py
@tool("Write generated content (essay, letter, code) to a file and open it.")
def write_document(filename: str, content: str) -> str:
    ...

# Backend/tools/media.py
@tool("Play a song or video on YouTube.")
def play_youtube(query: str) -> str:
    ...

# Backend/tools/images.py
@tool("Generate an image from a text prompt.")
def generate_image(prompt: str, count: int = 1) -> str:
    ...   # in-process; deletes the subprocess + ImageGeneration.data polling entirely
```

### The agent loop

Create `Backend/agent.py`. This replaces `FirstLayerDMM` and most of `MainExecution`:

```python
MAX_STEPS = 6   # hard ceiling; prevents runaway tool loops and cost blowups

def run_agent(user_query: str, emit) -> str:
    messages = build_messages(user_query)   # system + memory + recent history

    for step in range(MAX_STEPS):
        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
            tools=get_schemas(),
            tool_choice="auto",
            temperature=0.7,
            timeout=30,
        )
        msg = resp.choices[0].message

        if not msg.tool_calls:
            emit.response_done.emit(msg.content)
            return msg.content

        messages.append(msg)
        for tc in msg.tool_calls:
            args = json.loads(tc.function.arguments)
            emit.tool_started.emit(tc.function.name, args)      # UI shows the step live
            result = call_tool(tc.function.name, args)
            emit.tool_finished.emit(tc.function.name, result[:120])
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result,
            })

    return "I wasn't able to finish that in a reasonable number of steps."
```

**Why this matters for the demo:** "What's the weather in Pune and remind me to carry an umbrella if it's raining" becomes one utterance that triggers `web_search` → reads the result → conditionally calls `create_reminder` → speaks a summary. The current architecture cannot express that at all.

### Streaming

Set `stream=True` and emit `token` per chunk so the UI renders text as it arrives. Note that tool-call deltas arrive fragmented across chunks and must be accumulated by index before parsing the JSON arguments — handle that explicitly.

### What to delete

- `Backend/Model.py` entirely (and the `cohere` dependency, unless kept as a fallback router).
- The `Functions` list and the routing block in `main.py` (lines ~140-250).
- `Backend/ImageGeneration.py`'s `while True` polling loop and the subprocess spawn.

**Phase 2 acceptance:** a single multi-step request that requires searching and then acting on the search result completes end to end; the UI shows each tool call as it happens; no comma in a user query can break routing.

---

## Phase 3 — Voice pipeline

Three separate upgrades. Ordered by user-visible impact.

### 3.1 Wake word — stop the always-on listen loop

Today `FirstThread` calls `SpeechRecognition()` in a tight loop, so the mic is hot constantly, every ambient noise burns a Google STT request, and there is no way to address the assistant by name.

Use **openWakeWord** (Apache-2.0, no API key, ONNX runtime, has a prebuilt "hey jarvis" model — which is exactly this project's name):

```
pip install openwakeword onnxruntime
```

```python
from openwakeword.model import Model
oww = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")

# feed 80ms frames of 16kHz int16 audio
prediction = oww.predict(frame)
if prediction["hey_jarvis"] > 0.5:
    emit.state_changed.emit("listening")
```

Fallback if openWakeWord misbehaves on Windows: Picovoice Porcupine (`pvporcupine`), which is more accurate but needs a free access key.

Keep a click-to-talk button and a push-to-talk hotkey as alternatives — never make wake word the only path in.

### 3.2 Better STT — replace the Google web API

`recognizer.recognize_google()` uses an undocumented endpoint with no SLA, no punctuation, and a network round trip. Two better options:

**Option A (recommended, simplest):** Groq hosts Whisper. You already have a Groq key, so this adds no new credentials and is very fast.

```python
with open(audio_path, "rb") as f:
    transcript = client.audio.transcriptions.create(
        file=f, model="whisper-large-v3-turbo", response_format="text"
    )
```

**Option B (offline, no per-request cost):** `faster-whisper` with the `base.en` or `small.en` model runs on CPU on a normal laptop and works with no internet. Slower to start (model download), better for a demo where wifi may fail.

Implement A, keep B behind a config flag. Also delete the hardcoded `"Realtek"` microphone preference at `SpeechToText.py:26` — replace it with a device picker in the Phase 5 settings panel, defaulting to the system default device.

### 3.3 Barge-in — let the user interrupt

Currently `TextToSpeech` generates a complete MP3, plays it to the end, and only then returns. `request_stop()` exists but the only thing that calls it is a "stop" intent detected on the *next* full listen-classify cycle, which cannot happen while audio is playing. In practice the assistant is uninterruptible.

Fix in two parts:

1. **Stream the TTS.** `edge_tts.Communicate.stream()` yields audio chunks; feed them to a `sounddevice.OutputStream` instead of writing `Data/speech.mp3` and playing the finished file. Time-to-first-audio drops from seconds to a few hundred milliseconds.
2. **Run VAD during playback.** Keep the mic open while speaking, run `webrtcvad` (install `webrtcvad-wheels` on Windows) over the input, and if sustained speech is detected for ~300 ms, call `request_stop()` and transition straight to listening.

This single feature is the difference between a demo that feels like a toy and one that feels like a product.

---

## Phase 4 — Memory and autonomy

This is the "autonomous" half of the brief. `Backend/Database.py` already has the tables; most are empty.

### 4.1 Semantic long-term memory

Right now context is the last N messages of the current session. Nothing carries across restarts, and nothing is retrieved by relevance.

Use **fastembed** — ONNX-based, no PyTorch, small download, CPU-only, works cleanly on Windows:

```
pip install fastembed
```

Add to `Backend/Database.py`:

```sql
CREATE TABLE IF NOT EXISTS memories (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    content    TEXT NOT NULL,
    kind       TEXT NOT NULL,     -- fact | preference | event
    embedding  BLOB NOT NULL,     -- float32 numpy array, .tobytes()
    importance REAL DEFAULT 0.5,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_used  DATETIME
);
```

```python
from fastembed import TextEmbedding
_embedder = TextEmbedding("BAAI/bge-small-en-v1.5")   # 384-dim, ~130MB

def remember(content, kind="fact", importance=0.5):
    vec = next(_embedder.embed([content])).astype("float32")
    conn.execute(
        "INSERT INTO memories (content, kind, embedding, importance) VALUES (?,?,?,?)",
        (content, kind, vec.tobytes(), importance))

def recall(query, k=5):
    qv = next(_embedder.embed([query])).astype("float32")
    rows = conn.execute("SELECT id, content, embedding FROM memories").fetchall()
    scored = []
    for r in rows:
        v = np.frombuffer(r["embedding"], dtype="float32")
        scored.append((float(qv @ v / (np.linalg.norm(qv) * np.linalg.norm(v))), r["content"]))
    scored.sort(reverse=True)
    return [c for s, c in scored[:k] if s > 0.35]
```

Brute-force cosine over a few thousand rows is well under a millisecond — do not add a vector database. If the table ever exceeds ~50k rows, add `sqlite-vec`.

Expose two tools so the agent manages its own memory:

```python
@tool("Save a durable fact about the user for future conversations.")
def remember_fact(fact: str) -> str: ...

@tool("Search your long-term memory about the user.")
def recall_memory(query: str) -> str: ...
```

Then inject `recall(user_query)` results into the system prompt on every turn.

### 4.2 Reminders — the table exists and nothing uses it

`Backend/Database.py` created a `reminders` table in the initial schema. Nothing ever inserts into it or reads it. Finish the feature:

```python
@tool("Set a reminder. when_iso must be an ISO-8601 datetime.")
def create_reminder(message: str, when_iso: str) -> str: ...
```

Add a `SchedulerWorker` on its own `QThread` that wakes every 30 s, selects due rows, emits a signal that pops a toast and speaks the reminder, then marks `is_completed = 1`. Use `dateparser` so the model can pass natural language ("tomorrow at 6pm") if ISO parsing fails.

### 4.3 Proactive behaviors

The step from "responds when asked" to "autonomous". Keep it small and predictable — an assistant that talks unprompted at the wrong moment is worse than one that stays quiet.

- **Morning briefing:** at a configured time, run the agent with an internal prompt ("summarize today's weather, calendar, and one headline") and speak the result.
- **Follow-ups:** if a task fails (search timed out, app failed to open), retry once quietly in the background and report only if it then succeeds.
- **Usage-driven suggestions:** the `usage_stats` table already records every query and its latency. After N similar queries, offer a shortcut.

Gate all of these behind explicit settings toggles, default off.

### 4.4 Config instead of scattered constants

Add `Backend/config.py` with a dataclass loaded from `.env` plus the `user_preferences` table (which also already exists and is unused). Wake word on/off, STT backend, voice, TTS rate, proactive toggles, mic device index, theme. The Phase 5 settings panel writes to it.

---

## Phase 5 — UI and UX overhaul

Phase 1 is the prerequisite for all of this — you cannot stream text or show live tool calls over a 1-second file poll.

### 5.1 Design tokens first

Create `Frontend/theme.py` with a single palette dict, and generate the Qt stylesheet from it. Today colours like `#00D4FF` and `rgba(20, 20, 30, 0.9)` are hardcoded in a dozen `setStyleSheet` calls, which is why the theme toggle at `GUI.py:652` cannot do much.

```python
DARK = {
    "bg":        "#0B0F14",
    "surface":   "#141A22",
    "surface_2": "#1D2530",
    "border":    "#2A3441",
    "text":      "#E6EDF3",
    "text_dim":  "#8B98A5",
    "accent":    "#00D4FF",
    "accent_2":  "#A050FF",
    "success":   "#00E678",
    "warning":   "#FFA028",
    "error":     "#FF5C5C",
}
LIGHT = { ... }   # same keys, light values
```

Every widget reads from the active dict. The theme toggle then genuinely re-themes the app.

### 5.2 Replace the single QTextEdit with real chat bubbles

`ChatSection` currently inserts HTML paragraphs into one read-only `QTextEdit` (`GUI.py:231`), with a hack that converts the first `**` pair to `<b>` and leaves every other Markdown marker as literal asterisks.

Rebuild as a `QScrollArea` containing a `QVBoxLayout` of `MessageBubble` widgets:

- User bubbles right-aligned, accent background; assistant bubbles left-aligned, surface background.
- **Markdown rendering:** `QTextEdit.setMarkdown()` (Qt 5.14+) handles headings, lists, bold, and code blocks properly. Use it instead of manual HTML.
- **Code blocks** get a monospace font, a tinted background, and a copy button.
- **Streaming:** `append_token(str)` appends to the last assistant bubble and keeps the view pinned to the bottom.
- Timestamp, and a copy button per message on hover.

### 5.3 Show the agent thinking — the single best UX addition

Phase 2 emits `tool_started` / `tool_finished`. Render them as an inline collapsible timeline inside the assistant's bubble, above the answer:

```
┌────────────────────────────────────────┐
│ 🔍 web_search("pune weather today")    │  ← appears the instant it starts
│ ✅ Found 5 results · 0.8s              │
│ ⏰ create_reminder("umbrella", 8am)     │
│ ✅ Reminder set                        │
├────────────────────────────────────────┤
│ It's going to rain in Pune today, so   │  ← streams in token by token
│ I've set a reminder for 8am.           │
└────────────────────────────────────────┘
```

Users forgive latency they can see progress through. It also makes the agentic behavior legible to anyone evaluating the project, which a plain text answer does not.

### 5.4 Make the visualizer real

`CircularVisualizer._tick()` (`GUI.py:311`) generates bar heights from `math.sin()` plus `random.uniform()`. It is decorative — it does not respond to audio at all.

Feed it the actual signal: in the audio capture loop, compute RMS per frame and emit `audio_level`. Add `setLevel(float)` to the widget and drive `_target_bars` from a short rolling FFT of the input (`numpy.fft.rfft` over the last ~1024 samples, binned to `NUM_BARS`). Keep the existing smoothing interpolation and the state-colour mapping — both are good. During TTS playback, drive it from the output stream instead so it pulses with the assistant's voice.

### 5.5 Fill in the missing UI

- **Settings panel** — `Settings.png` exists and is wired to nothing. Build a modal: mic device picker, voice selection with preview, TTS rate, wake word on/off and sensitivity, STT backend, theme, proactive toggles, "clear memory". Writes to `user_preferences`.
- **Working text input** — free from Phase 1.
- **Working file upload** — attach the file's text to the next query as context; support `.txt`, `.md`, `.pdf` (via `pypdf`), and images if you add a vision model.
- **Conversation history sidebar** — `get_all_chat_history()` already exists and returns sessions. Add a searchable list, click to reload a session.
- **Stats panel** — `get_usage_summary()` is written, imported, and never called. Show total queries, success rate, average latency, and a breakdown by tool. Good for a project report.
- **Toasts** for errors and reminders instead of silent `print()` calls.
- **Keyboard shortcuts:** `Ctrl+Space` push-to-talk, `Ctrl+K` command palette, `Esc` stop speaking, `Ctrl+L` clear, `Ctrl+,` settings.
- **Empty state** — the first-run screen should suggest three example commands rather than showing a blank pane.
- **Window behavior** — the app currently forces fullscreen at `GUI.py:707` (`setGeometry(0, 0, screen_width, screen_height)`) with `FramelessWindowHint`. Make it a normal resizable window that remembers its size, with fullscreen as an option. Add a system tray icon so it can run minimized — important for a wake-word assistant.

### 5.6 Accessibility and polish

- Check contrast ratios; `#00D4FF` on `rgba(20,20,30,.9)` passes, but dim text at `#8B98A5` needs verifying at small sizes.
- `setAccessibleName` on every interactive widget; ensure full keyboard tab order.
- Respect the OS reduced-motion setting — drop the visualizer to a static ring when set.
- Never rely on colour alone for state; pair each visualizer colour with the status label text.

---

## Phase 6 — Engineering quality

Small, but it is what makes the difference between a script and a project.

- **Tests.** `pytest` with the network mocked. Priority order: tool registry schema generation, the agent loop's tool dispatch and step ceiling, database read/write, memory recall ranking. Aim for the agent core, not the UI.
- **Logging.** Replace every `print()` with the `logging` module, writing to both console and a rotating file. Log tool calls with arguments and durations.
- **Type hints** throughout, checked with `mypy --ignore-missing-imports`. The tool registry depends on hints being accurate, so this stops being optional in Phase 2.
- **`.env.example`** must list every key the code reads — check it after each phase.
- **README rewrite.** The current one describes a different project: it claims VisPy, Three.js, Azure TTS, `pyttsx3`, `sounddevice`, `scipy`, YouTube Data API, and "Grok AI", none of which are in the code. It also has an unclosed code fence and no install or run instructions. Rewrite with real setup steps, an architecture diagram, a tool list, and a screenshot or GIF.
- **CI.** One GitHub Actions workflow: install, lint (`ruff`), type check, test.

---

## Suggested order and effort

| Phase | What | Why this order | Rough effort |
|-------|------|----------------|--------------|
| 0 | Stabilize | Live bugs; blocks clean testing | 2-3 h |
| 1 | Qt signals, kill file IPC | Every later phase needs streaming | 1 day |
| 2 | Tool calling + agent loop | The core "agentic" upgrade | 2 days |
| 3 | Wake word, STT, barge-in | Biggest felt improvement in voice | 1-2 days |
| 5.1-5.3 | Tokens, bubbles, tool timeline | Makes Phase 2 visible | 1-2 days |
| 4 | Memory, reminders, proactive | The "autonomous" half | 2 days |
| 5.4-5.6 | Visualizer, settings, a11y | Polish | 1-2 days |
| 6 | Tests, docs, CI | Wrap up | 1 day |

Phase 5.1-5.3 is deliberately pulled ahead of Phase 4 — after Phase 2 the agent can do far more than the UI can show, and closing that gap early makes the rest easier to develop against.

---

## New dependencies, by phase

```
# Phase 2
groq>=0.11.0            # already present; needs a version with tool-call support

# Phase 3
openwakeword>=0.6.0
onnxruntime>=1.17.0
sounddevice>=0.4.6
webrtcvad-wheels>=2.0.14
faster-whisper>=1.0.0   # optional, offline STT

# Phase 4
fastembed>=0.3.0
numpy>=1.24.0
dateparser>=1.2.0

# Phase 5
pypdf>=4.0.0            # file upload

# Phase 6
pytest>=8.0.0
ruff>=0.6.0
mypy>=1.11.0
```

Removed along the way: `selenium`, `googlesearch-python`, `webdriver-manager` (Phase 0); `cohere`, `pywhatkit` (Phase 2, if fully ported to tools); `pygame` (Phase 3, replaced by `sounddevice`).

---

## Target architecture

```
main.py
  └─ QApplication (main thread)
       ├─ MainWindow ──────────────── Frontend/
       │    ├─ ChatSection             theme.py       design tokens
       │    ├─ MessageBubble           widgets/       bubbles, timeline, toasts
       │    ├─ ToolTimeline            settings.py    settings modal
       │    └─ CircularVisualizer
       │
       ├─ AgentWorker (QThread) ────── Backend/
       │    ├─ wake word detection      agent.py        the tool-calling loop
       │    ├─ STT                      agent_worker.py Qt bridge
       │    ├─ agent loop               stt.py / tts.py voice pipeline
       │    └─ streaming TTS            tools/          registry.py, web.py,
       │                                                system.py, files.py,
       └─ SchedulerWorker (QThread)                     media.py, images.py,
            └─ due reminders, briefings                 memory.py
                                        database.py     SQLite (extended)
                                        config.py       settings
```

Signals flow worker → GUI. Slots flow GUI → worker. No file passes between them.

---

## Definition of done

The project is finished when all of the following are true:

1. Saying "hey jarvis" from across the room wakes it; no always-on mic loop.
2. A request needing two or more tools in sequence completes without the user re-prompting.
3. The user can interrupt the assistant mid-sentence by speaking.
4. Closing and reopening the app preserves what it knows about the user.
5. A reminder set by voice actually fires and speaks.
6. Every tool call is visible in the UI as it happens.
7. The visualizer moves in response to real audio.
8. Theme toggle re-themes the entire app.
9. `pytest` passes; `python main.py` runs from a clean clone plus `.env`.
10. The README describes the software that actually exists.
