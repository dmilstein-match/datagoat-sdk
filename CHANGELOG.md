# Changelog

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
