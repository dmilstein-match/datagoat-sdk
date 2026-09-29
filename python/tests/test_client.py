"""What the client puts on the wire, and how it waits."""
import itertools
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from datagoat import Client, DatagoatError, choice, rank, score, yesno
from datagoat import cli
from datagoat.client import RETRY_SAFE

SEEN = []


def serve(script, status=200):
    it = iter(script)

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            n = int(self.headers.get("content-length", 0))
            SEEN.append((self.path, json.loads(self.rfile.read(n) or b"{}"), self.headers.get("authorization")))
            body, code = next(it), status
            if isinstance(body, tuple):
                body, code = body
            self.send_response(code)
            self.send_header("content-type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(body).encode())

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}"


DONE = {"status": "done", "task_id": "tk_1", "answers": {"churn": {"type": "yesno", "state": "answered", "verdicts": []}}}


def setup_function(_):
    SEEN.clear()


def test_ask_body_is_the_public_contract():
    srv, url = serve([DONE])
    out = Client(api_key="dgk_test_x", base_url=url).ask(
        {"churn": yesno("churned", outcome_is_desirable=False)}, dataset_id="sample:saas_churn",
        entity_column="customer_id", subject_kind="org", cases={"ids": ["cust_0001"]})
    path, body, auth = SEEN[-1]
    assert path == "/v1/ask" and out == DONE and auth == "Bearer dgk_test_x"
    assert body["data"] == {"dataset_id": "sample:saas_churn"}
    assert body["questions"] == {"churn": {"type": "yesno", "outcome_column": "churned", "outcome_is_desirable": False}}
    assert body["idempotency_key"]
    for k in ("band", "time_column", "acknowledge_decision_support"):
        assert k not in body
    srv.shutdown()


def test_builders_send_only_what_is_stated_and_choice_demands_polarity():
    assert yesno("y") == {"type": "yesno", "outcome_column": "y"}
    assert "outcome_is_desirable" not in score("y") and "outcome_is_desirable" not in rank("y")
    assert score("y", levels=("lo", "hi"), cuts=(0.4,))["levels"] == ["lo", "hi"]
    c = choice(option_column="contract", options=["a", "b"], outcome_column="churned", outcome_is_desirable=False)
    assert c["outcome_is_desirable"] is False
    with pytest.raises(TypeError):
        choice(option_column="contract", options=["a", "b"], outcome_column="churned")  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        choice(outcome_is_desirable=True)


def test_a_pending_fit_is_followed_and_bounded():
    pending = {"status": "pending", "task_id": "tk_9", "retry_after_ms": 0}
    srv, url = serve([pending, pending, DONE])
    out = Client(api_key="dgk_test_x", base_url=url).ask({"q": yesno("y")}, rows=[{"a": 1}], entity_column="a",
                                                          subject_kind="org", cases={"ids": ["1"]})
    assert out == DONE
    assert [p for p, _, _ in SEEN] == ["/v1/ask", "/v1/poll", "/v1/poll"]
    assert SEEN[1][1] == {"task_id": "tk_9"}
    srv.shutdown()
    srv, url = serve(itertools.repeat(pending))
    with pytest.raises(DatagoatError) as e:
        Client(api_key="dgk_test_x", base_url=url).ask({"q": yesno("y")}, rows=[{"a": 1}], entity_column="a",
                                                        subject_kind="org", cases={"ids": ["1"]}, timeout_s=0.1)
    assert e.value.problem.code == "poll_timeout" and "do not re-submit" in e.value.problem.remedy
    srv.shutdown()


def test_problems_are_raised_with_code_remedy_and_field():
    prob = {"type": "https://datagoat.io/problems/unknown_case_id", "title": "t", "status": 422, "detail": "not in the record",
            "code": "unknown_case_id", "remedy": "send ids that appear", "field": "cases.ids", "request_id": "r1",
            "engine_request_id": "tk_1"}
    srv, url = serve([(prob, 422)])
    with pytest.raises(DatagoatError) as e:
        Client(api_key="dgk_live_x", base_url=url).ask({"q": yesno("y")}, dataset_id="ds_x", entity_column="a",
                                                        subject_kind="org", cases={"ids": ["nope"]})
    assert (e.value.problem.code, e.value.problem.field, e.value.problem.request_id) == ("unknown_case_id", "cases.ids", "r1")
    assert e.value.problem.engine_request_id == "tk_1"  # the engine's id for the call (E10)
    assert not e.value.retryable
    srv.shutdown()


def test_verify_sends_no_key(monkeypatch):
    srv, url = serve([{"status": "valid"}])
    assert Client(api_key="dgk_live_secret", base_url=url).verify({"verdict_id": "v", "kind": "k"}, {"protected": "p", "signature": "s"}) == "valid"
    assert SEEN[-1][2] is None, "verification is keyless: the key is never sent"
    srv.shutdown()


def test_local_mistakes_fail_before_any_request():
    dg = Client(api_key="dgk_test_x", base_url="http://127.0.0.1:9")
    with pytest.raises(ValueError):
        dg.ask({}, dataset_id="sample:saas_churn", entity_column="a", subject_kind="org")
    with pytest.raises(ValueError):
        dg.ask({"q": yesno("y")}, dataset_id="x", rows=[{"a": 1}], entity_column="a", subject_kind="org")
    with pytest.raises(ValueError):
        dg.ask({"q": {"outcome_column": "y"}}, dataset_id="x", entity_column="a", subject_kind="org")


def test_the_key_comes_from_env_then_the_saved_file(monkeypatch, tmp_path):
    import datagoat.client as c
    monkeypatch.setattr(c, "CREDENTIALS", tmp_path / "credentials")
    monkeypatch.delenv("DATAGOAT_API_KEY", raising=False)
    with pytest.raises(ValueError, match="datagoat signup"):
        Client()
    (tmp_path / "credentials").write_text("dgk_test_saved\n")
    assert Client().test_mode
    monkeypatch.setenv("DATAGOAT_API_KEY", "dgk_live_env")
    assert not Client().test_mode


def test_cli_sample_asks_and_verifies(monkeypatch, capsys):
    calls = []

    class Fake:
        def ask(self, questions, **kw):
            calls.append((questions, kw))
            return {"status": "done", "answers": {"churn": {"state": "answered", "verdicts": [{"verdict": {}, "signature": {}}]}}}

        def verify_all(self, out):
            return True

    monkeypatch.setattr(cli, "Client", lambda *a, **k: Fake())
    assert cli.main(["sample"]) == 0
    (q, kw), = calls
    assert kw["dataset_id"] == "sample:saas_churn" and q["churn"]["outcome_is_desirable"] is False
    assert "verify: valid" in capsys.readouterr().out


def test_shapes_are_sent_as_stated_and_delete_dataset_calls_its_route():
    from datagoat import events, panel, series, signals, traces
    SEEN.clear()
    srv, base = serve([DONE, {"dataset_id": "ds_x", "deleted": True}])
    dg = Client("dgk_live_x", base_url=base)
    dg.ask({"quiet": yesno("lapsed_90d", outcome_is_desirable=False)}, dataset_id="sample:customer_events",
           entity_column="customer_id", subject_kind="org", time_column="date", cases={"ids": ["cust_0001"]},
           shape=events(label={"lapsed": True}, event_column="event", horizon_days=90))
    body = SEEN[0][1]
    assert body["shape"] == {"kind": "events", "label": {"lapsed": True}, "event_column": "event", "horizon_days": 90}
    assert body["time_column"] == "date"
    assert dg.delete_dataset("ds_x") == {"dataset_id": "ds_x", "deleted": True}
    assert SEEN[1][0] == "/v1/delete-dataset"
    assert series(value_columns=["sales"], windows=[4, 12]) == {"kind": "series", "value_columns": ["sales"], "windows": [4, 12]}
    assert panel(trend_of="usage", direction="down") == {"kind": "panel", "label": {"trend_of": "usage", "direction": "down"}}
    assert signals(signal_columns=["v"], windows=[1, 3, 7], snapshot_every="1d", horizon=3)["kind"] == "signals"
    assert traces(tool_column="tool") == {"kind": "traces", "tool_column": "tool"}
    with pytest.raises(ValueError):
        panel()
    srv.shutdown()


def test_attest_and_evidence_call_their_routes():
    SEEN.clear()
    srv, base = serve([{"compliant": True, "dose_fraction": 1.0, "evaluated_feature": "contract", "evaluated_direction": "change", "event_id": "e", "written": True},
                       {"model_ref": "mr1_x", "live": None},
                       {"model_ref": "mr1_x", "overall": {"calls": 0}},
                       {"namespace": "acme", "profile": None}])
    dg = Client("dgk_live_x", base_url=base)
    assert dg.attest("mr1_x", "acct_0020", "hsct1.t", "two_year", "2026-10-01", event_id="a1")["compliant"] is True
    assert SEEN[0][0] == "/v1/attest" and SEEN[0][1]["event_id"] == "a1"
    assert dg.evidence("mr1_x")["live"] is None
    assert SEEN[1] [0] == "/v1/evidence"
    assert dg.track_record("mr1_x")["overall"]["calls"] == 0
    assert SEEN[2][0] == "/v1/track-record"
    assert dg.profile("acme", display="bands")["namespace"] == "acme"
    assert SEEN[3][0] == "/v1/profile" and SEEN[3][1] == {"namespace": "acme", "display": "bands"}
    srv.shutdown()


def test_events_takes_a_horizon_in_any_unit():
    from datagoat import questions as q
    s = q.events(label={"lapsed": True}, horizon=24, unit="hours", lookback=[6, 24])
    assert s == {"kind": "events", "label": {"lapsed": True}, "horizon": {"value": 24, "unit": "hours"}, "lookback": [6, 24]}
    assert q.events(label={"lapsed": True}, horizon_days=90)["horizon_days"] == 90
    import pytest
    with pytest.raises(ValueError, match="months vary in length"):
        q.events(label={"lapsed": True}, horizon=1, unit="months")
    with pytest.raises(ValueError, match="go together"):
        q.events(label={"lapsed": True}, horizon=24)
    assert q.series(value_columns=["x"], windows=[3, 6], time_unit="steps")["time_unit"] == "steps"


# -- real-world volume: retries, pages, exports, many cases ----------------------------------- #

def serve_h(script):
    """Like serve, but each step may set headers: (body, status, headers)."""
    it = iter(script)

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _reply(self):
            step = next(it)
            body, code, headers = (step + (200, {})[len(step) - 1:]) if isinstance(step, tuple) else (step, 200, {})
            if code == "drop":
                self.connection.close()
                return
            self.send_response(code)
            self.send_header("content-type", "text/csv" if isinstance(body, str) else "application/json")
            for k, v in headers.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body.encode() if isinstance(body, str) else json.dumps(body).encode())

        def do_POST(self):
            n = int(self.headers.get("content-length", 0))
            SEEN.append((self.path, json.loads(self.rfile.read(n) or b"{}"), self.headers.get("authorization")))
            self._reply()

        def do_GET(self):
            SEEN.append((self.path, None, self.headers.get("authorization")))
            self._reply()

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}"


def quick(url, **kw):
    dg = Client("dgk_live_x", base_url=url, **kw)
    dg.waits = []
    dg._sleep = dg.waits.append
    return dg


LIMITED = ({"code": "rate_limited", "detail": "too many", "remedy": "wait", "retry_after_ms": 1500}, 429, {"retry-after": "2"})
BUSY = ({"code": "engine_error", "detail": "busy", "remedy": "retry"}, 503)


def test_a_429_is_retried_after_the_wait_the_api_names_for_any_operation():
    srv, url = serve_h([LIMITED, {"dataset_id": "ds_1"}])
    dg = quick(url)
    assert dg.add_dataset(rows=[{"a": 1}]) == {"dataset_id": "ds_1"}
    assert dg.waits == [1.5], "retry_after_ms in the body is exact; it wins over the rounded header"
    srv.shutdown()


def test_a_503_is_retried_only_where_sending_twice_is_safe():
    srv, url = serve_h([BUSY, DONE])
    dg = quick(url)
    out = dg.ask({"q": yesno("y")}, dataset_id="ds_x", entity_column="a", subject_kind="org", cases={"ids": ["1"]}, idempotency_key="k1")
    assert out == DONE
    assert SEEN[0][1]["idempotency_key"] == SEEN[1][1]["idempotency_key"] == "k1", "the retry is the same request"
    srv.shutdown()
    SEEN.clear()
    srv, url = serve_h([BUSY])
    with pytest.raises(DatagoatError) as e:
        quick(url).add_dataset(dataset_id="ds_1", rows=[{"a": 1}])
    assert e.value.problem.status == 503 and len(SEEN) == 1, "an append is never re-sent: it could land twice"
    srv.shutdown()
    SEEN.clear()
    srv, url = serve_h([BUSY])
    with pytest.raises(DatagoatError):
        quick(url).attest("mr1_x", "a", "hsct1.t", 1, "2026-10-01")
    assert len(SEEN) == 1, "an attest without event_id is not retried"
    srv.shutdown()
    SEEN.clear()
    srv, url = serve_h([BUSY, {"written": True}])
    assert quick(url).attest("mr1_x", "a", "hsct1.t", 1, "2026-10-01", event_id="e1")["written"] is True
    srv.shutdown()


def test_retries_are_bounded_and_problems_are_never_retried():
    srv, url = serve_h([BUSY, BUSY, BUSY, BUSY])
    dg = quick(url, max_retries=2)
    with pytest.raises(DatagoatError):
        dg.poll("tk_1")
    assert len(SEEN) == 3 and len(dg.waits) == 2
    assert all(0 < w <= 30 for w in dg.waits)
    srv.shutdown()
    SEEN.clear()
    srv, url = serve_h([({"code": "unknown_case_id", "detail": "d", "remedy": "r"}, 422)])
    with pytest.raises(DatagoatError):
        quick(url).poll("tk_1")
    assert len(SEEN) == 1
    srv.shutdown()


def test_a_dropped_connection_is_retried_for_safe_operations():
    srv, url = serve_h([({}, "drop"), {"status": "valid"}])
    assert quick(url).verify({"verdict_id": "v"}, {"signature": "s"}) == "valid"
    srv.shutdown()


def test_a_paged_answer_is_joined_whole_in_order():
    first = {"status": "done", "task_id": "tk_1", "answers": {
        "churn": {"type": "yesno", "state": "answered", "model_ref": "mr1_a", "cases": [{"entity_id": "a"}, {"entity_id": "b"}],
                  "page": {"offset": 0, "returned": 2, "total": 5}, "verdicts": [{"verdict": {}}]},
        "risk": {"type": "score", "state": "refused", "reasons": ["no_finding_cleared"]}},
        "page": {"answer_url": "http://x/a", "expires_at": "t", "next_cursor": "c2"}}
    p2 = {"status": "done", "answers": {"churn": {"type": "yesno", "state": "answered", "cases": [{"entity_id": "c"}, {"entity_id": "d"}],
                                                  "page": {"offset": 2, "returned": 2, "total": 5}}, "risk": {"type": "score", "state": "refused"}},
          "page": {"answer_url": "http://x/a", "expires_at": "t", "next_cursor": "c4"}}
    p3 = {"status": "done", "answers": {"churn": {"type": "yesno", "state": "answered", "cases": [{"entity_id": "e"}],
                                                  "page": {"offset": 4, "returned": 1, "total": 5}}}, "page": {"answer_url": "http://x/a", "expires_at": "t"}}
    srv, url = serve_h([first, p2, p3])
    out = quick(url).ask({"churn": yesno("y"), "risk": score("y")}, dataset_id="ds", entity_column="a", subject_kind="org", cases={"ids": ["a"]})
    assert [c["entity_id"] for c in out["answers"]["churn"]["cases"]] == list("abcde")
    assert "page" not in out and "page" not in out["answers"]["churn"]
    assert out["answers"]["churn"]["verdicts"] == [{"verdict": {}}]
    assert out["answers"]["risk"] == first["answers"]["risk"], "a refusal passes through untouched"
    assert [p for p, _, _ in SEEN] == ["/v1/ask", "/v1/page", "/v1/page"]
    assert SEEN[1][1] == {"cursor": "c2"}
    srv.shutdown()


def test_compact_is_asked_polled_and_paged_compact():
    """Spec v3 S10: response_format compact on ask, poll and page; the format is per call."""
    pending = {"status": "pending", "task_id": "tk_9", "retry_after_ms": 0}
    first = {"status": "done", "task_id": "tk_9", "answers": {"churn": {"type": "yesno", "state": "answered",
             "cases": [{"entity_id": "a", "p": 0.4, "p_display": "40%"}], "page": {"offset": 0, "returned": 1, "total": 2},
             "verdicts_withheld": {"count": 1, "reason": "response_format_compact", "in": "page.answer_url"}}},
             "page": {"answer_url": "http://x/a", "expires_at": "t", "next_cursor": "c1"}}
    p2 = {"status": "done", "answers": {"churn": {"type": "yesno", "state": "answered", "cases": [{"entity_id": "b", "p": 0.1, "p_display": "10%"}],
                                                  "page": {"offset": 1, "returned": 1, "total": 2}}}, "page": {"answer_url": "http://x/a", "expires_at": "t"}}
    srv, url = serve_h([pending, first, p2])
    out = quick(url).ask({"churn": yesno("y")}, dataset_id="ds", entity_column="a", subject_kind="org", cases={"ids": ["a", "b"]},
                         response_format="compact")
    assert [c["entity_id"] for c in out["answers"]["churn"]["cases"]] == ["a", "b"]
    assert [p for p, _, _ in SEEN] == ["/v1/ask", "/v1/poll", "/v1/page"]
    assert SEEN[0][1]["response_format"] == "compact"
    assert SEEN[1][1] == {"task_id": "tk_9", "response_format": "compact"}
    assert SEEN[2][1] == {"cursor": "c1", "response_format": "compact"}
    assert out["page"] == {"answer_url": "http://x/a", "expires_at": "t"}, "the link to the whole answer stays"
    srv.shutdown()
    dg = Client(api_key="dgk_live_x", base_url="http://127.0.0.1:9")
    for bad in ("concise", "short"):
        with pytest.raises(ValueError):
            dg.page("c1", response_format=bad)
        with pytest.raises(ValueError):
            dg.poll("tk_9", response_format=bad)


def test_export_is_requested_and_downloaded_without_the_key(tmp_path):
    csv_text = "question_id,type\nchurn,yesno\n"
    srv, url = serve_h([dict(DONE, export={"format": "csv", "results_url": "X", "expires_at": "t"}), (csv_text, 200), (csv_text, 200)])
    dg = quick(url)
    out = dg.ask({"churn": yesno("y")}, dataset_id="ds", entity_column="a", subject_kind="org", cases={"ids": ["1"]}, export="csv")
    assert SEEN[0][1]["export"] == "csv" and out["export"]["results_url"] == "X"
    assert dg.download(f"{url}/v1/exports/tok") == csv_text
    assert SEEN[1][2] is None, "a link is its own credential; the key is not sent"
    path = dg.download(f"{url}/v1/exports/tok", str(tmp_path / "a.csv"))
    assert open(path).read() == csv_text
    with pytest.raises(ValueError):
        dg.ask({"churn": yesno("y")}, dataset_id="ds", entity_column="a", subject_kind="org", cases={"ids": ["1"]}, export="xlsx")
    srv.shutdown()


def chunk_answer(ids, task, state="answered", model="mr1_a", cache="hit"):
    return {"status": "done", "task_id": task, "contract": "ask@1", "fits_run": 1 if cache == "miss" else 0, "billable_decisions": len(ids),
            "answers": {"churn": {"type": "yesno", "state": state, "model_ref": model, "cache": cache,
                                  "cases": [{"entity_id": i, "p": 0.5} for i in ids] if state == "answered" else None,
                                  "verdicts": [{"verdict": {"ids": ids}}]}}}


def test_ask_many_chunks_resumably_and_joins_in_order():
    ids = [f"c{i}" for i in range(7)]
    srv, url = serve_h([chunk_answer(ids[0:3], "t0", cache="miss"), chunk_answer(ids[3:6], "t1"), chunk_answer(ids[6:], "t2")])
    progress = []
    out = quick(url).ask_many({"churn": yesno("y")}, cases={"ids": ids}, chunk_size=3, idempotency_key="job",
                              dataset_id="ds", entity_column="a", subject_kind="org", on_chunk=lambda d, n: progress.append((d, n)))
    assert [b["cases"]["ids"] for _, b, _ in SEEN] == [ids[0:3], ids[3:6], ids[6:]]
    assert [b["idempotency_key"] for _, b, _ in SEEN] == ["job:0", "job:1", "job:2"]
    assert [c["entity_id"] for c in out["answers"]["churn"]["cases"]] == ids
    assert len(out["answers"]["churn"]["verdicts"]) == 3, "each chunk's Verdict is kept whole"
    assert out["task_ids"] == ["t0", "t1", "t2"] and out["fits_run"] == 1 and out["billable_decisions"] == 7
    assert progress == [(1, 3), (2, 3), (3, 3)]
    srv.shutdown()


def test_ask_many_stops_when_the_record_refuses_and_rejects_rank():
    srv, url = serve_h([chunk_answer(["a"], "t0", state="refused")])
    out = quick(url).ask_many({"churn": yesno("y")}, cases={"ids": ["a", "b", "c"]}, chunk_size=1,
                              dataset_id="ds", entity_column="a", subject_kind="org")
    assert len(SEEN) == 1 and out["answers"]["churn"]["state"] == "refused"
    srv.shutdown()
    dg = Client("dgk_live_x", base_url="http://127.0.0.1:9")
    with pytest.raises(ValueError, match="whole record"):
        dg.ask_many({"r": rank("y")}, cases={"ids": ["a"]}, dataset_id="ds", entity_column="a", subject_kind="org")
    with pytest.raises(ValueError):
        dg.ask_many({"q": yesno("y")}, cases={"ids": []}, dataset_id="ds", entity_column="a", subject_kind="org")
    with pytest.raises(ValueError):
        dg.ask_many({"q": yesno("y")}, cases={"ids": ["a"]}, chunk_size=20_000, dataset_id="ds", entity_column="a", subject_kind="org")


def test_ask_many_never_joins_chunks_that_disagree():
    SEEN.clear()
    srv, url = serve_h([chunk_answer(["a"], "t0"), chunk_answer(["b"], "t1", model="mr1_b")])
    out = quick(url).ask_many({"churn": yesno("y")}, cases={"ids": ["a", "b"]}, chunk_size=1,
                              dataset_id="ds", entity_column="a", subject_kind="org")
    assert "churn" not in out["answers"] and [p["model_ref"] for p in out["unjoined"]["churn"]] == ["mr1_a", "mr1_b"]
    srv.shutdown()


def test_a_cut_off_200_is_not_an_answer_and_is_retried_where_safe():
    SEEN.clear()
    srv, url = serve_h([('{"status": "do', 200), DONE])
    out = quick(url).poll("tk_1")
    assert out == DONE and len(SEEN) == 2
    srv.shutdown()
    SEEN.clear()
    srv, url = serve_h([('{"dataset_id": "ds', 200)])
    with pytest.raises(DatagoatError) as e:
        quick(url).add_dataset(dataset_id="ds_1", rows=[{"a": 1}])
    assert e.value.problem.code == "incomplete_response" and len(SEEN) == 1
    srv.shutdown()


def test_a_body_over_the_request_limit_fails_before_sending():
    dg = Client("dgk_live_x", base_url="http://127.0.0.1:9")
    with pytest.raises(DatagoatError) as e:
        dg.ask({"q": yesno("y")}, csv="a,b\n" + "1,2\n" * 1_200_000, entity_column="a", subject_kind="org", cases={"ids": ["1"]})
    assert e.value.problem.code == "request_too_large" and "upload_file" in e.value.problem.remedy


def test_ask_many_refuses_wait_false_and_a_joined_answer_has_no_next_cursor():
    dg = Client("dgk_live_x", base_url="http://127.0.0.1:9")
    with pytest.raises(ValueError, match="every chunk"):
        dg.ask_many({"q": yesno("y")}, cases={"ids": ["a"]}, dataset_id="ds", entity_column="a", subject_kind="org", wait=False)
    first = {"status": "done", "task_id": "t", "page": {"answer_url": "u", "expires_at": "e", "next_cursor": "c1"},
             "answers": {"q": {"type": "yesno", "state": "answered", "cases": [{"entity_id": "a"}], "verdicts_withheld": {"count": 1}}}}
    later = {"status": "done", "page": {"answer_url": "u", "expires_at": "e"}, "answers": {"q": {"type": "yesno", "state": "answered", "cases": [{"entity_id": "b"}]}}}
    SEEN.clear()
    srv, url = serve_h([first, later])
    out = quick(url).poll("t")
    assert [c["entity_id"] for c in out["answers"]["q"]["cases"]] == ["a", "b"]
    assert out["page"] == {"answer_url": "u", "expires_at": "e"}, "the link to the withheld Verdicts stays; there is no next page"
    srv.shutdown()


def test_building_a_product_model_refs_namespaces_and_lifetimes():
    from datagoat import from_model, snapshots
    ref = "mr1_" + "a" * 32
    srv, url = serve([DONE, DONE, {"candidates": [], "considered": 0, "not_checked": []},
                      {"model_ref": ref, "model_expires_at": "2027-01-01T00:00:00Z"}, {"model_ref": ref, "deleted": True}, {"pattern": "no_prior"}])
    dg = Client(api_key="dgk_live_x", base_url=url)
    dg.ask({"churn": from_model(ref, type="score", levels=["low", "high"], cuts=[0.5])}, entity_column="customer_id",
           subject_kind="org", cases={"rows": [{"customer_id": "n1", "tenure": 3}]}, namespace="acme")
    _, body, _ = SEEN[-1]
    assert "data" not in body, "a model_ref ask sends no record"
    assert body["questions"]["churn"] == {"type": "score", "model_ref": ref, "levels": ["low", "high"], "cuts": [0.5]}
    assert body["namespace"] == "acme"
    dg.ask({"won": yesno("won")}, dataset_id="ds_1", entity_column="deal_id", subject_kind="org", time_column="ts",
           shape=snapshots(snapshot_id_column="snap", snapshot_time_column="at", dataset_id="ds_2", lookback_days=[7, 30]),
           cases={"ids": ["d1@demo"]}, model_ttl_days=365)
    _, body, _ = SEEN[-1]
    assert body["shape"] == {"kind": "snapshots", "snapshots": {"dataset_id": "ds_2"}, "snapshot_id_column": "snap",
                             "snapshot_time_column": "at", "lookback_days": [7, 30]}
    assert body["model_ttl_days"] == 365 and "namespace" not in body and "group_column" not in body
    dg.suggest("customer_id", dataset_id="sample:saas_churn", include_categories=True)
    assert SEEN[-1][0] == "/v1/suggest" and SEEN[-1][1] == {"data": {"dataset_id": "sample:saas_churn"}, "entity_column": "customer_id", "include_categories": True}
    assert dg.extend_model(ref, 200)["model_expires_at"] == "2027-01-01T00:00:00Z"
    assert SEEN[-1] [0:2] == ("/v1/extend-model", {"model_ref": ref, "days": 200})
    dg.delete_model(ref, namespace="acme")
    assert SEEN[-1][0:2] == ("/v1/delete-model", {"model_ref": ref, "namespace": "acme"})
    dg.drift(ref)
    assert SEEN[-1][1] == {"model_ref": ref}
    with pytest.raises(ValueError):
        from_model(ref, type="choice")
    with pytest.raises(ValueError):
        dg.ask({"churn": yesno("churned")}, entity_column="customer_id", subject_kind="org")
    srv.shutdown()


def test_problems_are_typed_by_family_and_a_refusal_is_never_one():
    from datagoat import ModelUnavailableError, NotFoundError, PaymentRequiredError, RateLimitError, ValidationError

    def prob(code, status, **kw):
        return ({"type": f"https://datagoat.io/problems/{code}", "title": code, "status": status, "detail": "d",
                 "code": code, "remedy": "r", "doc_url": f"https://datagoat.io/docs/errors#{code}", **kw}, status)

    cases = [
        (prob("invalid_request", 422, field="questions.q.cuts"), ValidationError),
        (prob("model_deleted", 410, field="model_ref"), ModelUnavailableError),
        (prob("model_expired", 410, field="model_ref"), ModelUnavailableError),
        (prob("model_ref_missing", 404, field="model_ref"), ModelUnavailableError),
        (prob("unknown_dataset", 404), NotFoundError),
        (prob("payment_required", 402), PaymentRequiredError),
    ]
    srv, url = serve([c[0] for c in cases])
    dg = Client(api_key="dgk_live_x", base_url=url, max_retries=0)
    for (body, _), cls in cases:
        with pytest.raises(cls) as e:
            dg.drift("mr1_x")
        assert isinstance(e.value, DatagoatError) and e.value.code == body["code"]
        assert e.value.problem.doc_url == body["doc_url"]
    srv.shutdown()
    srv, url = serve([prob("invalid_request", 422, field="questions.q.cuts")])
    with pytest.raises(ValidationError) as e:
        Client(api_key="dgk_live_x", base_url=url).drift("mr1_x")
    assert e.value.field == "questions.q.cuts" and isinstance(e.value, DatagoatError)
    srv.shutdown()
    srv, url = serve([prob("rate_limited", 429, retry_after_ms=0)])
    with pytest.raises(RateLimitError):
        Client(api_key="dgk_live_x", base_url=url, max_retries=0).drift("mr1_x")
    srv.shutdown()
    refused = {"status": "done", "answers": {"q": {"type": "yesno", "state": "refused", "reasons": ["too_few_predictors"],
                                                  "needs": {"columns": 2}, "have": {"columns": 2}}}}
    srv, url = serve([refused])
    out = Client(api_key="dgk_live_x", base_url=url).ask({"q": yesno("y")}, dataset_id="ds_x", entity_column="a",
                                                         subject_kind="org", cases={"ids": ["1"]})
    assert out["answers"]["q"]["state"] == "refused", "a refusal is an answer, not an exception"
    srv.shutdown()


def test_report_outcomes_sends_chunks_and_surfaces_every_bad_row_by_its_index_in_the_whole_list():
    from datagoat import ValidationError
    rows = [{"entity_id": f"c{i}", "outcome": 1, "observed_at": "2026-10-01"} for i in range(5)]
    bad = ({"code": "invalid_outcomes", "status": 422, "detail": "1 invalid", "remedy": "fix", "field": "outcomes",
            "errors": [{"index": 1, "field": "outcome", "detail": "not yes/no"}]}, 422)
    srv, url = serve([{"written": 2, "duplicates": 0}, bad])
    with pytest.raises(ValidationError) as e:
        Client(api_key="dgk_live_x", base_url=url).report_outcomes("mr1_x", rows, chunk_size=2)
    assert [len(b["outcomes"]) for p, b, _ in SEEN] == [2, 2]
    assert e.value.errors == [{"index": 3, "field": "outcome", "detail": "not yes/no"}], "index in the whole list"
    assert "rows 0 to 1 were already sent and written: 2 written" in e.value.problem.detail
    assert e.value.written_before == 2
    assert all("partial" not in b for _, b, _ in SEEN), "partial is sent only when stated"
    srv.shutdown()
    SEEN.clear()
    part = lambda n: {"written": n, "duplicates": 0, "results": [{"index": i, "status": "written"} for i in range(n)]}
    srv, url = serve([part(2), part(2), part(1)])
    out = Client(api_key="dgk_live_x", base_url=url).report_outcomes("mr1_x", rows, chunk_size=2, partial=True)
    assert out["written"] == 5 and [r["index"] for r in out["results"]] == [0, 1, 2, 3, 4]
    assert all(b["partial"] is True for _, b, _ in SEEN)
    srv.shutdown()


def test_preflight_is_deprecated_for_map_and_still_served():
    """dg_preflight is deprecated (spec v3 S2): replaced by dg_map, removed at contract 2.0.0, served until then."""
    srv, url = serve([{"dataset_id": "ds_x", "grain": {}, "resolutions": {}, "blocking": [], "advisory": []}])
    dg = Client(api_key="dgk_live_x", base_url=url)
    with pytest.warns(DeprecationWarning, match="deprecated") as w:
        out = dg.preflight("ds_x", outcome_column="churned")
    assert "dg_map" in str(w[0].message) and "2.0.0" in str(w[0].message)
    assert SEEN[-1][0] == "/v1/preflight" and out["dataset_id"] == "ds_x"
    assert w[0].filename.endswith("test_client.py"), "the warning points at the caller's line"
    assert "deprecated" in (Client.preflight.__doc__ or "").lower() and "map" in Client.preflight.__doc__
    srv.shutdown()


def test_the_new_fields_are_sent_only_when_stated():
    srv, url = serve([DONE, {"ok": True}, {"ok": True}, {"ok": True}])
    dg = Client(api_key="dgk_live_x", base_url=url)
    dg.ask({"churn": yesno("churned")}, dataset_id="ds_x", entity_column="customer_id", subject_kind="org",
           cases={"ids": ["c1"]}, response_format="concise")
    assert SEEN[-1][1]["response_format"] == "concise"
    with pytest.warns(DeprecationWarning):
        dg.preflight("ds_x", entity_column="customer_id")
    assert SEEN[-1][1] == {"dataset_id": "ds_x", "entity_column": "customer_id"}
    dg.profile("acme", fixed=["tenure_months"])
    assert SEEN[-1][1] == {"namespace": "acme", "fixed": ["tenure_months"]}
    dg.profile("acme")
    assert SEEN[-1][1] == {"namespace": "acme"}
    with pytest.raises(ValueError):
        dg.ask({"churn": yesno("churned")}, dataset_id="ds_x", entity_column="c", subject_kind="org",
               cases={"ids": ["c1"]}, response_format="short")
    srv.shutdown()


def test_report_outcomes_defaults_to_one_atomic_call_and_any_later_chunk_failure_names_what_was_written():
    from datagoat import DatagoatError, RateLimitError
    rows = [{"entity_id": f"c{i}", "outcome": 1, "observed_at": "2026-10-01"} for i in range(5)]
    srv, url = serve([{"written": 5, "duplicates": 0}])
    Client(api_key="dgk_live_x", base_url=url).report_outcomes("mr1_x", rows)
    assert [len(b["outcomes"]) for _, b, _ in SEEN] == [5], "up to 10,000 rows go in one atomic call"
    srv.shutdown()
    SEEN.clear()
    for failure, cls in (((({"code": "engine_unavailable", "status": 503, "detail": "down", "remedy": "retry"}), 503), DatagoatError),
                         (({"code": "rate_limited", "status": 429, "detail": "busy", "remedy": "wait", "retry_after_ms": 0}, 429), RateLimitError)):
        srv, url = serve([{"written": 3, "duplicates": 1}, failure])
        with pytest.raises(cls) as e:
            Client(api_key="dgk_live_x", base_url=url, max_retries=0).report_outcomes("mr1_x", rows, chunk_size=4)
        assert e.value.written_before == 4
        assert "rows 0 to 3 were already sent and written: 3 written, 1 duplicate; rows 4 onward were not" in e.value.problem.detail
        srv.shutdown()


def test_map_sends_the_public_contract_and_is_never_retried_on_a_5xx():
    SEEN.clear()
    proposed = {"status": "proposed", "sources": [], "questions": [{"slot": "outcome", "options": ["lapsed", "event_type:e:event=cancelled"], "why": "two"}]}
    srv, url = serve_h([proposed, BUSY])
    dg = quick(url)
    out = dg.map(["sample:parley_events", {"fetch_url": "https://x.example/billing.csv", "label": "billing"}],
                 outcome_words="cancel", horizon=90, snapshot_every="4w")
    assert out["questions"][0]["slot"] == "outcome"
    path, body, _ = SEEN[0]
    assert path == "/v1/map"
    assert body == {"sources": [{"dataset_id": "sample:parley_events"}, {"fetch_url": "https://x.example/billing.csv", "label": "billing"}],
                    "outcome_words": ["cancel"], "horizon": {"value": 90, "unit": "days"}, "snapshot_every": "4w"}
    with pytest.raises(DatagoatError) as e:
        dg.map(mapping_id="mp_" + "a" * 25, closed_since="2025-10-01")
    assert e.value.problem.status == 503 and len(SEEN) == 2, "a confirm stores a mapping: never re-sent"
    assert SEEN[1][1] == {"mapping_id": "mp_" + "a" * 25, "closed_since": "2025-10-01"}
    with pytest.raises(ValueError):
        dg.map()
    srv.shutdown()


def test_map_problems_carry_their_members():
    SEEN.clear()
    expired = {"status": 410, "code": "mapping_expired", "detail": "gone", "remedy": "map again", "field": "mapping_id", "manifest": {"entity": "account_id"}}
    incomplete = {"status": 422, "code": "mapping_answers_incomplete", "detail": "Some questions have no answer (outcome).", "remedy": "answer", "field": "answers.outcome"}
    srv, url = serve([(expired, 410), (incomplete, 422)])
    dg = Client("dgk_live_x", base_url=url)
    with pytest.raises(DatagoatError) as e:
        dg.map(mapping_id="mp_" + "b" * 25)
    assert e.value.problem.manifest == {"entity": "account_id"}
    with pytest.raises(DatagoatError) as e2:
        dg.map(["ds_" + "c" * 25], answers={})
    assert e2.value.problem.field == "answers.outcome" and "(outcome)" in e2.value.problem.detail
    assert not hasattr(e2.value.problem, "slots"), "the slots are named in detail; no extension member (A2, A11)"
    srv.shutdown()


def test_cli_map_proposes_and_lists_the_questions_for_the_user(monkeypatch, capsys):
    calls = []

    class Fake:
        def map(self, sources, **kw):
            calls.append((sources, kw))
            return {"status": "proposed", "questions": [{"slot": "entity", "source": "parley_tickets", "options": ["account_id", "user_id"], "why": "two"}]}

    monkeypatch.setattr(cli, "Client", lambda *a, **k: Fake())
    assert cli.main(["map", "sample:parley_events", "https://x.example/t.csv", "--outcome", "cancel", "--horizon", "90", "--every", "4w"]) == 0
    (sources, kw), = calls
    assert sources == [{"dataset_id": "sample:parley_events"}, {"fetch_url": "https://x.example/t.csv"}]
    assert kw["outcome_words"] == ["cancel"] and kw["horizon"] == 90 and kw["snapshot_every"] == "4w" and kw["answers"] is None
    err = capsys.readouterr().err
    assert "question entity (parley_tickets): account_id | user_id" in err
    assert cli.main(["map"]) == 2


def test_snapshots_v2_form_builds_the_grid_shape_and_asks_open_cases():
    from datagoat import snapshots
    s = snapshots(snapshot_every="4w", horizon=90, label={"lapsed": True}, event_column="event", lookbacks_days=[7, 30])
    assert s == {"kind": "snapshots", "snapshot_every": "4w", "horizon": {"value": 90, "unit": "days"},
                 "label": {"lapsed": True}, "event_column": "event", "lookbacks_days": [7, 30]}
    by_time = snapshots(snapshot_every="1w", horizon=30, outcome_time_column="cancelled_at", terminal=True, as_of="2025-12-29")
    assert by_time == {"kind": "snapshots", "snapshot_every": "1w", "horizon": {"value": 30, "unit": "days"},
                       "outcome_time_column": "cancelled_at", "terminal": True, "as_of": "2025-12-29"}
    for bad in [dict(snapshot_every="1mo", horizon=90, label={"lapsed": True}),
                dict(snapshot_every="4w", horizon=45, label={"lapsed": True}),
                dict(snapshot_every="4w", horizon=90),
                dict(snapshot_every="4w", horizon=90, label={"lapsed": True}, outcome_time_column="x"),
                dict(snapshot_every="4w", horizon=90, label={"lapsed": True}, snapshot_id_column="snap"),
                dict(snapshot_every="4w", horizon=90, label={"lapsed": True}, dataset_id="ds_2", snapshot_id_column="s", snapshot_time_column="t")]:
        with pytest.raises(ValueError):
            snapshots(**bad)
    srv, url = serve([DONE])
    dg = Client(api_key="dgk_live_x", base_url=url)
    dg.ask({"quiet": yesno("lapsed_90d")}, dataset_id="ds_1", entity_column="account_id", subject_kind="org", time_column="ts",
           shape=s, cases={"open": True})
    _, body, _ = SEEN[-1]
    assert body["cases"] == {"open": True} and body["shape"]["snapshot_every"] == "4w"
    srv.shutdown()


def test_cli_ask_open_cases_of_a_mapped_record(monkeypatch, capsys):
    calls = []

    class Fake:
        def ask(self, questions, **kw):
            calls.append((questions, kw))
            return {"status": "done", "answers": {}}

        def verify_all(self, out):
            return True

    monkeypatch.setattr(cli, "Client", lambda *a, **k: Fake())
    q = '{"q":{"type":"yesno","outcome_column":"lapsed_90d","positive_values":["1"]}}'
    assert cli.main(["ask", q, "--data", "ds_1", "--entity", "account_id", "--open", "--time", "snapshot_at", "--group", "account_id"]) == 0
    (_, kw), = calls
    assert kw["cases"] == {"open": True} and kw["time_column"] == "snapshot_at" and kw["group_column"] == "account_id"
    with pytest.raises(SystemExit):
        cli.main(["ask", q, "--data", "ds_1", "--entity", "account_id", "--open", "--cases", "a1"])


BT_DONE = {"status": "done", "task_id": "tk_" + "a" * 24, "backtest_id": "bt1_x", "decision": "supported", "cutoffs": [],
           "summary": {}, "verdict": {"verdict_id": "v", "kind": "backtest"}, "signature": {"kid": "k"}, "fits_run": 6}


def test_backtest_sends_the_public_contract_follows_a_pending_walk_and_is_retried_with_its_key():
    SEEN.clear()
    pending = ({"status": "pending", "task_id": "tk_" + "a" * 24, "retry_after_ms": 500}, 202)
    srv, url = serve_h([BUSY, pending, BT_DONE])
    dg = quick(url)
    out = dg.backtest("sample:parley_record", subject_kind="org", every="4w", last=6, baseline="none", idempotency_key="bt-1")
    assert out["decision"] == "supported"
    assert [s[0] for s in SEEN] == ["/v1/backtest", "/v1/backtest", "/v1/poll"]
    assert SEEN[0][1] == {"data": {"dataset_id": "sample:parley_record"}, "cutoffs": {"every": "4w", "last": 6},
                          "baseline": "none", "subject_kind": "org", "idempotency_key": "bt-1"}
    assert SEEN[1][1] == SEEN[0][1], "retried with the same idempotency key: the walk is not started twice"
    srv.shutdown()
    SEEN.clear()
    srv, url = serve_h([BT_DONE])
    quick(url).backtest("ds_" + "b" * 25, subject_kind="person", acknowledge_decision_support=True)
    body = SEEN[0][1]
    assert isinstance(body["idempotency_key"], str) and "cutoffs" not in body and body["acknowledge_decision_support"] is True
    srv.shutdown()
    with pytest.raises(ValueError):
        Client("dgk_live_x", base_url=url).backtest("ds_x", subject_kind="org", baseline="best")


def test_cli_backtest_prints_the_result_and_checks_its_verdict(monkeypatch, capsys):
    calls = []

    class Fake:
        def backtest(self, dataset_id, **kw):
            calls.append((dataset_id, kw))
            return dict(BT_DONE)

        def verify(self, verdict, signature):
            calls.append(("verify", verdict["kind"]))
            return "valid"

    monkeypatch.setattr(cli, "Client", lambda *a, **k: Fake())
    assert cli.main(["backtest", "sample:parley_record", "--every", "4w", "--last", "6", "--baseline", "none"]) == 0
    (ds, kw), verified = calls
    assert ds == "sample:parley_record" and kw["every"] == "4w" and kw["last"] == 6 and kw["baseline"] == "none"
    assert kw["subject_kind"] == "org" and verified == ("verify", "backtest")
    out = capsys.readouterr().out
    assert '"decision": "supported"' in out and "verify: valid" in out


SC_OUT = {"schedule": {"schedule_id": "sc_" + "a" * 25, "mapping_id": "mp_" + "a" * 25, "cadence": "monthly", "state": "active",
                       "next_run_at": "2026-10-01T00:00:00Z", "model_ref": None, "last_run": None},
          "watch_url": "https://datagoat.io/watch/x"}


def test_schedule_sends_the_public_contract_and_is_not_retried_on_a_5xx():
    SEEN.clear()
    srv, url = serve_h([SC_OUT])
    out = quick(url).schedule("mp_" + "a" * 25, cadence="monthly", subject_kind="org", namespace="acme")
    assert out["schedule"]["state"] == "active"
    assert SEEN[0][0] == "/v1/schedule"
    assert SEEN[0][1] == {"mapping_id": "mp_" + "a" * 25, "cadence": "monthly", "subject_kind": "org", "namespace": "acme"}
    srv.shutdown()
    SEEN.clear()
    srv, url = serve_h([BUSY, SC_OUT])
    with pytest.raises(DatagoatError):
        quick(url).schedule("mp_" + "a" * 25, cadence="daily", subject_kind="org")
    assert len(SEEN) == 1, "a retry could create a second schedule"
    srv.shutdown()
    SEEN.clear()
    srv, url = serve_h([BUSY, {"schedule_id": "sc_x", "deleted": True}])
    with pytest.raises(DatagoatError):
        quick(url).delete_schedule("sc_x")
    assert [s[0] for s in SEEN] == ["/v1/delete-schedule"], "dg_delete_schedule is not in RETRY_SAFE either"
    srv.shutdown()
    assert "schedule" not in RETRY_SAFE and "delete-schedule" not in RETRY_SAFE
    with pytest.raises(ValueError):
        Client("dgk_live_x", base_url=url).schedule("mp_x", cadence="hourly", subject_kind="org")


def test_cli_schedule_creates_and_deletes(monkeypatch, capsys):
    calls = []

    class Fake:
        def schedule(self, mapping_id, **kw):
            calls.append(("schedule", mapping_id, kw))
            return dict(SC_OUT)

        def delete_schedule(self, schedule_id):
            calls.append(("delete", schedule_id))
            return {"schedule_id": schedule_id, "deleted": True}

    monkeypatch.setattr(cli, "Client", lambda *a, **k: Fake())
    assert cli.main(["schedule", "mp_" + "a" * 25, "--cadence", "weekly"]) == 0
    assert calls[0] == ("schedule", "mp_" + "a" * 25, {"cadence": "weekly", "subject_kind": "org"})
    assert '"state": "active"' in capsys.readouterr().out
    assert cli.main(["schedule", "--delete", "sc_x"]) == 0
    assert calls[1] == ("delete", "sc_x")
    assert cli.main(["schedule", "mp_x"]) == 2
