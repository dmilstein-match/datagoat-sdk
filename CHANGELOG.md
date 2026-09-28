# Changelog

## 1.9.0

- Watching a fit. `ask(..., on_progress=fn)` / `ask({ onProgress })`: while a first fit runs, the
  task's event stream (`GET /v1/tasks/{task_id}/events`, same key) is read and `fn` gets each stage
  the server reports: `stage`, `message` (the sentence, verbatim), `elapsed_ms`, `facts`, and
  `frac`, `fit`, `of` when known. A dropped connection resumes with `Last-Event-ID`. Where the
  stream cannot be opened, the SDK polls as before (same deadline) and `fn` gets each pending poll
  body, as in 1.8.0. Without a callback nothing changes: the stream is not opened.
- `events(task_id)` (Python iterator) / `events(taskId)` (TypeScript `AsyncIterable`): every event
  as `{event, id, data}`: `stage`, one `answer` per question, then `done` or `error`. `on_event` /
  `onEvent` on `ask` gets the same events.
- CLI: `datagoat ask ... --watch` draws each reported stage (a live checklist with
  `pip install "datagoat[watch]"` on a terminal; plain lines otherwise, on stderr). `--events`
  prints every event, then the answer, as JSON lines.
- No new required dependency; the answer and its Verdicts are unchanged.

## 1.8.0

- Contract 1.7.0. Typed errors under `DatagoatError`, one per code family: `ValidationError`
  (`field`; `errors` for `invalid_outcomes`), `NotFoundError` (`gone` on 410) and its
  `ModelUnavailableError` (`model_deleted`, `model_expired`, `model_ref_missing`),
  `RateLimitError`, `PaymentRequiredError`. Every problem carries `doc_url`. A refusal is still an
  answer, never an exception.
- `report_outcomes` / `reportOutcomes`: up to 10,000 rows is one atomic call; a longer list goes in
  chunks, a bad row is reported by its index in the whole list, and any error from a later chunk
  names the rows already written (`written_before` / `writtenBefore`). `partial=True` writes the valid rows and returns
  every row's result.
- New parameters: `response_format` ("concise" leaves Verdicts out; `page.answer_url` has them),
  `preflight(entity_column=...)`, `profile(fixed=[...])`.
- Answers may carry `too_few_predictors` refusals (`needs.columns`), per-case `unknown_id`, missing
  ranges, `excluded_columns`, and levers with their own token and `to_one_of`.

## 1.7.0

- Contract 1.6.0. `track_record` / `trackRecord` (how a model's calls held up against reported
  outcomes) and `profile` (a namespace's words, exclusions and display).
- Answers carry `decision` and `next`; a case the model cannot read is `state: not_scoreable`;
  `not_yet` carries `needs {labeled_rows, positives, countdown}` and `have`; a pending poll carries
  `progress`; answers in a profiled namespace carry `profile` and a `says` sentence per case.

## 1.6.0

- `verify_offline` / `fetch_keys` (Python) and `verifyOffline` / `fetchKeys` (TypeScript): check a
  Verdict's Ed25519 signature with no call to Datagoat. `datagoat verify … --offline` on the CLI.
- Public repository, examples, and the sample records' generator.

## 1.5.0

- Contract 1.5.0: daily scoring by `model_ref` on events and snapshots records; `row_not_scoreable`
  names every missing column; answers carry `model_columns`.
