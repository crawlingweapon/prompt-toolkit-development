---
title: prompt_toolkit Completion System API Reference
created: 2026-08-01
updated: 2026-10-08
type: reference
tags: [reference, prompt-toolkit, completion, api]
---

# prompt_toolkit completion system — API reference

Tested against **prompt_toolkit 3.0.53** (Aug 2026, re-verified Oct 2026). Source is authoritative; paths below are relative to `site-packages/prompt_toolkit/`.

## Source layout
- `completion/base.py` (438 ln) — Completion, Completer, CompleteEvent, ThreadedCompleter, DummyCompleter, DynamicCompleter, ConditionalCompleter, merge_completers, get_common_complete_suffix
- `completion/word_completer.py` — WordCompleter
- `completion/fuzzy_completer.py` — FuzzyCompleter, FuzzyWordCompleter, _FuzzyMatch
- `completion/filesystem.py` — PathCompleter, ExecutableCompleter
- `completion/nested.py` — NestedCompleter · `completion/deduplicate.py` — DeduplicateCompleter
- `document.py` (1183 ln) — Document (immutable, shared cache keyed by text via WeakValueDictionary)
- `search.py` — SearchState, SearchDirection, start_search/stop_search/accept_search (history search)
- `key_binding/bindings/search.py` + `key_binding/bindings/emacs.py` — Ctrl-R/Ctrl-S bindings
- `shortcuts/prompt.py` — prompt()/PromptSession wiring (completer, complete_while_typing, complete_in_thread)

Public exports of `prompt_toolkit.completion`: Completion, Completer, ThreadedCompleter, DummyCompleter, DynamicCompleter, CompleteEvent, ConditionalCompleter, merge_completers, get_common_complete_suffix, PathCompleter, ExecutableCompleter, FuzzyCompleter, FuzzyWordCompleter, NestedCompleter, WordCompleter, DeduplicateCompleter.

## Exact signatures (3.0.53)

### Completion
```python
Completion(text: str, start_position: int = 0,
           display: AnyFormattedText | None = None,
           display_meta: AnyFormattedText | None = None,
           style: str = "", selected_style: str = "")
```
- `start_position <= 0` (asserted). Negative N deletes N chars before the cursor, then inserts text.
- display defaults to text; display_meta may be a callable (lazy). Properties: `display_text`, `display_meta_text`.
- `new_completion_from_position()` — internal, used by Application when inserting the common prefix.

### Completer (ABC)
```python
@abstractmethod
def get_completions(self, document: Document, complete_event: CompleteEvent) -> Iterable[Completion]
async def get_completions_async(self, document, complete_event) -> AsyncGenerator[Completion, None]
```
Base `get_completions_async` simply iterates the sync generator — override for genuine async.

### CompleteEvent
```python
CompleteEvent(text_inserted: bool = False, completion_requested: bool = False)
```
Mutually exclusive (assert). `text_inserted` fires from complete_while_typing; `completion_requested` from explicit Tab.

### WordCompleter
```python
WordCompleter(words: Sequence[str] | Callable[[], Sequence[str]],
              ignore_case=False, display_dict=None, meta_dict=None,
              WORD=False, sentence=False, match_middle=False, pattern: Pattern | None = None)
```
- words may be a callable returning the list (dynamic).
- `sentence=True`: match against entire text_before_cursor (multi-word strings OK); mutually exclusive with WORD.
- `match_middle=True`: `word_before_cursor in word` (containment) instead of startswith.
- `pattern`: compiled regex overriding default word extraction (document._FIND_WORD_RE).

### FuzzyCompleter
```python
FuzzyCompleter(completer: Completer, WORD=False, pattern: str | None = None,
               enable_fuzzy: FilterOrBool = True)
```
- pattern must start with `^` (asserted). Defaults: `^[a-zA-Z0-9_]*` or `[^\s]+` when WORD.
- Algorithm: build a new Document with word_before_cursor stripped, run inner completer, then subsequence regex `.*?`.join(escaped chars) inside lookahead `(?=(...))`, re.IGNORECASE; best match per completion = min by (start, len); sort by (start_pos, match_length); rewritten `start_position = inner - len(word_before_cursor)`; display highlighted with `class:fuzzymatch.outside` / `fuzzymatch.inside` / `fuzzymatch.inside.character`.
- Docs: "not really a tool to work around spelling mistakes ... like what would be possible with difflib" — subsequence filtering, not typo tolerance.
- FuzzyWordCompleter = WordCompleter wrapped in FuzzyCompleter.

### PathCompleter / ExecutableCompleter
```python
PathCompleter(only_directories=False, get_paths=None, file_filter=None,
              min_input_len=0, expanduser=False)
```
- Yields `Completion(text=suffix_after_prefix, start_position=0, display=filename + "/")` for dirs; suppresses when len(text_before_cursor) < min_input_len; swallows OSError.

### Wrappers / helpers
- `NestedCompleter.from_nested_dict({...})` — None value = no further nesting; a set = shorthand for all-None dict.
- `merge_completers([...], deduplicate=False)` → _MergedCompleter, or DeduplicateCompleter when True.
- `DynamicCompleter(get_completer: Callable[[], Completer | None])`.
- `ConditionalCompleter(completer, filter)` — only yields when filter() true.
- `ThreadedCompleter` — consumes sync generator in a background thread via `generator_to_async_generator`, streams one completion at a time over a queue; source comments note it's heavy for 50k+ completions and UI invalidation is buffered.
- `get_common_complete_suffix(document, completions)` — common prefix of completion tails; returns "" if any completion changes text before the cursor.

## Document surface (what completers query)
- `.text`, `.cursor_position`, `.text_before_cursor`, `.text_after_cursor`, `.current_line_before_cursor`, `.current_line_after_cursor`, `.lines`.
- `get_word_before_cursor(WORD=False, pattern=None)` → `""` when whitespace before cursor.
- `find_start_of_previous_word(count=1, WORD=False, pattern=None)`; `get_word_under_cursor(WORD=False)`; `find_boundaries_of_current_word(...)`.
- Default word regexes: word = `([a-zA-Z0-9_]+|[^a-zA-Z0-9_\s]+)`; WORD = `[^\s]+`.
- Immutable: construct a new Document to simulate cursor movement (FuzzyCompleter does this internally).

## Incremental (history) search — separate system from completions
- `search.py`: `SearchState(text, direction=SearchDirection.FORWARD|BACKWARD, ignore_case)`, `start_search(direction=...)`, `stop_search()`, `accept_search()`.
- Bindings: `start_reverse_incremental_search` (Ctrl-R), `start_forward_incremental_search` (Ctrl-S), plus up/down while searching; filters `is_searching`, `control_is_searchable`.
- Plain substring matching over the buffer's history. No fuzzy/ranking/ML. Semantic Ctrl-R = replace these keybindings + SearchState logic.

## Wiring & menu control
- `prompt(completer=..., complete_while_typing=..., complete_in_thread=...)`; PromptSession keeps history across calls and accepts the same args at construction.
- `complete_in_thread=True` wraps the completer in ThreadedCompleter (shortcuts/prompt.py ~533).
- Buffer API for explicit menu: `buffer.complete_state` (truthy when menu open), `buffer.complete_next()`, `buffer.start_completion(select_first=False)`. Docs example binds c-space:
  ```python
  @kb.add("c-space")
  def _(event):
      buff = event.app.current_buffer
      if buff.complete_state:
          buff.complete_next()
      else:
          buff.start_completion(select_first=False)
  ```
- Docs: readthedocs "Asking for input (prompts)" → Autocompletion / Nested completion / A custom completer / Styling individual completions. **`pages/advanced_topics/completion.html` 404s** — do not link it.

## ML/embedding landscape (Aug 2026)
- No first-party ML/embedding completer; IPython & ptpython use Jedi (static AST analysis).
- GitHub repo searches for semantic/embedding completers: nothing on-target. Nearest: `stephen-bunn/prompt-toolkit-action-completer` (callable-registered completions, not ML); `fynnfluegge/codeqai` (semantic code search CLI with its own UI, not pt completions).
- User environment context: fastembed + bge-small ONNX cached under `~/.cache/fastembed`. Embedding stack is ready; the completer/suggester glue is the DIY part (~30–40 lines, see SKILL.md + fastembed reference).

## Verification
`scripts/verify_completion_api.py` exercises all of the above headlessly. Expected output highlights: `django_migrations` from "dj" (match_middle) and from "djm" (fuzzy subsequence); climate hits for "clima"; typing-vs-tab flag split; async stream; merged/conditional/path/nested results; common suffix `'ld'` for `world!`/`world?` when the cursor sits after `wor`.
