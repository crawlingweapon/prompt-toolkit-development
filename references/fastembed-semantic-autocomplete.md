---
title: Fastembed Semantic Autosuggest for prompt_toolkit
created: 2026-10-08
type: reference
tags: [reference, prompt-toolkit, fastembed, embeddings, autosuggest, history]
---

# Semantic history autosuggest with fastembed

Full design for the odd use case: rank fish-style inline suggestions by **semantic relevance** (fastembed embeddings over history) instead of recency. All latency numbers measured, all API claims verified against prompt_toolkit 3.0.53 + fastembed 0.9.0 by `scripts/verify_history_autosuggest.py`.

## The design problem

`AutoSuggestFromHistory` ranks prefix-matching history lines by **recency** — newest match wins. Typing `"gi"` in a repo where you push constantly suggests `git push --force-with-lease` (yesterday) over `git status` (the command you actually want). Semantic ranking fixes this: embed the typed prefix, embed the candidate lines once, rank by cosine similarity — the suggestion becomes "most relevant", not "most recent".

## Hard constraint: suggestions are append-only

`Suggestion.text` is inserted **at the cursor** — it appends, never rewrites. Returning a full line when the user typed a prefix doubles the text (`"gi"` + `"git status"` → `"gigit status"`). Therefore:

**Prefix-gate candidates FIRST (`line.startswith(typed)`), rank SECOND (cosine).** The ranking only decides *which* matching line completes your prefix, never *whether* the buffer is rewritten.

If you want whole-line substitution ("type 3 chars, get the full semantic match replacing what you typed"), that is a **completer**, not a suggester — `Completion(text=line, start_position=-len(typed))` replaces the typed span in the completion menu. See "Suggester vs completer" below.

## fastembed API (verified 0.9.0)

```python
from fastembed import TextEmbedding

TextEmbedding.list_supported_models()   # 41 models; check BEFORE hardcoding a name
emb = TextEmbedding(
    model_name="BAAI/bge-small-en-v1.5",  # 384-dim, ~130 MB ONNX, CPU
    cache_dir=None,                        # defaults to ~/.cache/fastembed
    threads=None,                          # set explicitly in Docker (see pitfalls)
    lazy_load=False,
)
emb.embed(documents, batch_size=256, parallel=None)  # corpus side
emb.query_embed(query)                               # query side (adds BGE instruction)
emb.passage_embed(documents)                         # corpus side, BGE-aware
emb.get_embedding_size()                             # 384 for bge-small
```

**BGE asymmetry:** BGE models are trained with different text on the query vs passage side — `query_embed()` prepends `"Represent this sentence for searching relevant passages: "`. Use `query_embed` for what the user typed, `passage_embed` (or `embed`) for history lines. Mixing sides degrades ranking.

**Model-name drift across fastembed versions:** the user's local cache (fastembed 0.8.x) holds `qdrant/bge-small-en-v1.5-onnx-q`; the 0.9.0 registry no longer lists that name (verified: 0.9.0 registry has only `BAAI/*` bge variants). Check `list_supported_models()` at startup; either pin fastembed to the version matching your cache or re-download the current name. The probe downloads `BAAI/bge-small-en-v1.5` fresh on first run and caches it.

## Architecture

```
History (FileHistory/InMemory/custom)
        │ get_strings()  (pull, oldest-first)
        ▼
SemanticHistoryAutoSuggest
  ├─ _corpus: list[str]              ← history lines (+ any static corpus)
  ├─ _vec_cache: dict[str, ndarray]  ← text → unit vector; ONLY new strings embedded
  ├─ _matrix: ndarray                ← stacked corpus vectors for one dot-product
  └─ get_suggestion(buffer, document):
        typed = document.text.rsplit("\n", 1)[-1]      # last line only (builtin parity)
        if len(typed) < min_chars: return None         # latency gate
        candidates = [s for s in _corpus if s.startswith(typed)]   # APPEND-ONLY gate
        if not candidates: return None
        q = query_embed(typed)                          # one embed per keystroke
        rank candidates by q @ _matrix                  # ~0.006 ms for ≤1000 candidates
        return Suggestion(best[len(typed):])            # suffix only
```

Corpus growth is handled by diffing: each `get_suggestion` (or a periodic refresh) computes `new = [s for s in _corpus if s not in _vec_cache]` and embeds only those. Extending history at runtime (`history.append_string(...)`) needs no other wiring — the next refresh picks the new line up. Verified: `extend_corpus(["git pull --rebase"])` then typing `"git pull --r"` suggests `ebase` immediately.

## Latency budget (measured, shared container CPU, bge-small-en-v1.5)

| Operation | Measured |
|---|---|
| Cold first `query_embed` (model load) | 378 ms |
| Warm `query_embed` per keystroke | avg 228 ms, max 407 ms |
| `passage_embed` × 5 (first batch) | 803 ms |
| Cosine search over candidates (once vectors cached) | ~0.006 ms |

Design rules that follow:
1. **Never embed the corpus per keystroke** — cache vectors; embed only new strings. (A known failure mode in the wild: re-embedding a whole history file on every call — GitHub issue IEZhu/Agents#157 reports a 13 GB RSS spike from `batch_size=256` rebuilds of a large history.)
2. **Gate on `min_chars >= 3`** — below that, suggestions are noise and you pay 200+ ms for nothing.
3. **Wrap in `ThreadedAutoSuggest`** (or override `get_suggestion_async`) — a 200+ ms sync call inside `get_suggestion` freezes the TUI. The buffer retries automatically if the document changed while the suggestion computed.
4. **Persist the vector cache** alongside the history file (`.npz` keyed by `hash(text)` or a JSONL sidecar). On startup, load cache → embed only cache misses. Cold-start cost becomes one batch, not per-entry.
5. On dedicated hardware expect substantially better than the container numbers above; the rules don't change.

## Wiring

```python
from prompt_toolkit.auto_suggest import ThreadedAutoSuggest, AutoSuggestFromHistory
from prompt_toolkit.history import FileHistory
from prompt_toolkit.shortcuts import PromptSession

history = FileHistory("~/.mycli_history")
semantic = SemanticHistoryAutoSuggest(
    corpus=history.get_strings(),        # seed; refresh() re-pulls on growth
    embedder=TextEmbedding(model_name="BAAI/bge-small-en-v1.5"),
    min_chars=3,
)
session = PromptSession(
    history=history,
    auto_suggest=ThreadedAutoSuggest(semantic),
    complete_while_typing=False,          # suggestions, not menus, while typing
)
```

Fallback pattern: `ConditionalAutoSuggest(semantic, filter=is_multiline_off)` or a `DynamicAutoSuggest` that returns `AutoSuggestFromHistory()` when the embedding model failed to load (offline cold start) — keep the prompt usable without the model.

## Suggester vs completer — choose deliberately

| | Suggester (AutoSuggest) | Completer (Completer) |
|---|---|---|
| UX | inline ghost text after cursor | dropdown menu |
| Accept | right-arrow / End | Tab / Enter |
| Text effect | **appends** suffix | **replaces** `start_position` chars before cursor |
| Semantic fit | suffix completion of history | whole-line/word substitution by relevance |
| Latency posture | every keystroke (gated+threaded) | Tab or while-typing (threaded) |

History semantic **menu** (the completer variant) — same embedding core, different surface:

```python
class HistoryCompleter(Completer):
    def get_completions(self, document, complete_event):
        typed = document.get_word_before_cursor(WORD=True)
        if len(typed) < 3:
            return
        for line, score in semantic_search(typed, top_k=8):
            yield Completion(line, start_position=-len(typed),
                             display=line, display_meta=f"hist · {score:.2f}")
```

Aider uses the completer surface for its REPL (menu of files/commands, `complete_while_typing=True`, `MULTI_COLUMN`); ghost-text suggesters suit single-line command history. Both are valid; many REPLs ship both (menu on Tab, ghost text while typing).

## Extending history + keeping the index honest

Two patterns, both verified:

1. **Pull/diff (recommended):** suggester holds a reference to the History and diffs `get_strings()` against its vector cache. `history.append_string(entry)` from anywhere (user typed it, programmatic import, completed multiline composite) is picked up on next refresh. Decoupled — History stays a pure storage backend.
2. **Push/hook:** custom `History.store_string` override also embeds and upserts the vector (eager). Faster first suggestion after append; couples storage to the model, and a failed embed must not break history persistence (wrap in try/except, fall back to lazy diff).

Custom History backends (JSONL/SQLite) are covered in `references/history-autosuggest-api.md` — the suggester doesn't care which backend supplies `get_strings()`.

## Pitfalls

- **Prefix-gate or corrupt.** Non-prefix semantic matches must never reach `Suggestion(text)` — append-only semantics will double the buffer text. The completer surface is where non-prefix substitution belongs.
- **Read `document.text`, never `buffer.text`** in `get_suggestion` — the buffer may have advanced; document is the snapshot from call time.
- **Only the last line** of a multiline buffer is suggestable (builtin parity: `document.text.rsplit("\n", 1)[-1]`).
- **`get_strings()` is empty until `History.load()` runs** — seed corpora from a freshly constructed `InMemoryHistory` and you'll rank over nothing. Real sessions load at prompt start; headless tests must `async for _ in history.load(): pass` first.
- **One-shot iterators:** `FileHistory.load_history_strings()` returns a reversed generator — materialize once (`list(...)`) or the second read is empty.
- **Query/passage asymmetry** (BGE): `query_embed` for typed text, `passage_embed` for corpus. Wrong side = silently worse rankings.
- **Full-history re-embeds blow up memory** — diff against the vector cache; never re-embed strings already in `_vec_cache`; cap `batch_size` if you must batch-embed a large backfill.
- **Model registry drift** across fastembed versions — check `list_supported_models()`; the name in your cache dir may not exist in the installed version's registry.
- **onnxruntime in Docker:** `pthread_setaffinity_np failed ... error code: 22` on stderr is harmless (affinity in restricted cgroups); pass `threads=N` to `TextEmbedding(...)` to silence. It does not affect correctness (probe passes with the noise present).
- **First-keystroke cold start** (378 ms model load) lands on the first suggestion — warm the model at startup (`next(emb.query_embed("warmup"))` in a background thread) so the user never pays it mid-sentence.
- **Whitespace before cursor** → `get_word_before_cursor()` returns `""` — gate on length before embedding.
