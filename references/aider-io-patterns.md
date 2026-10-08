---
title: Aider prompt_toolkit Usage Patterns
created: 2026-10-08
type: reference
tags: [reference, prompt-toolkit, aider, cli, patterns]
---

# Aider's prompt_toolkit patterns — annotated

Aider (`Aider-AI/aider`, `aider/io.py`, ~32 KB `InputOutput` class) is the reference implementation for a polished prompt_toolkit REPL — the "pretty good" UX the skill targets. Everything below was read from current `main` source (Oct 2026) and cross-checked against the aider docs' debugging transcript (aider.chat/examples/complex-change.html). What to steal, and why it works.

## Session lifecycle: one PromptSession per process

```python
# InputOutput.__init__ — built ONCE, reused for every prompt
session_kwargs = {
    "input": self.input,                          # injectable for tests
    "output": self.output,
    "lexer": PygmentsLexer(MarkdownLexer),        # prompt area renders as markdown
    "editing_mode": self.editingmode,             # EMACS default, VI optional
}
if self.editingmode == EditingMode.VI:
    session_kwargs["cursor"] = ModalCursorShapeConfig()  # block cursor in vi modes
if self.input_history_file is not None:
    session_kwargs["history"] = FileHistory(self.input_history_file)
self.prompt_session = PromptSession(**session_kwargs)
```

- **Per-prompt `.prompt(...)` overrides** carry the volatile config: `completer`, `style`, `key_bindings`, `complete_while_typing=True`, `reserve_space_for_menu=4`, `complete_style=CompleteStyle.MULTI_COLUMN`, `prompt_continuation`, `default=` (placeholder replay). Session holds identity; call site holds context.
- **Dumb-terminal fallback:** `is_dumb_terminal()` (from `prompt_toolkit.output.vt100`) → `fancy_input = False` → plain `input()` everywhere. The REPL degrades instead of crashing.
- `fancy_input=False` or session-init failure → non-pretty Console (`force_terminal=False, no_color=True`) and builtin `input()`. Try/except around `PromptSession(...)` — a broken terminal config shouldn't kill the tool.
- `NO_COLOR` env var forces `pretty = False` (spec compliance, one line).

## AutoCompleter — the custom completer anatomy

```python
class AutoCompleter(Completer):
    def __init__(self, root, rel_fnames, addable_rel_fnames, commands, encoding, ...):
        # words = basenames map (fname_to_rel_fnames) + rel paths + command names
        self.tokenized = False

    def tokenize(self):                      # LAZY — first Tab/keystroke, not __init__
        if self.tokenized: return
        self.tokenized = True
        for fname in self.all_fnames:        # open files only
            content = open(fname).read()     # FileNotFoundError/UnicodeDecode → skip
            lexer = guess_lexer_for_filename(fname, content)
            tokens = list(lexer.get_tokens(content))
            self.words.update((t[1], f"`{t[1]}`") for t in tokens if t[0] in Token.Name)
```

Points worth copying:
- **Lazy corpus building** — __init__ stays instant; the expensive file read + lexing happens on first completion request.
- **Pygments `Token.Name` extraction** turns any open source file into an identifier completer with zero configuration.
- **Basename → full-path secondary index** (`fname_to_rel_fnames`): typing `foo` offers both the token and the path(s) containing it.
- **Command completion via delegation:** slash commands are completed from `commands.get_completions(cmd)`, cached per command in `self.command_completions`; a `CommandCompletionException` raised by a command-specific completer falls through to normal word completion (structured escape hatch).
- **Threshold + sort:** ≥3 chars before suggesting (`if len(last_word) < 3: return`); candidates sorted; `Completion(insert, start_position=-len(last_word), display=match)`.
- **Trailing-space guard:** `if text and text[-1].isspace(): return` — don't keep completing after a space.
- Wrapped in `ThreadedCompleter(...)` at the call site — the tokenizer + lexing never block the UI.

## get_input() — the prompt call, annotated

```python
line = self.prompt_session.prompt(
    show,                                  # formatted file list + "> " prefix
    default=default,                       # placeholder replay after interrupt
    completer=completer_instance,          # ThreadedCompleter(AutoCompleter(...))
    reserve_space_for_menu=4,              # layout stability: menu space reserved
    complete_style=CompleteStyle.MULTI_COLUMN,
    style=style,                           # Style.from_dict, completion-menu colors
    key_bindings=kb,
    complete_while_typing=True,
    prompt_continuation=get_continuation,  # multiline rows repeat the prompt prefix
)
```

- `show` is built per call: rich `Columns` display of editable vs readonly files + prompt prefix (`edit_format`, `multi` tag in multiline mode). Message is context, not a constant.
- `reserve_space_for_menu=4` + `MULTI_COLUMN` is the "feels good" combination: no layout jump when the menu appears, wide lists scan horizontally.
- Per-call `completer_instance` construction is cheap (corpus is lazy/tokenized-once); the session owns history/style, the call owns the completer's view of current files.

## Key bindings catalog (all verified against source)

| Binding | Behavior | Notes |
|---|---|---|
| `c-space` | inserts a literal space | deliberately NOT the completion menu — menu appears while typing instead |
| `c-up` / `c-down` | `history_backward()` / `history_forward()` | readline-style history nav |
| `c-x c-e` | edit current buffer in external editor (`pipe_editor`), replace buffer, cursor to end | bash muscle memory preserved |
| `enter` (eager, `filter=~is_searching`) | multiline mode → insert `\n`; else `validate_and_handle()` | vi-aware: navigation mode still submits |
| `escape enter` (Alt-Enter, eager, `~is_searching`) | inverse of the above | multiline toggle pair |
| `c-z` | `app.suspend_to_background()` | gated on `hasattr(signal, "SIGTSTP")` — Windows-safe |

Patterns generalizing beyond aider:
- `eager=True` on Enter so it beats competing bindings.
- `filter=~is_searching` — never break Ctrl-R incremental search with your Enter handling.
- Vi-mode guard: `event.app.vi_state.input_mode == InputMode.NAVIGATION` decides submit vs newline.
- `Condition(lambda: hasattr(signal, "SIGTSTP"))` — capability-gated bindings instead of platform branches.

## History hygiene

```python
def add_to_input_history(self, inp):
    if not self.input_history_file: return
    FileHistory(self.input_history_file).append_string(inp)   # disk (fresh instance)
    if self.prompt_session and self.prompt_session.history:
        self.prompt_session.history.append_string(inp)        # session memory
```

- **Dual-write:** a fresh `FileHistory(path)` touches disk; `session.history.append_string` updates the live in-memory cache. Append-only on the session object — the in-memory list stays consistent with what the user will see on Up-arrow.
- **Multiline composites are ONE history entry:** `{` or `{tag}` starts block collection, lines accumulate until `}` / `tag}`, the joined text is appended once. FileHistory's `+`-continuation format stores it natively.
- `get_input_history()` = `FileHistory(path).load_history_strings()` — read back outside the prompt loop (feeds the LLM context in aider's case).

## Robustness patterns

- **Interrupt-safe placeholder:** on external interrupt (file watcher), `self.placeholder = app.current_buffer.text` then `app.exit()`; next prompt passes `default=self.placeholder` — half-typed input survives the interruption.
- **Bell/notifications:** `llm_started()` sets `bell_on_next_input`; next prompt rings `\a` or shells out to `terminal-notifier`/`notify-send` (OS-probed default command). Waiting state is visible.
- **Style dict with completion menu theming:** `Style.from_dict({"": user_color, "pygments.literal.string": ..., "completion-menu": "bg:...", "completion-menu.completion.current": "..."})` — the menu is themed like the rest of the UI, validated color strings via RichStyle before use.

## Testing pattern — and the bug it exists to catch

aider's own test migration (documented in their complex-change transcript) settled on:

```python
from prompt_toolkit.input import create_pipe_input   # or create_input(StringIO(""))
from prompt_toolkit.output import DummyOutput

pipe_input = create_pipe_input()
io = InputOutput(..., input=pipe_input, output=DummyOutput())
main(["foo.txt"], input=pipe_input, output=DummyOutput())
pipe_input.close()
```

The transcript is a masterclass in why: LLM-assisted edits first tried `prompt(..., input=..., output=...)` → `TypeError: prompt() got an unexpected keyword argument 'input'`; then `stdin=`/`stdout=` (pt 1.x names) → same failure on 3.x; the fix that shipped is **`PromptSession(input=..., output=...)`**. Verified in 3.0.53: `prompt()` has neither `input` nor `output`; `PromptSession.__init__` has both. Any prompt_toolkit test harness must construct a session, not call `prompt()`.

Also verified: `create_pipe_input()` (context manager, `send_text()` for keystrokes) and `create_input(StringIO(""))` both exist in 3.0.53. When feeding suggestions acceptances (`\x1b[C` right-arrow) through a pipe, **pace the sends from a thread** — suggestion computation is a background task; an accept sent before it lands silently no-ops (the verify script's section 8 does exactly this and asserts the accepted line).

## Steal list (minimum viable polish)

1. `PromptSession` once per process; `FileHistory` at init; per-call `.prompt()` with completer/style/bindings.
2. `ThreadedCompleter` + lazy corpus + 3-char threshold + trailing-space guard.
3. `reserve_space_for_menu=4`, `CompleteStyle.MULTI_COLUMN`, themed `completion-menu` style.
4. `complete_while_typing=True` for the main REPL prompt; `False` for yes/no confirmations (`confirm_ask` uses the same session with `complete_while_typing=False` — right call per prompt type).
5. Dual-write history on programmatic appends; multiline composites as single entries.
6. Placeholder replay on interrupt; dumb-terminal fallback; `NO_COLOR` respect.
7. Tests via `PromptSession(input=pipe, output=DummyOutput())` — never `prompt()`.
