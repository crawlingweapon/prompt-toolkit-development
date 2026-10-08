"""Headless verification of prompt_toolkit's completion API surface.

Runs without a TTY: calls completer.get_completions(document, event) directly.
Verified against prompt_toolkit 3.0.53 (pip install prompt_toolkit).

Usage:  python verify_completion_api.py
Expected output: every section prints results (see comments); a section printing
nothing means an API regression in the installed version.
"""
import asyncio

from prompt_toolkit.completion import (
    Completer, Completion, CompleteEvent, WordCompleter, FuzzyWordCompleter,
    PathCompleter, NestedCompleter, ThreadedCompleter, merge_completers,
    ConditionalCompleter, get_common_complete_suffix,
)
from prompt_toolkit.document import Document

WORDS = ["leopard", "gorilla", "dinosaur", "cat", "bee", "django_migrations"]


def main() -> None:
    print("=== 1. WordCompleter (prefix + match_middle) ===")
    wc = WordCompleter(WORDS, ignore_case=True, match_middle=True)
    for c in wc.get_completions(Document("dj", 2), CompleteEvent()):
        print(f"  {c.text!r} start_position={c.start_position}")

    print("=== 2. FuzzyWordCompleter (subsequence matching) ===")
    fw = FuzzyWordCompleter(WORDS)
    for c in fw.get_completions(Document("djm", 3), CompleteEvent()):
        print(f"  {c.text!r} start_position={c.start_position} display_text={c.display_text!r}")

    print("=== 3. Custom completer calling an 'external model' ===")
    class EmbeddingCompleter(Completer):
        def __init__(self):
            self.hits = {
                "clima": ["climate-change-report", "climate-api-docs", "climate-data-2025"],
                "wat": ["water-quality-dashboard", "water-pump-status"],
            }
        def get_completions(self, document, complete_event):
            q = document.get_word_before_cursor()
            for h in self.hits.get(q.lower(), []):
                yield Completion(text=h, start_position=-len(q), display_meta="semantic match")

    for c in EmbeddingCompleter().get_completions(Document("read the clima", 14), CompleteEvent()):
        print(f"  {c.text!r} start_position={c.start_position} meta={c.display_meta_text!r}")

    print("=== 4. CompleteEvent flags (text_inserted vs Tab) ===")
    class EventAwareCompleter(Completer):
        def get_completions(self, document, complete_event):
            if complete_event.completion_requested:
                yield Completion("tab-only-suggestion")
            elif complete_event.text_inserted:
                yield Completion("typing-suggestion")

    ea = EventAwareCompleter()
    print("  text_inserted:", [c.text for c in ea.get_completions(Document("x"), CompleteEvent(text_inserted=True))])
    print("  tab pressed:  ", [c.text for c in ea.get_completions(Document("x"), CompleteEvent(completion_requested=True))])

    print("=== 5. Async path (get_completions_async) ===")
    class AsyncEmbeddingCompleter(EmbeddingCompleter):
        async def get_completions_async(self, document, complete_event):
            await asyncio.sleep(0.01)  # real code: await model API / vector search
            for c in self.get_completions(document, complete_event):
                yield c

    async def run_async():
        async for c in AsyncEmbeddingCompleter().get_completions_async(Document("wat", 3), CompleteEvent()):
            print(f"  async -> {c.text!r}")
    asyncio.run(run_async())

    print("=== 6. Wrappers: merge / Conditional / Path / Nested ===")
    merged = merge_completers([WordCompleter(["alpha"]), WordCompleter(["beta"])], deduplicate=True)
    print("  merged:", [c.text for c in merged.get_completions(Document(""), CompleteEvent())])
    cond = ConditionalCompleter(EmbeddingCompleter(), filter=True)
    print("  conditional:", [c.text for c in cond.get_completions(Document("wat", 3), CompleteEvent())])
    print("  PathCompleter /etc/host*:", [c.display_text for c in PathCompleter().get_completions(Document("/etc/host", 9), CompleteEvent())][:3])
    nc = NestedCompleter.from_nested_dict({"show": {"version": None, "ip": {"interface": None}}, "exit": None})
    print("  NestedCompleter 'show ': ", [c.text for c in nc.get_completions(Document("show ", 5), CompleteEvent())])

    print("=== 7. Document surface + common suffix ===")
    d = Document("hello world", 8)
    print("  text_before_cursor:", repr(d.text_before_cursor))
    print("  get_word_before_cursor:", repr(d.get_word_before_cursor()))
    print("  get_word_under_cursor:", repr(d.get_word_under_cursor()))
    # cursor_position=9 -> 'wor' is before cursor; completions replace it (start_position=-3)
    d2 = Document("hello world", 9)
    print("  common suffix:", repr(get_common_complete_suffix(
        d2, [Completion("world!", start_position=-3), Completion("world?", start_position=-3)])))


if __name__ == "__main__":
    main()
