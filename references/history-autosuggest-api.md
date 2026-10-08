# prompt_toolkit History + AutoSuggest — API reference

Tested against **prompt_toolkit 3.0.53** (Oct 2026). Source is authoritative; paths relative to `site-packages/prompt_toolkit/`. Every claim verified by `scripts/verify_history_autosuggest.py`.

## Source layout
- `history.py` (307 ln) — History, ThreadedHistory, DummyHistory, FileHistory, InMemoryHistory
- `auto_suggest.py` (178 ln) — Suggestion, AutoSuggest, ThreadedAutoSuggest, DummyAutoSuggest, AutoSuggestFromHistory, ConditionalAutoSuggest, DynamicAutoSuggest
- `buffer.py` — Buffer integration: `_create_auto_suggest_coroutine`, `start_history_lines_completion`, `apply_completion`
- `shortcuts/prompt.py` — PromptSession wiring (history=, auto_suggest= params)

## History — exact contract (3.0.53)

### Base class `History` (ABC)
```python
class History(metaclass=ABCMeta):
    def __init__(self) -> None:
        self._loaded = False
        self._loaded_strings: list[str] = []   # newest-first in-memory cache

    # --- expected by Buffer ---
    async def load(self) -> AsyncGenerator[str, None]
        # Yields entries NEWEST-FIRST. Calls load_history_strings() once,
        # caches in _loaded_strings, re-yields cache on subsequent calls.
    def get_strings(self) -> list[str]
        # OLDEST-FIRST. EMPTY until load() has run (or append_string called).
    def append_string(self, string: str) -> None
        # Inserts at index 0 of _loaded_strings AND calls store_string().

    # --- implement in subclasses ---
    @abstractmethod
    def load_history_strings(self) -> Iterable[str]   # NEWEST-first
    @abstractmethod
    def store_string(self, string: str) -> None       # persist one entry
```

**Ordering contract (easy to get backwards):**
- `load_history_strings()` / `load()` → **newest first** (docs: "most recent items first, because they are the most important")
- `get_strings()` → **oldest first** (`_loaded_strings[::-1]`)
- `FileHistory.load_history_strings()` returns `reversed(strings)` — a **one-shot generator**; materialize with `list()` exactly once or the second consumption is empty (verified pitfall).

### Built-in implementations

| Class | Behavior |
|---|---|
| `FileHistory(filename)` | On-disk persistence (format below) |
| `InMemoryHistory(history_strings=None)` | List-backed; constructor seeds = "emulated disk" |
| `ThreadedHistory(inner)` | Runs `inner.load_history_strings()` in a background thread; entries stream in as loaded; `append_string` proxied under a lock |
| `DummyHistory()` | Forgets everything (`append_string` no-ops) — useful for `--no-history` flags |

**Deliberately absent: `DynamicHistory`.** Module docstring: "This doesn't work well, because the Buffer needs to be able to attach an event handler to the event when a history entry is loaded. This loading can be done asynchronously and making the history swappable would probably break this." To swap behavior, pass a different History per PromptSession instead.

### FileHistory on-disk format (verified roundtrip)
```
\n# 2026-10-08 03:54:10.703813\n+single line\n
\n# 2026-10-08 03:54:10.703878\n+multi\n+line\n+entry\n
```
- One `\n# <timestamp>\n` header per entry.
- Each line of the entry prefixed with `+`; multiline entries = consecutive `+` lines (loader joins lines starting with `+`, strips the final newline).
- Loader decodes bytes with `errors="replace"`, tolerates missing file, returns `reversed(strings)`.
- Same format IPython/Jupyter consoles use — cross-tool history sharing works.

### Prepopulation & runtime extension (verified)
```python
hist = InMemoryHistory(["seed1", "seed2"])   # seeds go to _storage, NOT _loaded_strings
# get_strings() == [] here! Seeds invisible until load() runs.
async for _ in hist.load(): pass             # now get_strings() == ["seed1", "seed2"]
hist.append_string("new")                    # in-memory + store_string
# get_strings() == ["seed1", "seed2", "new"]
```
- Real PromptSession Buffers call `load()` at prompt start — prepopulation "just works" interactively; headless tests must call `load()` themselves.
- Aider's `add_to_input_history(inp)`: creates a FRESH `FileHistory(path)` and calls `.append_string(inp)`, then ALSO calls `session.history.append_string(inp)` — keeps file and the session's in-memory cache in sync (append-only touches memory; the fresh FileHistory touches disk).

### Custom backend template (2 methods)
```python
class JsonlHistory(History):
    def __init__(self, filename):
        self.filename = filename
        super().__init__()
    def load_history_strings(self):
        entries = [json.loads(l)["text"] for l in open(self.filename) if l.strip()]
        return reversed(entries)              # NEWEST first
    def store_string(self, string):
        with open(self.filename, "a") as f:
            f.write(json.dumps({"text": string}) + "\n")
```
Wrap slow loaders: `PromptSession(history=ThreadedHistory(JsonlHistory(p)))`.

## AutoSuggest — exact contract (3.0.53)

```python
class Suggestion:
    def __init__(self, text: str) -> None:   # text inserted at CURSOR (append-only)

class AutoSuggest(metaclass=ABCMeta):
    @abstractmethod
    def get_suggestion(self, buffer: Buffer, document: Document) -> Suggestion | None
    async def get_suggestion_async(self, buff, document) -> Suggestion | None
        # base: just calls get_suggestion(); override for real async

class ThreadedAutoSuggest(AutoSuggest):      # runs get_suggestion in executor thread
class DummyAutoSuggest(AutoSuggest):         # always None
class AutoSuggestFromHistory(AutoSuggest):   # built-in: prefix match, recency wins
class ConditionalAutoSuggest(AutoSuggest):   # (inner, filter) — gated on/off
class DynamicAutoSuggest(AutoSuggest):       # (get_auto_suggest callable)
```

### The two critical semantics (verified)

**1. Append-only at cursor.** `Suggestion.text` is inserted after the cursor position. Returning a full line when the user typed a prefix produces doubled text (`"gi"` + `"git status"` → `"gigit status"`). Any custom suggester must return only the SUFFIX — i.e., prefix-gate candidates (`line.startswith(typed)`) before ranking.

**2. `document`, never `buffer`.** Docstring: "auto suggestions are retrieved asynchronously... the buffer text could have changed in the meantime, but `document` contains the buffer document like it was at the start of the auto suggestion call." Read `document.text`; using `buffer.text` is a race.

### AutoSuggestFromHistory internals (verified behavior)
```python
def get_suggestion(self, buffer, document):
    text = document.text.rsplit("\n", 1)[-1]      # LAST LINE ONLY
    if text.strip():
        for string in reversed(list(history.get_strings())):   # newest entry first
            for line in reversed(string.splitlines()):         # newest line first
                if line.startswith(text):
                    return Suggestion(line[len(text):])        # suffix only
    return None
```
Verified consequences:
- Multiline buffers: only the last line is considered for suggestion (`"git status\nls "` → suggests from `"ls "`).
- Whitespace-only last line → `None`.
- Recency beats relevance: with history `[git status, git push origin main, ls -la, git push --force-with-lease]`, typing `"gi"` suggests `git push --force-with-lease` (newest match), NOT `git status`.
- Depends on `history.get_strings()` — hence the `load()` pitfall above.

### Buffer integration (buffer.py)
- `_create_auto_suggest_coroutine`: fires after text insertion; skips if a suggestion already set; awaits `auto_suggest.get_suggestion_async`; sets `self.suggestion` only if the document is unchanged (else raises `_Retry`).
- Suggestion display/accept: rendered inline after cursor; right-arrow at end-of-line (or the accept binding) inserts `suggestion.text` at cursor.
- `ThreadedAutoSuggest` exists precisely so `get_suggestion` can block (embedding call, HTTP) without freezing the UI — it runs the sync method in an executor.

### Testing without a TTY (verified — aider's pattern)
```python
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.shortcuts import PromptSession

with create_pipe_input() as pipe:
    session = PromptSession(history=hist, auto_suggest=suggest,
                            input=pipe, output=DummyOutput())
    # feed paced from a thread — suggestion computation is a background task;
    # right-arrow sent before it lands silently no-ops the accept
    ...
    result = session.prompt()
```
- `prompt_toolkit.input.create_input(StringIO(""))` also works (newer alternative aider migrated to in tests).
- **`prompt()` does NOT accept `input=`/`output=`** — only `PromptSession(...)` does (verified via `inspect.signature`). This is the exact bug aider hit and fixed by switching to PromptSession.

## PromptSession vs prompt() — parameter delta (verified signature dump)
`PromptSession.__init__` extras vs `prompt()`: `input`, `output`, `default`, `placeholder`, `refresh_interval`. Shared relevant params: `history`, `auto_suggest`, `completer`, `complete_while_typing`, `complete_in_thread`, `complete_style`, `reserve_space_for_menu`, `enable_history_search`, `search_ignore_case`, `editing_mode`, `vi_mode`, `lexer`, `key_bindings`, `multiline`, `prompt_continuation`, `validator`, `style`.

## Ctrl-R incremental search vs autosuggest (two separate systems)
- Ctrl-R/Ctrl-S = history **search** (`search.py`: `SearchState`, `SearchDirection.FORWARD/BACKWARD`), plain substring over buffer history, bound in `key_binding/bindings/search.py`.
- Autosuggest = inline ghost text (`auto_suggest.py`), suffix-append, right-arrow accept.
- They share only the History data. Replacing one does not touch the other. Semantic Ctrl-R = custom keybindings + SearchState logic (moderately hard); semantic ghost text = custom AutoSuggest (easy, see fastembed reference).
