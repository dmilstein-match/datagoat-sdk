"""Drawing a pending ask's stages on the command line (`datagoat ask ... --watch`).

Every sentence shown is the server's `message`, verbatim. Nothing here names, orders or guesses a
stage: a row appears only once the server has reported it. Times are the server's `elapsed_ms`.
`RichWatch` needs the optional extra `datagoat[watch]` (rich); `PlainWatch` needs nothing.
"""
from __future__ import annotations

import io
import sys
import time
from typing import Any, Dict, List, Optional, TextIO

#: The facts a stage event may carry, shown as counts next to the stage that first reported them.
FACT_LABELS = (("rows", "rows"), ("training_rows", "training"), ("held_out_rows", "held out"))


def _say(text: str, out: TextIO) -> None:
    from .cli import say

    say(text, out)


def _pending_stage(body: Dict[str, Any]) -> Optional[str]:
    """The stage a pending poll body names, as the server wrote it (no event stream to read)."""
    prog = body.get("progress")
    if isinstance(prog, dict) and isinstance(prog.get("stage"), str):
        return prog["stage"]
    return body.get("stage") if isinstance(body.get("stage"), str) else None


class PlainWatch:
    """One line per reported stage sentence: `<elapsed>s  <message>`. For logs and pipes."""

    def __init__(self, out: Optional[TextIO] = None) -> None:
        self.out = out or sys.stderr
        self._last: Optional[str] = None
        self._polling = False

    def _line(self, text: str) -> None:
        if text != self._last:
            self._last = text
            _say(text, self.out)

    def on_progress(self, ev: Dict[str, Any]) -> None:
        if ev.get("status") == "pending":  # a poll body: the stream is not available here
            if not self._polling:
                self._polling = True
                _say("(no event stream: polling)", self.out)
            stage = _pending_stage(ev)
            if stage:
                self._line(f"{ev.get('elapsed_ms', 0) / 1000:.1f}s  {stage}")
            return
        msg = ev.get("message")
        if isinstance(msg, str):
            self._line(f"{ev.get('elapsed_ms', 0) / 1000:.1f}s  {msg}")

    def on_event(self, ev: Dict[str, Any]) -> None:
        pass

    def close(self) -> None:
        pass


class RichWatch:
    """A live checklist: ✔ on each finished stage with the time it took, a spinner on the current
    one, and the counts the server reported."""

    def __init__(self, console: Any = None) -> None:
        from rich.console import Console
        from rich.live import Live

        self.console = console or Console(stderr=True)
        self.rows: List[Dict[str, Any]] = []
        self.finished = False
        self.polling_stage: Optional[str] = None
        self._at = time.monotonic()
        self._live = Live(self, console=self.console, refresh_per_second=8, transient=False)
        self._started = False

    def _start(self) -> None:
        if not self._started:
            self._started = True
            self._live.start()

    def on_progress(self, ev: Dict[str, Any]) -> None:
        self._start()
        if ev.get("status") == "pending":
            self.polling_stage = _pending_stage(ev)
            self._at = time.monotonic()
            self._live.refresh()
            return
        msg = ev.get("message")
        if not isinstance(msg, str):
            return
        elapsed = int(ev.get("elapsed_ms") or 0)
        key = (ev.get("stage"), ev.get("fit"))
        if self.rows and self.rows[-1]["key"] == key:
            self.rows[-1].update(message=msg, facts=ev.get("facts") or {})
        else:
            if self.rows:
                self.rows[-1]["end_ms"] = elapsed
            self.rows.append({"key": key, "message": msg, "start_ms": elapsed, "end_ms": None,
                              "facts": ev.get("facts") or {}})
        self._last_ms = elapsed
        self._at = time.monotonic()
        self._live.refresh()

    def on_event(self, ev: Dict[str, Any]) -> None:
        if ev.get("event") in ("done", "error"):
            self.finished = ev.get("event") == "done"
            if self.rows and self.rows[-1]["end_ms"] is None and self.finished:
                # `done` carries no elapsed_ms: the last stage ran until it arrived.
                last = getattr(self, "_last_ms", self.rows[-1]["start_ms"])
                self.rows[-1]["end_ms"] = last + int((time.monotonic() - self._at) * 1000)
                self.rows[-1]["done"] = True
            if self._started:
                self._live.refresh()

    def _counts(self, facts: Dict[str, Any], seen: Dict[str, Any]) -> str:
        parts = []
        for key, label in FACT_LABELS:
            if key in facts and seen.get(key) != facts[key]:
                parts.append(f"{label} {facts[key]}")
                seen[key] = facts[key]
        return " · ".join(parts)

    def __rich__(self) -> Any:
        from rich.spinner import Spinner
        from rich.table import Table
        from rich.text import Text

        t = Table.grid(padding=(0, 2))
        t.add_column(width=2)
        t.add_column()
        t.add_column(justify="right")
        t.add_column(style="dim")
        seen: Dict[str, Any] = {}
        for i, r in enumerate(self.rows):
            current = i == len(self.rows) - 1 and not r.get("done")
            counts = self._counts(r["facts"], seen)
            if not current:
                took = "" if r["end_ms"] is None else f"{(r['end_ms'] - r['start_ms']) / 1000:.1f}s"
                t.add_row(Text("✔", style="green"), Text(r["message"]), took, counts)
            else:
                running = (getattr(self, "_last_ms", r["start_ms"]) - r["start_ms"]) / 1000 + (time.monotonic() - self._at)
                t.add_row(Spinner("dots"), Text(r["message"], style="bold"), f"{running:.1f}s", counts)
        if self.polling_stage and not self.rows:
            t.add_row(Spinner("dots"), Text(self.polling_stage), "", "no event stream: polling")
        return t

    def render_text(self) -> str:
        """The checklist as plain text (for tests and logs)."""
        from rich.console import Console

        buf = io.StringIO()
        Console(file=buf, width=100, color_system=None, force_terminal=False).print(self)
        return buf.getvalue()

    def close(self) -> None:
        if self._started:
            self._live.stop()


def make_watch(out: Optional[TextIO] = None) -> Any:
    """The rich checklist on a terminal with rich installed; plain lines otherwise."""
    stream = out or sys.stderr
    if hasattr(stream, "isatty") and stream.isatty():
        try:
            import rich  # noqa: F401
        except ImportError:
            return PlainWatch(stream)
        return RichWatch()
    return PlainWatch(stream)
