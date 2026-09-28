"""Watching a pending ask: the task's event stream, its resume, and the fall back to polling.

A local server scripts GET /v1/tasks/{id}/events as text/event-stream, the way WATCH.md fixes it:
`id`, `event`, `data` (JSON) per event, comment heartbeats, a resume by Last-Event-ID, and an end
at `done` or `error`. Stage sentences come from the server's `message`; nothing here makes one up.
"""
import io
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from datagoat import Client, DatagoatError, cli, yesno

PENDING = {"status": "pending", "task_id": "tk_w", "retry_after_ms": 0}
DONE = {"status": "done", "task_id": "tk_w", "answers": {"churn": {"type": "yesno", "state": "answered", "verdicts": []}}}


def stage(eid, name, message, elapsed_ms, **extra):
    data = {"task_id": "tk_w", "stage": name, "facts": extra.pop("facts", {}), "message": message,
            "elapsed_ms": elapsed_ms, **extra}
    return f"id: {eid}\nevent: stage\ndata: {json.dumps(data)}\n\n"


S1 = stage(1, "reading_data", "Reading your record", 10)
S2 = stage(2, "profiling", "Profiling 1200 rows · outcome: churned", 900, frac=0.05,
           facts={"rows": 1200, "outcome": "churned"})
S3 = stage(3, "splitting", "Holding out 240 rows the search never sees", 1500, frac=0.3,
           facts={"rows": 1200, "outcome": "churned", "training_rows": 960, "held_out_rows": 240})
ANSWER = 'id: 4\nevent: answer\ndata: {"task_id": "tk_w", "question": "churn", "state": "answered", "decision": "act"}\n\n'
DONE_EV = 'id: 5\nevent: done\ndata: {"task_id": "tk_w", "status": "done"}\n\n'


class Fake:
    """POST /v1/ask and /v1/poll answer from `posts`; each GET of the events route plays the next
    connection script: a list of chunks (str), then "drop" (close mid-stream) or "hold" (keep the
    connection open, sending heartbeats), or an int status for a plain error response."""

    def __init__(self, posts, connections):
        self.posts = list(posts)
        self.connections = list(connections)
        self.seen = []
        fake = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def do_POST(self):
                n = int(self.headers.get("content-length", 0))
                self.rfile.read(n)
                fake.seen.append(("POST", self.path, None))
                body = json.dumps(fake.posts.pop(0)).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                fake.seen.append(("GET", self.path, {"last-event-id": self.headers.get("last-event-id"),
                                                     "authorization": self.headers.get("authorization"),
                                                     "accept": self.headers.get("accept")}))
                script = fake.connections.pop(0) if fake.connections else 404
                if isinstance(script, int):
                    body = json.dumps({"code": "not_found", "detail": "no such route", "remedy": "poll"}).encode()
                    self.send_response(script)
                    self.send_header("content-type", "application/problem+json")
                    self.send_header("content-length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.send_header("cache-control", "no-store")
                self.send_header("connection", "close")
                self.end_headers()
                for chunk in script:
                    if chunk == "drop":
                        break
                    if chunk == "hb1s":  # heartbeats for 1.2 s
                        for _ in range(12):
                            self.wfile.write(b": hb\n\n")
                            self.wfile.flush()
                            time.sleep(0.1)
                        continue
                    if chunk == "silent":  # keep the connection open, sending nothing
                        time.sleep(6)
                        break
                    if chunk == "hold":
                        try:
                            for _ in range(200):
                                self.wfile.write(b": keep-alive\n\n")
                                self.wfile.flush()
                                time.sleep(0.05)
                        except OSError:
                            pass
                        break
                    self.wfile.write(chunk.encode())
                    self.wfile.flush()
                    time.sleep(0.05)  # separate reads, so a CR can end one and LF start the next
                self.close_connection = True

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.srv.daemon_threads = True
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_port}"

    def paths(self):
        return [(m, p) for m, p, _ in self.seen]

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


def ask(url, **kw):
    return Client(api_key="dgk_test_w", base_url=url).ask({"churn": yesno("churned")}, dataset_id="sample:saas_churn",
                                                          entity_column="customer_id", subject_kind="org",
                                                          cases={"ids": ["c1"]}, **kw)


def test_events_iterates_the_stream_skips_heartbeats_and_stops_at_done():
    split = 'id: 3\nevent: stage\ndata: {"task_id": "tk_w", "stage": "splitting",\ndata:  "message": "m3", "elapsed_ms": 3}\r\n\r\n'
    f = Fake([], [[": hello\n\n", S1, ": keep-alive\n\n", S2, split, ANSWER, DONE_EV, "hold"]])
    try:
        evs = list(Client(api_key="dgk_test_w", base_url=f.url).events("tk_w"))
    finally:
        f.close()
    assert [(e["event"], e["id"]) for e in evs] == [("stage", 1), ("stage", 2), ("stage", 3), ("answer", 4), ("done", 5)]
    assert evs[1]["data"]["message"] == "Profiling 1200 rows · outcome: churned"
    assert evs[2]["data"] == {"task_id": "tk_w", "stage": "splitting", "message": "m3", "elapsed_ms": 3}
    (m, path, h), = f.seen
    assert (m, path) == ("GET", "/v1/tasks/tk_w/events")
    assert h["authorization"] == "Bearer dgk_test_w" and h["accept"] == "text/event-stream" and h["last-event-id"] is None


def test_a_dropped_stream_resumes_with_last_event_id():
    f = Fake([], [[S1, S2, "drop"], [S3, DONE_EV]])
    try:
        evs = list(Client(api_key="dgk_test_w", base_url=f.url).events("tk_w"))
    finally:
        f.close()
    assert [e["id"] for e in evs] == [1, 2, 3, 5]
    assert [h["last-event-id"] for _, _, h in f.seen] == [None, "2"]


def test_events_raises_the_problem_when_there_is_no_stream():
    f = Fake([], [404])
    try:
        with pytest.raises(DatagoatError) as e:
            list(Client(api_key="dgk_test_w", base_url=f.url).events("tk_w"))
    finally:
        f.close()
    assert e.value.problem.status == 404


def test_ask_on_progress_gets_each_stage_event_verbatim_then_the_answer_is_polled():
    f = Fake([PENDING, DONE], [[S1, S2, S3, ANSWER, DONE_EV, "hold"]])
    got, raw = [], []
    try:
        out = ask(f.url, on_progress=got.append, on_event=raw.append)
    finally:
        f.close()
    assert out == DONE
    assert [g["message"] for g in got] == ["Reading your record", "Profiling 1200 rows · outcome: churned",
                                           "Holding out 240 rows the search never sees"]
    assert got[2]["facts"] == {"rows": 1200, "outcome": "churned", "training_rows": 960, "held_out_rows": 240}
    assert [e["event"] for e in raw] == ["stage", "stage", "stage", "answer", "done"]
    assert f.paths() == [("POST", "/v1/ask"), ("GET", "/v1/tasks/tk_w/events"), ("POST", "/v1/poll")]


def test_ask_resumes_a_dropped_stream_without_polling_in_between():
    f = Fake([PENDING, DONE], [[S1, "drop"], [S2, S3, DONE_EV]])
    got = []
    try:
        assert ask(f.url, on_progress=got.append) == DONE
    finally:
        f.close()
    assert [g["stage"] for g in got] == ["reading_data", "profiling", "splitting"]
    assert f.paths() == [("POST", "/v1/ask"), ("GET", "/v1/tasks/tk_w/events"), ("GET", "/v1/tasks/tk_w/events"),
                         ("POST", "/v1/poll")]
    assert f.seen[2][2]["last-event-id"] == "1"


def test_a_server_without_the_stream_falls_back_to_polling():
    f = Fake([PENDING, PENDING, DONE], [404])
    got = []
    try:
        assert ask(f.url, on_progress=got.append) == DONE
    finally:
        f.close()
    assert f.paths() == [("POST", "/v1/ask"), ("GET", "/v1/tasks/tk_w/events"), ("POST", "/v1/poll"), ("POST", "/v1/poll")]
    # the 1.8.0 behaviour, unchanged: each pending poll body
    assert got == [PENDING, PENDING]


def test_a_stream_that_drops_and_cannot_be_reopened_falls_back_to_polling():
    f = Fake([PENDING, DONE], [[S1, "drop"], 503])
    got = []
    try:
        assert ask(f.url, on_progress=got.append) == DONE
    finally:
        f.close()
    assert [g.get("stage") for g in got] == ["reading_data", None]
    assert got[1] == PENDING
    assert f.paths()[-1] == ("POST", "/v1/poll")


def test_the_stream_keeps_the_ask_deadline():
    f = Fake([PENDING], [[S1, "hold"]])
    try:
        t0 = time.monotonic()
        with pytest.raises(DatagoatError) as e:
            ask(f.url, on_progress=lambda ev: None, timeout_s=1.0)
    finally:
        f.close()
    assert e.value.problem.code == "poll_timeout" and time.monotonic() - t0 < 8


def test_without_a_callback_the_stream_is_never_opened():
    f = Fake([PENDING, DONE], [[S1, DONE_EV]])
    try:
        assert ask(f.url) == DONE
    finally:
        f.close()
    assert f.paths() == [("POST", "/v1/ask"), ("POST", "/v1/poll")]


def cli_env(monkeypatch, f):
    monkeypatch.setenv("DATAGOAT_API_KEY", "dgk_test_w")
    monkeypatch.setenv("DATAGOAT_BASE_URL", f.url)


ARGS = ["ask", '{"churn": {"type": "yesno", "outcome_column": "churned"}}', "--data", "sample:saas_churn",
        "--entity", "customer_id", "--cases", "c1"]


def test_cli_watch_prints_plain_lines_when_not_a_tty(monkeypatch, capsys):
    f = Fake([PENDING, DONE], [[S1, S2, S3, DONE_EV]])
    cli_env(monkeypatch, f)
    try:
        assert cli.main(ARGS + ["--watch"]) == 0
    finally:
        f.close()
    cap = capsys.readouterr()
    lines = [ln for ln in cap.err.splitlines() if ln.strip()]
    assert [ln.split("  ", 1)[1] for ln in lines[:3]] == [
        "Reading your record", "Profiling 1200 rows · outcome: churned", "Holding out 240 rows the search never sees"]
    assert "\x1b[" not in cap.err
    assert json.loads(cap.out.rsplit("verify:", 1)[0]) == DONE and "verify: valid" in cap.out


def test_cli_events_prints_json_lines(monkeypatch, capsys):
    f = Fake([PENDING, DONE], [[S1, S2, ANSWER, DONE_EV]])
    cli_env(monkeypatch, f)
    try:
        assert cli.main(ARGS + ["--events"]) == 0
    finally:
        f.close()
    out = [json.loads(ln) for ln in capsys.readouterr().out.splitlines()]
    assert [o.get("event") for o in out[:4]] == ["stage", "stage", "answer", "done"]
    assert out[1]["data"]["message"] == "Profiling 1200 rows · outcome: churned"
    assert out[-1] == DONE


def test_cli_events_without_a_stream_prints_the_pending_polls(monkeypatch, capsys):
    f = Fake([PENDING, DONE], [404])
    cli_env(monkeypatch, f)
    try:
        assert cli.main(ARGS + ["--events"]) == 0
    finally:
        f.close()
    out = [json.loads(ln) for ln in capsys.readouterr().out.splitlines()]
    assert out == [PENDING, DONE]


def test_the_rich_checklist_ticks_finished_stages_and_shows_the_message_verbatim():
    rich = pytest.importorskip("rich")
    from rich.console import Console
    from datagoat.watch import RichWatch

    con = Console(file=io.StringIO(), force_terminal=True, width=100, record=True)
    w = RichWatch(console=con)
    for s in (S1, S2, S3):
        w.on_progress(json.loads(s.split("data: ", 1)[1]))
    text = w.render_text()
    assert text.count("✔") == 2
    assert "Reading your record" in text and "Profiling 1200 rows · outcome: churned" in text
    assert "Holding out 240 rows the search never sees" in text
    assert "0.9s" in text  # reading_data ran from 10 ms to 900 ms by the server's clock
    w.on_event({"event": "done", "id": 5, "data": {"task_id": "tk_w", "status": "done"}})
    assert w.render_text().count("✔") == 3
    w.close()
    assert rich is not None


# -- review fixes ---------------------------------------------------------------------------------
JP = stage(2, "profiling", "Profiling 1200 rows · outcome: 退会", 900, frac=0.05)


def cp1252(monkeypatch, name):
    buf = io.BytesIO()
    w = io.TextIOWrapper(buf, encoding="cp1252", newline="\n", write_through=True)
    monkeypatch.setattr(__import__("sys"), name, w)
    return buf


def test_cli_events_survive_a_cp1252_stdout(monkeypatch):
    f = Fake([PENDING, DONE], [[S1, JP, DONE_EV]])
    cli_env(monkeypatch, f)
    out = cp1252(monkeypatch, "stdout")
    cp1252(monkeypatch, "stderr")
    try:
        assert cli.main(ARGS + ["--events"]) == 0
    finally:
        f.close()
    lines = [json.loads(ln) for ln in out.getvalue().decode("cp1252").splitlines()]
    assert lines[1]["data"]["message"] == "Profiling 1200 rows · outcome: 退会"
    assert lines[-1] == DONE


def test_cli_watch_survives_a_cp1252_stderr(monkeypatch):
    f = Fake([PENDING, DONE], [[S1, JP, DONE_EV]])
    cli_env(monkeypatch, f)
    out = cp1252(monkeypatch, "stdout")
    cp1252(monkeypatch, "stderr")
    try:
        assert cli.main(ARGS + ["--watch"]) == 0
    finally:
        f.close()
    assert b"verify: valid" in out.getvalue()


def test_the_deadline_bounds_a_stream_that_goes_silent_after_heartbeats():
    # heartbeats for a while, then silence with the connection open
    f = Fake([PENDING], [[S1, "hb1s", "silent"]])
    try:
        t0 = time.monotonic()
        with pytest.raises(DatagoatError) as e:
            ask(f.url, on_progress=lambda ev: None, timeout_s=1.5)
    finally:
        f.close()
    assert e.value.problem.code == "poll_timeout"
    assert time.monotonic() - t0 < 2.3  # not 1.2 s of heartbeats + a fresh 1.5 s read timeout


def test_a_stream_that_sends_headers_then_nothing_falls_back_to_polling_at_once(monkeypatch):
    monkeypatch.setattr(Client, "EVENTS_FIRST_BYTE_S", 0.5)
    f = Fake([PENDING, DONE], [["silent"]])
    got = []
    try:
        t0 = time.monotonic()
        assert ask(f.url, on_progress=got.append) == DONE
    finally:
        f.close()
    assert time.monotonic() - t0 < 3
    assert got == [PENDING]
    assert f.paths() == [("POST", "/v1/ask"), ("GET", "/v1/tasks/tk_w/events"), ("POST", "/v1/poll")]


def test_lone_cr_line_endings_and_a_cr_split_across_reads():
    f = Fake([], [["id: 1\revent: stage\rdata: {\"stage\": \"a\", \"message\": \"m1\"}\r", "\rid: 2\r\n",
                   "event: done\r\ndata: {}\r\n\r\n"]])
    try:
        evs = list(Client(api_key="dgk_test_w", base_url=f.url).events("tk_w"))
    finally:
        f.close()
    assert [(e["event"], e["id"]) for e in evs] == [("stage", 1), ("done", 2)]


def test_an_id_of_unicode_digits_is_kept_as_text():
    f = Fake([], [['id: ²\nevent: done\ndata: {}\n\n']])
    try:
        evs = list(Client(api_key="dgk_test_w", base_url=f.url).events("tk_w"))
    finally:
        f.close()
    assert evs[0]["id"] == "²"
