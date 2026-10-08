---
name: prompt-toolkit-development
description: "prompt_toolkit: completers, history, autosuggest, fastembed."
version: 2.0.0
author: tony, Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [prompt-toolkit, cli, tui, completion, history, autosuggest, fastembed, embeddings]
title: prompt_toolkit Development Skill
created: 2026-10-08
type: reference
tags: [reference, prompt-toolkit, cli, skills, completion, history, autosuggest, fastembed]
---

# prompt_toolkit Interactive Terminal Apps

Build CLIs/REPLs with `prompt()` / `PromptSession` — completers (including ML/embedding-backed), history (file, memory, custom backends), fish-style autosuggest (including semantic/fastembed-backed), key bindings, and styling. All API claims below were verified by running code against the installed library; re-run the scripts in `scripts/` after any version bump.

## When to Use
- Building CLIs/REPLs with `prompt()` / `PromptSession` (autocompletion, syntax highlighting, key bindings).
- Writing custom `Completer` classes — including ones that call an LLM, embedding model, or vector store.
- Customizing or extending **history** (persisted entries, custom backends, programmatic appends, prepopulation).
- Customizing or extending **autosuggest** (fish-style inline suggestions) — including semantic suggestions from an embedding model over history.
- Questions about completion behavior: fuzzy matching, incremental search (Ctrl-R), `complete_while_typing`.
- Researching the prompt_toolkit API surface for a feature. (API knowledge verified against **3.0.53**, Oct 2026.)

Don't use for: full-screen TUI apps (use Textual/Rich); data-heavy triage UIs (see Pitfalls — web UI is the right tool).

## Prerequisites
- `pip install prompt_toolkit` (3.x). For the semantic-autosuggest patterns: `pip install fastembed numpy` — any embedder works, fastembed is the local/CPU default.
- No API keys needed for any pattern here (fastembed runs local ONNX models).

## How to Run
- Verify the whole surface headlessly: `terminal(command="python scripts/verify_completion_api.py")` then `terminal(command="python scripts/verify_history_autosuggest.py")`. Both run without a TTY; every section prints results, and any assertion failure = API regression in the installed version.
- Scripts download `BAAI/bge-small-en-v1.5` (~130 MB, cached under `~/.cache/fastembed`) on first run; they fall back to a deterministic stub vectorizer if fastembed/model is unavailable.

## Quick Reference
- `prompt(message, **kwargs)` — one-shot prompt. **No `input=`/`output=` params** (use `PromptSession` for injected I/O, see Pitfalls).
- `PromptSession(...)` — reusable session; create ONCE, call `.prompt()` per input (aider pattern). Extra params vs `prompt()`: `input`, `output`, `default`, `placeholder`, `refresh_interval`.
- History: `FileHistory(path)` · `InMemoryHistory([...])` · `ThreadedHistory(inner)` · `DummyHistory()`. Extend: subclass `History` (implement `load_history_strings` + `store_string`) or call `session.history.append_string(s)` at runtime.
- Autosuggest: `AutoSuggestFromHistory()` · `ThreadedAutoSuggest(inner)` · custom `AutoSuggest.get_suggestion(buffer, document)`. Wrap any slow suggester in `ThreadedAutoSuggest`.
- Completion menu styles: `CompleteStyle.COLUMN | MULTI_COLUMN | READLINE_LIKE`. Menu sizing: `reserve_space_for_menu=N`.
- Testing without a TTY: `PromptSession(input=create_pipe_input(), output=DummyOutput())`, feed with `pipe.send_text(...)`.

## History System (verified — see references/history-autosuggest-api.md for exact semantics)
- **Contract:** implement `load_history_strings()` (yield **newest-first**) + `store_string(string)`. The base class handles caching; `get_strings()` returns **oldest-first**; `append_string()` inserts in-memory + calls `store_string()`.
- **FileHistory on-disk format:** `\n# <timestamp>\n` header per entry, each line prefixed `+` (multiline entries = consecutive `+` lines). Same format IPython uses.
- **Prepopulation:** `InMemoryHistory(["seed1", "seed2"])` — seeds live in "storage" and are only visible to `get_strings()` / `AutoSuggestFromHistory` **after `load()` runs** (real sessions do this at prompt start; headless tests must call it).
- **Runtime extension:** `session.history.append_string(entry)` — aider writes to BOTH a fresh `FileHistory(path)` and `session.history` to keep file + memory in sync.
- **Custom backend = 2 methods** (e.g., JSONL/SQLite-backed `History` subclass). Wrap slow loaders in `ThreadedHistory` — entries stream in as they load.
- Pitfalls: `load_history_strings()` may return a one-shot reversed iterator (materialize with `list()` once); `get_strings()` is empty until `load()`; there is deliberately **no `DynamicHistory`** (the Buffer attaches load-event handlers; swappable history breaks async loading).

## Autosuggest System (verified — fish-style inline ghost text)
- `Suggestion(text)` — **append-only at the cursor**. The suggestion is inserted AFTER what's typed; it cannot rewrite the buffer. A semantic suggester MUST prefix-gate candidates or it corrupts input (`"gi"` + full-line suggestion → `"gigit ..."`).
- `AutoSuggest.get_suggestion(buffer, document) -> Suggestion | None` — receives both; use `document.text` (snapshot at call time), **never `buffer.text`** (may have changed — suggestions are computed async).
- `AutoSuggestFromHistory` — built-in: considers only the **last line** of the document; among prefix-matching history lines, **recency wins** (iterates newest-first); returns the suffix. Verified: `'gi'` → most recent `git push --force-with-lease`, not the top-of-history `git status`.
- `get_suggestion_async(buff, document)` — override for real async work; the base just calls the sync method. Wrap sync suggesters in `ThreadedAutoSuggest` so embedding/LLM calls don't block the UI.
- Buffer integration: suggestions are computed in a background task after text insertion; right-arrow (cursor at end) or End inserts the suggestion. If the buffer changed before the task lands, it retries — hence paced input in tests.

## Semantic Autosuggest with fastembed (the odd use case)
Embeddings rank history suggestions by **relevance** instead of recency. Design (full reference: `references/fastembed-semantic-autocomplete.md`):

1. **Prefix-gate first, rank second.** Candidates = history lines starting with the typed last line (append-only constraint). Then rank by cosine(`query_embed(typed)`, `passage_embed(line)`).
2. **Cache corpus embeddings**; embed only NEW strings when history grows (`extend_corpus()` pattern). Per-keystroke cost = one `query_embed` + a dot product (0.006 ms for 5–1000 candidates once vectors exist).
3. **Latency is real on CPU:** measured warm `query_embed` ≈ 228 ms avg / 407 ms max (bge-small-en-v1.5, shared container CPU). Gate suggestions on `len(typed) >= 3`, wrap in `ThreadedAutoSuggest`, and never embed the corpus per keystroke.
4. Choose the mechanism deliberately: **suggestions append** (inline ghost text, right-arrow accept) vs **completions replace** (`Completion(text, start_position=-len(typed))`, Tab menu — can substitute whole words). Semantic whole-line substitution belongs in a completer; suffix completion of history belongs in a suggester.

## Custom Completer calling an external model (~30 lines)
```python
from prompt_toolkit.completion import Completer, Completion

class EmbeddingCompleter(Completer):
    def get_completions(self, document, complete_event):
        query = document.get_word_before_cursor()
        if len(query) < 2:
            return
        vec = embed(query)   # fastembed / any embedding model
        for text, score in vector_store.search(vec, top_k=10):
            yield Completion(text=text, start_position=-len(query),
                             display_meta=f"score {score:.2f}")
```
Wire: `PromptSession(completer=ThreadedCompleter(EmbeddingCompleter()), complete_while_typing=True)`.

## Two-Layer Completer Architecture (NestedCompleter + EmbeddingCompleter)
For CLIs with a defined tool/command grammar, use two complementary layers merged into one dropdown:

1. **NestedCompleter** (built-in, grammar layer): handles syntax — valid tokens at each position. Dict-based, zero custom code. `cxone` → shows `show`, `scan`, `export`.
2. **EmbeddingCompleter** (custom, semantic layer): handles intent — matches on tool descriptions/metadata, not just names. `scan for vulns` → shows `cxone_scan` (semantic match on description).

```python
from prompt_toolkit.completion import (
    NestedCompleter, merge_completers, ThreadedCompleter
)

# Layer 1: Grammar (built-in) — what tokens are valid at each position
grammar = NestedCompleter.from_nested_dict({
    "cxone": {"show": {"archive": None, "findings": None}, "scan": None},
    "archive": {"show": {"cxone": None, "checkmarx": None}},
})

# Layer 2: Semantic (custom Completer) — what does this tool do
class EmbeddingCompleter(Completer):
    def __init__(self, corpus, embedder):
        # corpus: list of {name, desc, params} dicts
        self.corpus = corpus
        self.embeddings = embedder([t["desc"] for t in corpus])

    def get_completions(self, document, complete_event):
        query = document.get_word_before_cursor()
        if len(query) < 2:
            return
        vec = embed(query)
        for i, score in cosine_search(vec, self.embeddings):
            yield Completion(
                text=self.corpus[i]["name"],
                start_position=-len(query),
                display_meta=f"semantic · {score:.2f}",
            )

# Merge — both run through the same async pipeline, same dropdown
semantic = ThreadedCompleter(EmbeddingCompleter(corpus, embedder))
combined = merge_completers([grammar, semantic])

session = PromptSession(
    completer=combined,
    complete_while_typing=True,
    complete_in_thread=True,
)
```

**What gets embedded for the semantic layer:** tool descriptions, parameter docs, usage examples — the same base corpus as NestedCompleter but at deeper semantic depth. NestedCompleter handles names/syntax; EmbeddingCompleter handles descriptions/intent.

**When to use two layers vs just NestedCompleter:**
- NestedCompleter alone: you know your tools, want fast syntax completion, corpus < 100 items
- Add EmbeddingCompleter: 100+ tools, users don't remember all names, want discovery by intent ("scan for vulns" → `cxone_scan`)

## Aider Production Patterns (what "pretty good" looks like in practice)
Aider (`aider/io.py`) is the reference implementation for a polished prompt_toolkit REPL. Condensed (full detail: `references/aider-io-patterns.md`):
- **One `PromptSession` per process**, built in `__init__` with `history=FileHistory(...)`, `lexer=PygmentsLexer(MarkdownLexer)`, `editing_mode`, `cursor=ModalCursorShapeConfig()` (vi). Per-input `.prompt(...)` overrides pass `completer`, `style`, `key_bindings`, `complete_while_typing=True`, `reserve_space_for_menu=4`, `complete_style=CompleteStyle.MULTI_COLUMN`, `prompt_continuation`.
- **Custom `AutoCompleter`:** lazy Pygments tokenization of open files (identifiers → completions), slash-command completion with a `CommandCompletionException` fallthrough, 3-char minimum before suggesting, sorted output.
- **Key bindings that matter:** `c-space` inserts a literal space (menu appears while typing instead), `c-up`/`c-down` history nav, `c-x c-e` external editor on current buffer, `enter`/`alt-enter` multiline toggle (guarded by `filter=~is_searching` + vi-mode checks).
- **History hygiene:** `add_to_input_history()` appends to a fresh `FileHistory(path)` AND `session.history`; multiline composites are appended as ONE entry.
- **Robustness:** dumb-terminal detection → plain `input()` fallback; placeholder saved on interrupt and replayed as `default=` next prompt.

## Latency rules (pt 3.x)
- Completions are **synchronous** — a slow model call freezes the event loop.
- `ThreadedCompleter` (or `complete_in_thread=True` on PromptSession) streams results from a background thread; UI shows them as they arrive.
- Override `get_completions_async` (real async generator) for non-blocking LLM/vector calls.
- Cache embedded candidates; embed only the query per keystroke. Same for suggesters: cache corpus vectors, one query embed per keystroke, `ThreadedAutoSuggest` wrapper.

## Pitfalls
- `get_word_before_cursor()` returns `""` when there's whitespace before the cursor — gate on length.
- `WordCompleter`: `WORD` and `sentence` are mutually exclusive (assert). `sentence=True` matches against ALL text before cursor (strings may contain spaces).
- `FuzzyCompleter(pattern=...)` requires the pattern to start with `^` (asserted).
- `get_common_complete_suffix` returns `""` if any completion would alter text before the cursor (filters them out first).
- `Document` is immutable — to "pretend" the cursor moved, construct a new Document (FuzzyCompleter does exactly this internally).
- Explicit-menu keybinding pattern (docs): `buff.complete_state` → `buff.complete_next()` else `buff.start_completion(select_first=False)` (e.g. bound to c-space).
- Docs pitfall: `readthedocs pages/advanced_topics/completion.html` **404s** — the completion docs live under "Asking for input (prompts)" → Autocompletion.
- **`prompt()` has no `input=`/`output=`** — only `PromptSession` does (verified 3.0.53). Aider shipped this bug: tests failed with `TypeError: prompt() got an unexpected keyword argument 'input'` before they switched to `PromptSession(input=..., output=...)`.
- **History ordering traps:** `load_history_strings()` yields newest-first and may be a one-shot generator; `get_strings()` returns oldest-first and is **empty until `load()` runs**. `InMemoryHistory([...])` seeds are invisible to `AutoSuggestFromHistory` until the buffer loads history.
- **Suggestion is append-only** — a semantic suggester returning non-prefix text corrupts the buffer. Prefix-gate candidates before ranking.
- **Suggestion vs completion semantics:** `Suggestion.text` appends at cursor; `Completion(start_position=-N)` replaces N chars before cursor. Whole-word semantic substitution = completer; suffix ghost-text = suggester.
- **Suggesters read `document.text`, not `buffer.text`** — the buffer may have moved on; document is the snapshot from call time.
- **`ThreadedHistory` load ordering:** appends before the load thread starts get re-derived from storage; append after load for predictable ordering.
- **TUI scale limit**: prompt_toolkit is fine for single-issue review but painful for browsing/filtering 100K+ records. For data-heavy triage, web UI (Marimo/Streamlit) is necessary. If the tool was designed for automation and needs human review at scale, plan the web UI from the start — retrofitting is costly.
- **onnxruntime in Docker/containers:** `pthread_setaffinity_np failed` errors on stderr are harmless noise (CPU affinity in restricted cgroups); pass `threads=` explicitly to silence if it bothers you.

## ML/embedding landscape (as of Oct 2026)
- No first-party ML completer. IPython/ptpython use **Jedi** (static AST analysis, not embeddings).
- No notable off-the-shelf semantic completer/suggester for prompt_toolkit — it's DIY, and the API makes it ~30 lines (completer) / ~40 lines (suggester, see references).
- Verified stack here: fastembed 0.9.0 + `BAAI/bge-small-en-v1.5` (41 models in registry; ONNX CPU; cached under `~/.cache/fastembed`). Use `query_embed()` for the typed query and `passage_embed()` for corpus lines (BGE is asymmetric — queries carry an instruction prefix).
- Measured latency (shared container CPU, bge-small): cold first query 378 ms; warm `query_embed` avg 228 ms / max 407 ms; cosine search over candidates ~0.006 ms. On dedicated hardware expect better; the design rules (gate, thread, cache) don't change.

## Technique: researching a Python library API cheaply
1. `pip install <lib>` in the sandbox; read the installed source in site-packages — authoritative and version-exact.
2. Write a **headless probe**: call the API directly with synthetic inputs (`Document("dj", 2)` + `CompleteEvent()`), no TTY/interactive session needed. Fast, proof-based, re-runnable.
3. Keep the probe as a script for future regression checks — both `scripts/verify_*.py` files here are exactly that.

## Support files
- `scripts/verify_completion_api.py` — headless verification of the completion API surface.
- `scripts/verify_history_autosuggest.py` — headless verification of History + AutoSuggest + semantic (fastembed) autosuggest + PromptSession I/O injection.
- `references/prompt-toolkit-completion-api.md` — completion API detail: exact signatures, source paths, internals, history-search machinery, wiring.
- `references/history-autosuggest-api.md` — History + AutoSuggest API detail: exact signatures, ordering contracts, FileHistory format, buffer integration, custom backend templates.
- `references/fastembed-semantic-autocomplete.md` — full semantic-autosuggest design: architecture, code, latency budget, caching, suggester-vs-completer decision.
- `references/aider-io-patterns.md` — annotated aider (`aider/io.py`) production patterns: session lifecycle, AutoCompleter, key bindings, history hygiene, testing.
