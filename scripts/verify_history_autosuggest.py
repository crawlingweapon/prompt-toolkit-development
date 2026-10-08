"""Headless verification of prompt_toolkit history + autosuggest + fastembed design.

Runs without a TTY. Verifies, against the installed prompt_toolkit (3.0.53) and
optional fastembed:

  1. History ABC contract + FileHistory on-disk format roundtrip
  2. Custom History subclass (the extension point for semantic history)
  3. Prepopulating history + programmatic append (extending history)
  4. AutoSuggestFromHistory semantics (recency, last-line-only, suffix insert)
  5. Suggestion is APPEND-ONLY at the cursor (the key semantic constraint)
  6. Custom semantic AutoSuggest (fastembed cosine ranking, prefix-gated)
  7. ThreadedAutoSuggest wiring + get_suggestion_async
  8. PromptSession input/output injection via create_pipe_input (aider test pattern)

Usage:  python verify_history_autosuggest.py
If fastembed/model unavailable, section 6 falls back to a deterministic stub
vectorizer and prints a SKIP note — everything else still verifies.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

from prompt_toolkit.auto_suggest import (
    AutoSuggest,
    AutoSuggestFromHistory,
    Suggestion,
    ThreadedAutoSuggest,
)
from prompt_toolkit.document import Document
from prompt_toolkit.history import (
    DummyHistory,
    FileHistory,
    History,
    InMemoryHistory,
    ThreadedHistory,
)


# ---------------------------------------------------------------- 1-3. History

class JsonlHistory(History):
    """Custom History: one JSON object per line, newest last on disk.

    Demonstrates the two-method extension contract:
      load_history_strings() -> yield newest-first
      store_string(string)   -> persist one entry
    """

    def __init__(self, filename):
        self.filename = filename
        super().__init__()

    def load_history_strings(self):
        import json
        entries = []
        p = Path(self.filename)
        if p.exists():
            for line in p.read_text().splitlines():
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line)["text"])
                    except Exception:
                        continue
        return reversed(entries)  # newest first

    def store_string(self, string):
        import json
        with open(self.filename, "a") as f:
            f.write(json.dumps({"text": string, "ts": "2026-10-08"}) + "\n")


def section_history():
    print("=== 1. FileHistory on-disk format + roundtrip ===")
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "hist")
        fh = FileHistory(path)
        fh.append_string("single line")
        fh.append_string("multi\nline\nentry")
        raw = Path(path).read_text()
        print("  on-disk repr:", repr(raw))
        assert "\n# " in raw, "expected '# timestamp' header lines"
        assert "\n+" in raw, "expected '+' continuation prefix"
        got = list(fh.load_history_strings())  # one-shot generator: materialize ONCE
        print("  load_history_strings (newest first):", got)
        assert got[0] == "multi\nline\nentry"
        print("  get_strings (oldest first):", fh.get_strings())
        assert fh.get_strings() == ["single line", "multi\nline\nentry"]

    print("=== 2. Custom History subclass (JSONL) ===")
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "h.jsonl")
        jh = JsonlHistory(path)
        jh.append_string("deploy staging")
        jh.append_string("deploy prod")
        jh.append_string("rollback prod")
        print("  get_strings:", jh.get_strings())
        assert jh.get_strings() == ["deploy staging", "deploy prod", "rollback prod"]
        # reload from disk (fresh instance) — persistence works
        jh2 = JsonlHistory(path)
        reloaded = list(jh2.load_history_strings())  # contract: NEWEST first
        print("  reload from disk (newest first):", reloaded)
        assert reloaded == ["rollback prod", "deploy prod", "deploy staging"]

    print("=== 3. Prepopulate + programmatic extend + ThreadedHistory ===")
    im = InMemoryHistory(["seeded-1", "seeded-2"])
    # Seeded strings live in _storage ("emulated disk"); they enter
    # _loaded_strings only when load() runs — same pitfall as section 4.
    asyncio.run(_collect(im.load()))
    im.append_string("runtime-appended")
    print("  InMemoryHistory:", im.get_strings())
    assert im.get_strings() == ["seeded-1", "seeded-2", "runtime-appended"]

    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "th")
        FileHistory(path).append_string("slow-load-entry")
        th = ThreadedHistory(FileHistory(path))
        loaded = asyncio.run(_collect(th.load()))
        print("  ThreadedHistory.load() yielded:", loaded)
        assert loaded == ["slow-load-entry"]

    d = DummyHistory()
    d.append_string("forgotten")
    assert d.get_strings() == []
    print("  DummyHistory forgets: OK")


async def _collect(agen):
    return [x async for x in agen]


# ---------------------------------------------------------------- 4-5. AutoSuggest

def section_autosuggest():
    print("=== 4. AutoSuggestFromHistory semantics ===")
    hist = InMemoryHistory([
        "git status",
        "git push origin main",
        "ls -la",
        "git push --force-with-lease",
    ])
    # CRITICAL: seeded strings are invisible to get_strings()/AutoSuggestFromHistory
    # until History.load() runs. A real PromptSession Buffer does this at prompt
    # start; headless tests must do it themselves. (Verified pitfall.)
    asyncio.run(_collect(hist.load()))
    buff = type("B", (), {"history": hist})()  # duck-typed: only .history used

    # prefix "git pu" -> most recent match wins (force-with-lease), suffix inserted
    doc = Document("git pu", 6)
    sug = AutoSuggestFromHistory().get_suggestion(buff, doc)
    print("  'git pu' ->", repr(sug.text) if sug else None)
    # 'git pu' is 6 chars; suffix of 'git push --force-with-lease' from idx 6:
    assert sug is not None and sug.text == "sh --force-with-lease"

    # only the LAST line of a multiline buffer is considered
    doc2 = Document("git status\nls ", 14)
    sug2 = AutoSuggestFromHistory().get_suggestion(buff, doc2)
    print("  multiline last-line 'ls ' ->", repr(sug2.text) if sug2 else None)
    assert sug2 is not None and sug2.text.startswith("-la")

    # empty/whitespace -> None
    doc3 = Document("   ", 3)
    assert AutoSuggestFromHistory().get_suggestion(buff, doc3) is None
    print("  whitespace -> None: OK")

    print("=== 5. Suggestion is append-only at cursor (semantic constraint) ===")
    # The suggestion text is inserted AT the cursor — it cannot rewrite what
    # was already typed. A semantic suggester MUST prefix-gate or it corrupts.
    doc4 = Document("gi", 2)
    sug4 = AutoSuggestFromHistory().get_suggestion(buff, doc4)
    # 'gi' prefix-matches several entries; RECENCY wins: the newest
    # ('git push --force-with-lease') beats 'git status'. Suggestion = suffix only.
    print("  'gi' ->", repr(sug4.text) if sug4 else None)
    assert sug4.text == "t push --force-with-lease"
    print("  (if it returned the full line the buffer would become 'gigit push ...')")


# ---------------------------------------------------------------- 6-7. Semantic

HISTORY_CORPUS = [
    "git status",
    "git push origin main",
    "git push --force-with-lease",
    "kubectl get pods -n production",
    "kubectl rollout restart deployment/api",
    "docker compose up -d postgres",
    "make test",
    "make deploy ENV=staging",
    "psql -h db.internal -U admin",
]


class _StubEmbedder:
    """Deterministic fallback: char-trigram hashing. NOT semantic — only proves
    the plumbing (embed, cache, cosine, prefix gate) end-to-end."""

    dim = 64

    def embed(self, texts):
        import numpy as np
        for t in texts:
            v = np.zeros(self.dim)
            for i in range(len(t) - 2):
                v[hash(t[i:i + 3]) % self.dim] += 1.0
            n = (v ** 2).sum() ** 0.5 or 1.0
            yield v / n

    def query_embed(self, q):
        return self.embed([q])


class SemanticHistoryAutoSuggest(AutoSuggest):
    """fastembed (or any embedder) ranked autosuggest over a history corpus.

    Design (see references/fastembed-semantic-autosuggest.md):
      - Candidate = history lines that PREFIX-MATCH the current last line
        (Suggestion is append-only; non-prefix returns corrupt the buffer).
      - Ranking = cosine similarity(query_embed(typed), passage_embed(line)),
        NOT recency (AutoSuggestFromHistory picks most-recent; this picks
        most-relevant).
      - Embeddings cached per string; only NEW strings embedded on growth.
      - Gate: skip when typed text < min_chars (latency).
    """

    def __init__(self, corpus, embedder, min_chars=3, top_k=3):
        self.min_chars = min_chars
        self.top_k = top_k
        self._embedder = embedder
        self._corpus = list(corpus)
        self._vec_cache = {}
        self._refresh()

    def _refresh(self):
        import numpy as np
        new = [s for s in self._corpus if s not in self._vec_cache]
        if new:
            for s, v in zip(new, self._embedder.passage_embed(new) if hasattr(self._embedder, "passage_embed") else self._embedder.embed(new)):
                self._vec_cache[s] = np.asarray(v, dtype="float32")
        self._matrix = np.stack([self._vec_cache[s] for s in self._corpus])

    def extend_corpus(self, strings):
        """Extend history at runtime — new strings embedded lazily on next use."""
        self._corpus.extend(strings)
        self._refresh()

    def get_suggestion(self, buffer, document):
        import numpy as np
        text = document.text.rsplit("\n", 1)[-1]  # last line only, like the builtin
        if len(text) < self.min_chars or not text.strip():
            return None
        prefix_matches = [s for s in self._corpus if s.startswith(text)]
        if not prefix_matches:
            return None  # append-only: no safe suffix to offer
        q = np.asarray(next(self._embedder.query_embed(text)), dtype="float32")
        scored = []
        for s in prefix_matches:
            v = self._vec_cache[s]
            scored.append((float(q @ v), s))
        scored.sort(reverse=True)
        best = scored[0][1]
        return Suggestion(best[len(text):])


def _load_fastembed():
    try:
        from fastembed import TextEmbedding
        model_name = "BAAI/bge-small-en-v1.5"
        supported = [m["model"] for m in TextEmbedding.list_supported_models()]
        if model_name not in supported:
            print(f"  SKIP fastembed: {model_name} not in registry; using stub")
            return None
        emb = TextEmbedding(model_name=model_name)  # downloads to ~/.cache/fastembed once
        # probe latency on first use
        import time
        t0 = time.perf_counter()
        next(emb.query_embed("git push"))
        print(f"  fastembed {model_name}: first query_embed {1000*(time.perf_counter()-t0):.1f} ms")
        t0 = time.perf_counter()
        n = sum(1 for _ in emb.passage_embed(HISTORY_CORPUS))
        print(f"  passage_embed x{n}: {1000*(time.perf_counter()-t0):.1f} ms")
        return emb
    except Exception as e:
        print(f"  SKIP fastembed ({type(e).__name__}: {e}); using stub vectorizer")
        return None


def section_semantic():
    print("=== 6. Semantic history autosuggest (fastembed or stub) ===")
    embedder = _load_fastembed() or _StubEmbedder()
    kind = "fastembed" if not isinstance(embedder, _StubEmbedder) else "stub"
    sug_engine = SemanticHistoryAutoSuggest(HISTORY_CORPUS, embedder, min_chars=2)
    hist = InMemoryHistory(HISTORY_CORPUS)
    asyncio.run(_collect(hist.load()))  # load before AutoSuggestFromHistory can see it
    buff = type("B", (), {"history": hist})()

    tests = [
        ("git p", "most relevant 'git p*' entry (push --force or push origin)"),
        ("kubectl roll", "prefix match -> rollout restart"),
        ("docker comp", "prefix match -> compose up"),
        ("make de", "prefix match -> deploy ENV=staging"),
        ("xyz", "no prefix match -> None"),
        ("", "empty -> None"),
    ]
    for text, note in tests:
        doc = Document(text, len(text))
        sug = sug_engine.get_suggestion(buff, doc)
        full = (text + sug.text) if sug else None
        print(f"  [{kind}] {text!r:14} -> full={full!r}  ({note})")
        if text == "xyz":
            assert sug is None
        if text == "kubectl roll":
            assert full == "kubectl rollout restart deployment/api"

    # runtime corpus extension
    sug_engine.extend_corpus(["git pull --rebase", "git pull origin main"])
    doc = Document("git pull --r", 12)
    sug = sug_engine.get_suggestion(buff, doc)
    full = "git pull --r" + sug.text if sug else None
    print(f"  [{kind}] after extend 'git pull --r' -> {full!r}")
    assert full == "git pull --rebase"

    print("=== 7. ThreadedAutoSuggest + get_suggestion_async ===")
    threaded = ThreadedAutoSuggest(AutoSuggestFromHistory())
    doc = Document("git p", 5)
    result = asyncio.run(threaded.get_suggestion_async(buff, doc))
    print("  async suggestion:", repr(result.text) if result else None)
    assert result is not None


# ---------------------------------------------------------------- 8. Session test

def section_session():
    print("=== 8. PromptSession with injected input/output (aider test pattern) ===")
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput
    from prompt_toolkit.shortcuts import PromptSession

    hist = InMemoryHistory(["hello world", "hello there", "goodbye"])
    suggest = AutoSuggestFromHistory()

    with create_pipe_input() as pipe:
        session = PromptSession(
            history=hist,
            auto_suggest=suggest,
            input=pipe,
            output=DummyOutput(),
        )
        # Feed paced from a thread: suggestion computation is a background task;
        # if right-arrow arrives before it lands, the accept silently no-ops.
        import threading, time

        def feeder():
            time.sleep(0.5)  # let the session start
            pipe.send_text("hello ")
            time.sleep(0.5)  # let AutoSuggestFromHistory compute 'there'
            pipe.send_text("\x1b[C")   # right arrow: accept inline suggestion
            time.sleep(0.1)
            pipe.send_text("\r")       # Enter: accept buffer

        th = threading.Thread(target=feeder, daemon=True)
        th.start()
        result = session.prompt()
        th.join()
        print("  accepted line:", repr(result))
        assert result == "hello there"

    # prompt() itself does NOT take input=/output= in 3.0.53 (aider hit this):
    import inspect
    from prompt_toolkit.shortcuts import prompt as pt_prompt
    params = inspect.signature(pt_prompt).parameters
    assert "input" not in params and "output" not in params
    assert "input" in inspect.signature(PromptSession.__init__).parameters
    print("  prompt() lacks input=/output=; PromptSession has them: OK (aider's bug)")

    # history extension from outside the prompt loop (aider add_to_input_history):
    hist.append_string("programmatically added")
    assert "programmatically added" in hist.get_strings()
    print("  programmatic history append: OK")


def main():
    section_history()
    section_autosuggest()
    section_semantic()
    section_session()
    print("\nALL SECTIONS PASSED")


if __name__ == "__main__":
    main()
