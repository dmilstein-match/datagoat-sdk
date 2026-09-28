"""The Datagoat client. Standard library only.

    from datagoat import Client, yesno
    dg = Client()                     # DATAGOAT_API_KEY, else the key `datagoat signup` saved
    dg.ask({"churn": yesno("churned", outcome_is_desirable=False)},
           dataset_id="sample:saas_churn", entity_column="customer_id",
           subject_kind="org", cases={"ids": ["cust_0001"]})
"""
from __future__ import annotations

import json
import os
import random
import shutil
import socket
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from ._version import __version__

DEFAULT_BASE = "https://api.datagoat.io"
#: The API's request limit; a bigger body would be refused before reaching Datagoat.
MAX_REQUEST_BYTES = 4_500_000
CREDENTIALS = Path(os.environ.get("DATAGOAT_CONFIG_DIR", Path.home() / ".config" / "datagoat")) / "credentials"


@dataclass(frozen=True)
class Problem:
    status: int
    code: str
    detail: str
    remedy: str
    field: Optional[str] = None
    request_id: Optional[str] = None
    #: The code's entry on https://datagoat.io/docs/errors.
    doc_url: Optional[str] = None
    #: invalid_outcomes: every bad row, as {index, field, detail}.
    errors: Optional[List[Dict[str, Any]]] = None


class DatagoatError(Exception):
    """A problem the API answered with. Read `problem.code` and `problem.remedy`.

    Each family has its own subclass, so `except` can tell them apart: ValidationError (the
    request is wrong: `field` names what to fix), NotFoundError (and ModelUnavailableError for a
    model_ref that no longer answers), RateLimitError, PaymentRequiredError. A refusal is never an
    exception: it is an answer with state "refused"."""

    def __init__(self, p: Problem) -> None:
        super().__init__(f"{p.code}: {p.detail} ({p.remedy})")
        self.problem = p
        #: Seconds the API asked to wait before retrying (a 429 names it), else None.
        self.retry_after_s: Optional[float] = None
        #: report_outcomes over several chunks: rows of the list already sent and written before
        #: the chunk that failed (0 when the first chunk failed).
        self.written_before: int = 0

    @property
    def retryable(self) -> bool:
        return self.problem.status in (429, 502, 503, 504)

    @property
    def code(self) -> str:
        return self.problem.code


class ValidationError(DatagoatError):
    """400/422: the request is wrong. `field` names the argument; for invalid_outcomes, `errors`
    lists every bad row as {index, field, detail}."""

    @property
    def field(self) -> Optional[str]:
        return self.problem.field

    @property
    def errors(self) -> List[Dict[str, Any]]:
        return list(self.problem.errors or [])


class NotFoundError(DatagoatError):
    """404/410: what the call names does not exist here, or no longer does (`gone` for 410)."""

    @property
    def gone(self) -> bool:
        return self.problem.status == 410


class ModelUnavailableError(NotFoundError):
    """A model_ref that does not answer: model_deleted or model_expired (410), model_ref_missing
    (404). Asking again with the record fits a new model."""


class RateLimitError(DatagoatError):
    """429: the workspace's requests for this minute are spent; `retry_after_s` says how long."""


class PaymentRequiredError(DatagoatError):
    """402: asking about your own data needs a card on file (payment_required, payment_past_due,
    subscription_canceled). The samples stay free."""


MODEL_CODES = frozenset({"model_deleted", "model_expired", "model_ref_missing"})


def error_for(p: Problem) -> DatagoatError:
    """The typed error for a problem, by its code family."""
    if p.code in MODEL_CODES:
        return ModelUnavailableError(p)
    if p.status in (404, 410):
        return NotFoundError(p)
    if p.status in (400, 422):
        return ValidationError(p)
    if p.status == 429:
        return RateLimitError(p)
    if p.status == 402:
        return PaymentRequiredError(p)
    return DatagoatError(p)


def _json_default(o: Any) -> Any:
    import datetime as _dt
    if isinstance(o, _dt.date):
        return o.isoformat()
    raise TypeError(f"Object of type {type(o).__name__} is not JSON serializable")


def saved_key() -> Optional[str]:
    try:
        return CREDENTIALS.read_text().strip() or None
    except OSError:
        return None


#: Operations where sending the same request twice cannot do the work twice: reads, an ask (its
#: idempotency_key returns the first call's task), deletes, and outcome reports (duplicates are
#: recognised). An attest is safe only with an event_id; add-dataset (which may append) never is,
#: except after a 429, which means the request was turned away before any work.
RETRY_SAFE = frozenset({"ask", "poll", "page", "preflight", "verify", "describe", "drift", "evidence",
                        "report-outcomes", "delete-dataset", "suggest", "extend-model", "delete-model"})


def _may_retry(op: str, body: Optional[Mapping[str, Any]], status: Optional[int]) -> bool:
    if status == 429:
        return True
    if status is not None and status not in (502, 503, 504):
        return False
    if op == "attest":
        return bool(body and body.get("event_id"))
    return op in RETRY_SAFE


def _retry_after(header: Optional[str], body_ms: Any) -> Optional[float]:
    if isinstance(body_ms, (int, float)) and body_ms >= 0:
        return body_ms / 1000
    try:
        return max(0.0, float(header)) if header is not None else None
    except ValueError:
        return None


def _backoff(attempt: int) -> float:
    """1s, 2s, 4s ... with jitter, so many clients retrying at once spread out."""
    return min(30.0, 2 ** (attempt - 1)) * (0.5 + random.random() / 2)


def _merge_pages(first: Dict[str, Any], pages: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """A paged answer made whole: each answer's list is the concatenation of its slices, in order.
    Paging only ever slices, so nothing else changes. Verdicts withheld from the first page are not
    on later pages either; `download(out["page"]["answer_url"])` returns them."""
    out = dict(first)
    answers = {q: dict(a) for q, a in (first.get("answers") or {}).items()}
    for p in pages:
        for q, a in (p.get("answers") or {}).items():
            for key in ("cases", "ranked"):
                if key in a and q in answers:
                    answers[q][key] = list(answers[q].get(key) or []) + list(a[key])
    for a in answers.values():
        a.pop("page", None)
    out["answers"] = answers
    return out


def _source(dataset_id, rows, csv, fetch_url, fetch_headers, *, optional: bool = False) -> Optional[Dict[str, Any]]:
    data = {k: v for k, v in {"dataset_id": dataset_id, "rows": rows, "csv": csv, "fetch_url": fetch_url}.items() if v is not None}
    if optional and not data and not fetch_headers:
        return None
    if len(data) != 1:
        raise ValueError("pass exactly one of dataset_id, rows, csv, fetch_url")
    if fetch_headers:
        if "fetch_url" not in data:
            raise ValueError("fetch_headers needs fetch_url")
        data["fetch_headers"] = dict(fetch_headers)
    return data


def _ns(namespace: Optional[str]) -> Dict[str, Any]:
    return {"namespace": namespace} if namespace is not None else {}


class Client:
    #: The longest Datagoat keeps a first fit running. `ask` never waits longer than this.
    RUN_TIMEOUT_S = 900.0

    def __init__(self, api_key: Optional[str] = None, *, base_url: Optional[str] = None, timeout: float = 320.0,
                 max_retries: int = 2) -> None:
        key = api_key or os.environ.get("DATAGOAT_API_KEY") or saved_key()
        if not key:
            raise ValueError("no API key: pass api_key, set DATAGOAT_API_KEY, or run `datagoat signup`")
        self._auth = f"Bearer {key}"
        self.base = (base_url or os.environ.get("DATAGOAT_BASE_URL") or DEFAULT_BASE).rstrip("/")
        self.timeout = timeout
        self.test_mode = key.startswith("dgk_test_")
        self.max_retries = max(0, int(max_retries))
        self._sleep = time.sleep

    # -- transport ------------------------------------------------------------ #
    @staticmethod
    def _request(url: str, body: Optional[Mapping[str, Any]], headers: Mapping[str, str], timeout: float) -> Dict[str, Any]:
        data = json.dumps(body or {}, default=_json_default, allow_nan=False).encode()
        if len(data) > MAX_REQUEST_BYTES:
            raise DatagoatError(Problem(413, "request_too_large",
                                        f"this request is {len(data):,} bytes; a request is at most {MAX_REQUEST_BYTES:,}",
                                        "store the table first: upload_file(path) or upload_rows(rows), then ask by dataset_id"))
        req = urllib.request.Request(url, data=data, method="POST", headers={
            "content-type": "application/json", "user-agent": f"datagoat-python/{__version__}", **headers})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - fixed https base
                raw = r.read()
            try:
                return json.loads(raw)
            except ValueError:
                # A 200 whose body was cut off (a dropped stream) is not an answer.
                raise DatagoatError(Problem(502, "incomplete_response", "the response ended before the answer was complete",
                                            "retry; an ask with the same idempotency_key returns the same answer")) from None
        except urllib.error.HTTPError as e:
            try:
                p = json.load(e)
            except Exception:  # noqa: BLE001
                p = {}
            err = error_for(Problem(e.code, p.get("code", "http_error"), p.get("detail", str(e.reason)),
                                    p.get("remedy", "see https://datagoat.io/docs"), p.get("field"),
                                    p.get("request_id") or e.headers.get("x-request-id"),
                                    p.get("doc_url"), p.get("errors") if isinstance(p.get("errors"), list) else None))
            err.retry_after_s = _retry_after(e.headers.get("retry-after"), p.get("retry_after_ms"))
            raise err from None

    def _call(self, op: str, body: Optional[Mapping[str, Any]] = None, *, keyless: bool = False) -> Dict[str, Any]:
        """One operation, retried when that is safe (see RETRY_SAFE): a 429 is always retried after the
        wait the API names; a 502/503/504 or a dropped connection only for operations where sending the
        same request twice cannot do the work twice. A problem the API answered with (a 4xx) and a
        refusal (which is an answer, not an error) are never retried."""
        headers = {} if keyless else {"authorization": self._auth}
        attempt = 0
        while True:
            try:
                return self._request(f"{self.base}/v1/{op}", body, headers, self.timeout)
            except DatagoatError as e:
                if attempt >= self.max_retries or not _may_retry(op, body, e.problem.status):
                    raise
                wait = e.retry_after_s
            except (urllib.error.URLError, ConnectionError, TimeoutError, socket.timeout) as e:
                if attempt >= self.max_retries or not _may_retry(op, body, None):
                    raise DatagoatError(Problem(503, "network_error", str(getattr(e, "reason", e)),
                                                "check the connection and retry")) from None
                wait = None
            attempt += 1
            self._sleep(_backoff(attempt) if wait is None else min(wait, 60.0))

    def _follow(self, out: Dict[str, Any], *, wait: bool, timeout_s: Optional[float],
                on_progress: Optional[Callable[[Dict[str, Any]], None]]) -> Dict[str, Any]:
        """Follow a pending task to its answer, bounded. Never re-submits: that would fit twice."""
        deadline = time.monotonic() + (self.RUN_TIMEOUT_S if timeout_s is None else timeout_s)
        while wait and out.get("status") == "pending":
            if on_progress is not None:
                on_progress(out)
            task_id = out["task_id"]
            if time.monotonic() >= deadline:
                raise DatagoatError(Problem(504, "poll_timeout", "the task is still running",
                                            f"poll({task_id!r}) later; do not re-submit (that would fit twice)"))
            time.sleep(max(0.5, out.get("retry_after_ms", 2000) / 1000))
            out = self.poll(task_id)
        return out

    # -- the one call ----------------------------------------------------------- #
    def ask(self, questions: Mapping[str, Mapping[str, Any]], *, entity_column: str, subject_kind: str,
            dataset_id: Optional[str] = None, rows: Optional[Sequence[Mapping[str, Any]]] = None,
            csv: Optional[str] = None, fetch_url: Optional[str] = None, fetch_headers: Optional[Mapping[str, str]] = None,
            cases: Optional[Mapping[str, Any]] = None, time_column: Optional[str] = None,
            shape: Optional[Mapping[str, Any]] = None, band: bool = False,
            acknowledge_decision_support: bool = False, idempotency_key: Optional[str] = None,
            export: Optional[str] = None, model_ttl_days: Optional[int] = None,
            group_column: Optional[str] = None, namespace: Optional[str] = None,
            response_format: Optional[str] = None, wait: bool = True, timeout_s: Optional[float] = None,
            on_progress: Optional[Callable[[Dict[str, Any]], None]] = None) -> Dict[str, Any]:
        """Ask typed questions about cases, answered from a record of past outcomes.

        Build questions with `yesno`, `score`, `choice`, `rank`. `cases` is {"ids": [...]} or
        {"rows": [...]}; omit it to rank the whole record. When the record is not one row per case,
        pass `shape` (build it with `events`, `series`, `panel`, `signals` or `traces`) and
        `time_column`. Each answer's `state` is answered, refused or not_yet; a refusal is an
        answer, and retrying returns the same one.

        `export="csv"` adds `export.results_url`, a CSV of every case (one row per question per
        case) to fetch with `download` within 24 hours. The answer comes back whole at any size.

        For a product: `namespace` keeps a customer's models apart, `model_ttl_days` (1-365) sets how
        long a model this call fits keeps answering, and questions built with `from_model` answer
        from an existing model with no fit (then the record is optional when cases are rows).
        `group_column` names the case each row belongs to when a table has several rows per case.

        `response_format="concise"` leaves each answer's Verdicts out of the response
        (`verdicts_withheld`); `download(out["page"]["answer_url"])` returns the whole answer.

        A first fit on a large record answers pending; `ask` polls it to the end. Sending the ask
        again instead of polling would start (and bill) a second fit.
        """
        if not questions:
            raise ValueError("ask needs at least one question")
        for name, q in questions.items():
            if not isinstance(q, Mapping) or "type" not in q:
                raise ValueError(f"question {name!r} needs a type: yesno | score | choice | rank")
        data = _source(dataset_id, list(rows) if rows is not None else None, csv, fetch_url, fetch_headers,
                       optional=all("model_ref" in q for q in questions.values()))
        body: Dict[str, Any] = {
            **({"data": data} if data is not None else {}),
            "entity_column": entity_column, "subject_kind": subject_kind,
            "questions": {n: dict(q) for n, q in questions.items()},
            "idempotency_key": idempotency_key or str(uuid.uuid4()),
        }
        if cases is not None:
            body["cases"] = dict(cases)
        if time_column:
            body["time_column"] = time_column
        if shape is not None:
            body["shape"] = dict(shape)
        if band:
            body["band"] = True
        if acknowledge_decision_support:
            body["acknowledge_decision_support"] = True
        if export is not None:
            if export != "csv":
                raise ValueError('export is "csv"')
            body["export"] = export
        if response_format is not None:
            if response_format not in ("full", "concise"):
                raise ValueError('response_format is "full" or "concise"')
            body["response_format"] = response_format
        for k, v in (("model_ttl_days", model_ttl_days), ("group_column", group_column), ("namespace", namespace)):
            if v is not None:
                body[k] = v
        return self._whole(self._follow(self._call("ask", body), wait=wait, timeout_s=timeout_s, on_progress=on_progress))

    def _whole(self, out: Dict[str, Any]) -> Dict[str, Any]:
        """REST answers come whole; should one ever arrive paged, fetch the rest and join it."""
        cursor = (out.get("page") or {}).get("next_cursor")
        if out.get("status") != "done" or not cursor:
            return out
        pages: List[Dict[str, Any]] = []
        while cursor:
            nxt = self.page(cursor)
            pages.append(nxt)
            cursor = (nxt.get("page") or {}).get("next_cursor")
        merged = _merge_pages(out, pages)
        # Joined: there is no next page. The link stays only while it holds withheld Verdicts.
        if any("verdicts_withheld" in a for a in merged["answers"].values()):
            merged["page"] = {k: v for k, v in (out.get("page") or {}).items() if k != "next_cursor"}
        else:
            merged.pop("page", None)
        return merged

    def ask_many(self, questions: Mapping[str, Mapping[str, Any]], *, cases: Mapping[str, Any], chunk_size: int = 5000,
                 idempotency_key: Optional[str] = None, on_chunk: Optional[Callable[[int, int], None]] = None,
                 **kw: Any) -> Dict[str, Any]:
        """Ask about more cases than one call takes (10,000), in calls of `chunk_size` cases.

        Every chunk is answered by the same model (the fit is keyed by the record's content, so it
        runs once and later chunks reuse it); each answer's cases are joined in the order you gave
        them. rank is not accepted: a ranking is over the whole record, so ask it with `ask` and no
        cases. If every question is refused or not_yet on the first chunk, that is the answer for
        all of them (it is about the record, not the cases), and no further chunk is sent.

        Each chunk has its own idempotency key (`<key>:<n>`). Pass `idempotency_key` yourself, and
        re-running the same call after a failure resumes without fitting or billing the finished
        chunks again (without one, a fresh key is made each time). Every chunk is waited for, so
        `wait=False` is not accepted. The result has the
        shape of one ask; `task_ids` lists each chunk's task, and `fits_run` and
        `billable_decisions` are the sums over the chunks.
        """
        for name, q in questions.items():
            if isinstance(q, Mapping) and q.get("type") == "rank":
                raise ValueError(f"question {name!r} is a rank: a ranking is over the whole record; use ask() without cases")
        kinds = [k for k in ("ids", "rows") if k in cases]
        if len(kinds) != 1:
            raise ValueError('cases is {"ids": [...]} or {"rows": [...]}')
        kind = kinds[0]
        items = list(cases[kind])
        if not items:
            raise ValueError("no cases")
        if not 1 <= chunk_size <= 10_000:
            raise ValueError("chunk_size is 1 to 10,000")
        if kw.get("wait") is False:
            raise ValueError("ask_many waits for every chunk; wait=False would return part of the answer")
        base = idempotency_key or str(uuid.uuid4())
        if len(base) > 120:
            raise ValueError("idempotency_key is at most 120 characters here (a chunk number is added)")
        chunks = [items[i:i + chunk_size] for i in range(0, len(items), chunk_size)]
        outs: List[Dict[str, Any]] = []
        for n, part in enumerate(chunks):
            outs.append(self.ask(questions, cases={kind: part}, idempotency_key=f"{base}:{n}", **kw))
            out = outs[-1]
            if on_chunk is not None:
                on_chunk(n + 1, len(chunks))
            if n == 0 and all(a.get("state") != "answered" for a in (out.get("answers") or {}).values()):
                break
        return _join_chunks(outs)

    # -- supporting operations ----------------------------------------------------- #
    def add_dataset(self, *, rows: Optional[Sequence[Mapping[str, Any]]] = None, csv: Optional[str] = None,
                    fetch_url: Optional[str] = None, fetch_headers: Optional[Mapping[str, str]] = None,
                    upload: bool = False, dataset_id: Optional[str] = None, filename: Optional[str] = None) -> Dict[str, Any]:
        """Store a table once and ask about it by dataset_id. Pass dataset_id with rows to append a piece."""
        body = {k: v for k, v in {"rows": list(rows) if rows is not None else None, "csv": csv, "fetch_url": fetch_url,
                                   "fetch_headers": dict(fetch_headers) if fetch_headers else None,
                                   "upload": True if upload else None, "dataset_id": dataset_id, "filename": filename}.items() if v is not None}
        return self._call("add-dataset", body)

    def upload_rows(self, rows: Sequence[Mapping[str, Any]], *, chunk_size: int = 5000) -> str:
        """Send a large table in pieces and return its dataset_id."""
        rows = list(rows)
        if not rows:
            raise ValueError("no rows")
        first = self.add_dataset(rows=rows[:chunk_size])
        for i in range(chunk_size, len(rows), chunk_size):
            self.add_dataset(dataset_id=first["dataset_id"], rows=rows[i:i + chunk_size])
        return first["dataset_id"]

    def upload_file(self, path: str) -> str:
        """Upload a CSV file of any size through a presigned URL and return its dataset_id."""
        d = self.add_dataset(upload=True, filename=os.path.basename(path))
        with open(path, "rb") as f:  # streamed from disk, not read into memory
            req = urllib.request.Request(d["upload_url"], data=f, method="PUT", headers={
                "content-type": "text/csv", "content-length": str(os.path.getsize(path))})
            with urllib.request.urlopen(req, timeout=self.timeout):  # noqa: S310 - presigned URL we were given
                pass
        return d["dataset_id"]

    def delete_dataset(self, dataset_id: str) -> Dict[str, Any]:
        """Delete a stored dataset now. Otherwise it is deleted 24 hours after its last use."""
        return self._call("delete-dataset", {"dataset_id": dataset_id})

    def poll(self, task_id: str) -> Dict[str, Any]:
        return self._whole(self._call("poll", {"task_id": task_id}))

    def page(self, cursor: str) -> Dict[str, Any]:
        """The next page of a paged answer (answers over MCP are paged; REST answers come whole)."""
        return self._call("page", {"cursor": cursor})

    def download(self, url: str, path: Optional[str] = None) -> Any:
        """Fetch an export or answer link (valid 24 hours; the link itself is the credential, so no
        key is sent). With `path`, stream it to that file and return the path; else return the text."""
        req = urllib.request.Request(url, method="GET", headers={"user-agent": f"datagoat-python/{__version__}"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:  # noqa: S310 - a link Datagoat issued
                if path is None:
                    return r.read().decode("utf-8")
                with open(path, "wb") as f:
                    shutil.copyfileobj(r, f)
                return path
        except urllib.error.HTTPError as e:
            try:
                p = json.load(e)
            except Exception:  # noqa: BLE001
                p = {}
            raise error_for(Problem(e.code, p.get("code", "http_error"), p.get("detail", str(e.reason)),
                                    p.get("remedy", "ask again for a new link"), doc_url=p.get("doc_url"))) from None

    def preflight(self, dataset_id: str, *, outcome_column: Optional[str] = None,
                  predictors: Optional[Sequence[str]] = None, entity_column: Optional[str] = None) -> Dict[str, Any]:
        """Is the table worth asking about? Free; fits nothing. `entity_column` is reported as an
        identifier and never counted as a usable predictor."""
        body: Dict[str, Any] = {"dataset_id": dataset_id}
        if outcome_column:
            body["outcome_column"] = outcome_column
        if entity_column:
            body["entity_column"] = entity_column
        if predictors:
            body["predictors"] = list(predictors)
        return self._call("preflight", body)

    def report_outcomes(self, model_ref: str, outcomes: Sequence[Mapping[str, Any]], *,
                        namespace: Optional[str] = None, partial: Optional[bool] = None,
                        chunk_size: int = 10_000) -> Dict[str, Any]:
        """Record what really happened: [{entity_id, outcome, observed_at, event_id?}].

        Up to 10,000 rows (the API's most per call) go in ONE call, written whole or not at all:
        a bad row writes nothing and raises ValidationError (code invalid_outcomes) whose `errors`
        give every bad row's index. A longer list is sent in chunks of `chunk_size`; each chunk is
        atomic, but chunks before a failing one stay written. Any error from a later chunk
        (validation, rate limit, server, network) says so: `problem.detail` names the rows already
        written, `written_before` counts them, and `errors` index rows in `outcomes` as given.
        Resending the list is safe when every row has an event_id. With `partial=True` the valid
        rows are written and `results` lists every row by its index: written, duplicate, or error
        with field and detail."""
        rows = [dict(o) for o in outcomes]
        if not rows:
            raise ValueError("no outcomes")
        if not 1 <= chunk_size <= 10_000:
            raise ValueError("chunk_size is 1 to 10,000")
        total: Dict[str, Any] = {"written": 0, "duplicates": 0}
        results: List[Dict[str, Any]] = []
        for start in range(0, len(rows), chunk_size):
            body: Dict[str, Any] = {"model_ref": model_ref, "outcomes": rows[start:start + chunk_size], **_ns(namespace)}
            if partial is not None:
                body["partial"] = bool(partial)
            try:
                out = self._call("report-outcomes", body)
            except DatagoatError as e:
                if not start:
                    raise
                # A later chunk failed: rows 0..start-1 were written. Same class, indices in the
                # whole list, and the rows already written named.
                errs = e.problem.errors
                shifted = [{**x, "index": x["index"] + start} if isinstance(x.get("index"), int) else x for x in errs] if errs is not None else None
                err = type(e)(Problem(e.problem.status, e.problem.code,
                                      f"{e.problem.detail} (rows 0 to {start - 1} were already sent and written: "
                                      f"{total['written']} written, {total['duplicates']} duplicate; rows {start} onward were not)",
                                      e.problem.remedy, e.problem.field, e.problem.request_id, e.problem.doc_url, shifted))
                err.retry_after_s = e.retry_after_s
                err.written_before = start
                raise err from None
            total["written"] += int(out.get("written") or 0)
            total["duplicates"] += int(out.get("duplicates") or 0)
            for r in out.get("results") or []:
                results.append({**r, "index": r["index"] + start} if isinstance(r.get("index"), int) else r)
        if partial:
            total["results"] = results
        return total

    def attest(self, model_ref: str, entity_id: str, lever_token: str, post_value: Any, acted_at: str,
               event_id: Optional[str] = None, *, namespace: Optional[str] = None) -> Dict[str, Any]:
        """Record that you acted on a case through one of its levers: pass the lever's lever_token and
        the feature's value after the action. Returns compliant, dose_fraction and evaluated_feature."""
        body: Dict[str, Any] = {"model_ref": model_ref, "entity_id": entity_id, "lever_token": lever_token,
                                "post_value": post_value, "acted_at": acted_at, **_ns(namespace)}
        if event_id:
            body["event_id"] = event_id
        return self._call("attest", body)

    def evidence(self, model_ref: str, *, namespace: Optional[str] = None) -> Dict[str, Any]:
        """Did acting work? Outcomes of cases acted on vs not, once each group has 30 with an outcome."""
        return self._call("evidence", {"model_ref": model_ref, **_ns(namespace)})

    def track_record(self, model_ref: str, *, namespace: Optional[str] = None) -> Dict[str, Any]:
        """How the model's earlier calls held up: each reported outcome paired with the latest
        answer about that case given before it, overall and by band, level and chance range."""
        return self._call("track-record", {"model_ref": model_ref, **_ns(namespace)})

    def profile(self, namespace: Optional[str] = None, *, words: Optional[Mapping[str, Any]] = None,
                exclude: Optional[Sequence[str]] = None, display: Optional[str] = None,
                fixed: Optional[Sequence[str]] = None) -> Dict[str, Any]:
        """A namespace's profile. With words, exclude, fixed or display it replaces the profile;
        without, it returns the current one (or profile None). `fixed`: columns an action never
        changes (tenure, age): still read by the model, and no lever is offered on them."""
        body: Dict[str, Any] = {**_ns(namespace)}
        if words is not None:
            body["words"] = dict(words)
        if exclude is not None:
            body["exclude"] = list(exclude)
        if fixed is not None:
            body["fixed"] = list(fixed)
        if display is not None:
            body["display"] = display
        return self._call("profile", body)

    def drift(self, model_ref: str, *, namespace: Optional[str] = None) -> Dict[str, Any]:
        return self._call("drift", {"model_ref": model_ref, **_ns(namespace)})

    def suggest(self, entity_column: str, *, dataset_id: Optional[str] = None,
                rows: Optional[Sequence[Mapping[str, Any]]] = None, csv: Optional[str] = None,
                fetch_url: Optional[str] = None, fetch_headers: Optional[Mapping[str, str]] = None,
                time_column: Optional[str] = None, include_categories: bool = False) -> Dict[str, Any]:
        """Which yes/no questions this table could be asked, and whether each is worth asking. Free;
        fits nothing. Each candidate has a ready `question` to pass to `ask`."""
        body: Dict[str, Any] = {"data": _source(dataset_id, list(rows) if rows is not None else None, csv, fetch_url, fetch_headers),
                                "entity_column": entity_column}
        if time_column:
            body["time_column"] = time_column
        if include_categories:
            body["include_categories"] = True
        return self._call("suggest", body)

    def extend_model(self, model_ref: str, days: int, *, namespace: Optional[str] = None) -> Dict[str, Any]:
        """Keep a model answering until `days` (1-365) from now. Returns model_expires_at."""
        return self._call("extend-model", {"model_ref": model_ref, "days": int(days), **_ns(namespace)})

    def delete_model(self, model_ref: str, *, namespace: Optional[str] = None) -> Dict[str, Any]:
        """Delete a model now; its model_ref stops answering."""
        return self._call("delete-model", {"model_ref": model_ref, **_ns(namespace)})

    def verify(self, verdict: Mapping[str, Any], signature: Optional[Mapping[str, Any]]) -> str:
        """valid | invalid_signature | expired | unknown_key. Needs no key. Never act on anything but valid."""
        if not signature:
            return "invalid_signature"
        return self._call("verify", {"verdict": dict(verdict), "signature": dict(signature)}, keyless=True)["status"]

    def verify_all(self, answer: Mapping[str, Any]) -> bool:
        """True when every Verdict in an ask answer is valid."""
        return all(self.verify(v["verdict"], v.get("signature")) == "valid"
                   for a in (answer.get("answers") or {}).values() for v in a.get("verdicts", []))

    def describe(self) -> Dict[str, Any]:
        return self._call("describe", {}, keyless=True)


def _join_chunks(outs: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Chunks of one ask_many joined: cases concatenated in order, Verdicts listed chunk by chunk.
    A question whose chunks disagree on state or model (which only a change to the record between
    calls could cause) is not joined: its per-chunk answers are under `unjoined`."""
    first = outs[0]
    out: Dict[str, Any] = {k: v for k, v in first.items() if k not in ("task_id", "answers", "export")}
    out["task_ids"] = [o.get("task_id") for o in outs]
    for k in ("fits_run", "billable_decisions"):
        if any(k in o for o in outs):
            out[k] = sum(o.get(k) or 0 for o in outs)
    exports = [o["export"] for o in outs if o.get("export")]
    if exports:
        out["exports"] = exports
    answers: Dict[str, Any] = {}
    unjoined: Dict[str, Any] = {}
    for q in (first.get("answers") or {}):
        parts = [(o.get("answers") or {}).get(q) or {} for o in outs]
        same = all(p.get("state") == parts[0].get("state") and p.get("model_ref") == parts[0].get("model_ref")
                   and p.get("model_refs") == parts[0].get("model_refs") for p in parts)
        if not same:
            unjoined[q] = parts
            continue
        a = dict(parts[0])
        if "cases" in a:
            a["cases"] = [c for p in parts for c in (p.get("cases") or [])]
        if "verdicts" in a:
            a["verdicts"] = [v for p in parts for v in (p.get("verdicts") or [])]
        answers[q] = a
    out["answers"] = answers
    if unjoined:
        out["unjoined"] = unjoined
    return out


def register(base_url: Optional[str] = None, timeout: float = 30.0) -> Dict[str, Any]:
    """Get a free test key (sample datasets only). No account, no key needed."""
    base = (base_url or os.environ.get("DATAGOAT_BASE_URL") or DEFAULT_BASE).rstrip("/")
    return Client._request(f"{base}/v1/agents/register", {}, {}, timeout)
