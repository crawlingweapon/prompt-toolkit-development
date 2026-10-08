# prompt_toolkit Development Skill

A Hermes agent skill for building interactive terminal apps with [prompt_toolkit](https://github.com/prompt-toolkit/python-prompt-toolkit): custom completers (including embedding/ML-backed), history (file, memory, and custom backends), fish-style autosuggest (including semantic suggestions via [fastembed](https://github.com/qdrant/fastembed)), key bindings, and styling.

Every API claim in this skill is verified by runnable headless probes — no TTY required. All results below were produced by executing the scripts against **prompt_toolkit 3.0.53** and **fastembed 0.9.0**.

## Quick start

```bash
pip install prompt_toolkit fastembed numpy
python scripts/verify_completion_api.py       # completion API surface
python scripts/verify_history_autosuggest.py  # history + autosuggest + semantic fastembed path
```

Both scripts run headlessly. Any assertion failure = API regression in the installed prompt_toolkit version. The semantic-autosuggest probe downloads `BAAI/bge-small-en-v1.5` (~130 MB, cached under `~/.cache/fastembed`) on first run and falls back to a deterministic stub vectorizer if fastembed is unavailable.

## What's inside

| Path | Contents |
|------|----------|
| `SKILL.md` | The skill: when to use, quick reference, History system, AutoSuggest system, semantic fastembed autosuggest design, aider production patterns, latency rules, pitfalls |
| `references/prompt-toolkit-completion-api.md` | Completion API detail — exact signatures, source paths, fuzzy vs incremental-search distinction, menu wiring |
| `references/history-autosuggest-api.md` | History + AutoSuggest API detail — ordering contracts, FileHistory on-disk format, custom backend templates, buffer integration |
| `references/fastembed-semantic-autocomplete.md` | Semantic history suggestions — prefix-gate + cosine design, measured latency budget, caching rules, suggester-vs-completer decision |
| `references/aider-io-patterns.md` | Annotated patterns from aider's `aider/io.py` — session lifecycle, AutoCompleter, key bindings, history hygiene, testing |
| `scripts/verify_completion_api.py` | Headless probe — completers, fuzzy matching, CompleteEvent flags, async path, wrappers, Document surface |
| `scripts/verify_history_autosuggest.py` | Headless probe — History ABC, FileHistory format roundtrip, custom JSONL backend, AutoSuggestFromHistory semantics, semantic fastembed suggester, PromptSession I/O injection |

## Highlights

- **Suggestions are append-only** — the central semantic constraint. A suggester must prefix-gate candidates or it corrupts the buffer; non-prefix substitution belongs in a completer (`Completion(start_position=-len(...))`), not a suggester.
- **Recency vs relevance** — `AutoSuggestFromHistory` picks the newest prefix match; the fastembed engine ranks by cosine similarity over cached passage vectors, gated on `len(typed) >= 3` and wrapped in `ThreadedAutoSuggest`.
- **History is a two-method extension point** — implement `load_history_strings()` (newest-first) + `store_string()` to back history with JSONL/SQLite; wrap slow loaders in `ThreadedHistory`.
- **Aider as the production reference** — one `PromptSession` per process, lazy tokenized completer, dual-write history, multiline composites as single entries, placeholder replay after interrupt, testing via `create_pipe_input`/`DummyOutput`.

## Measured latency (shared container CPU, bge-small-en-v1.5)

| Operation | Measured |
|---|---|
| Cold first query (model load) | ~380 ms |
| Warm `query_embed` per keystroke | avg ~228 ms / max ~407 ms |
| Cosine search over cached candidates | ~0.006 ms |

Hence the design rules: cache corpus vectors, embed only new history entries, gate early, thread the suggester.

## License

MIT — see [LICENSE](LICENSE).
