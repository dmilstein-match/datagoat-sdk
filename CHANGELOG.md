# Changelog

## 1.6.0

- `verify_offline` / `fetch_keys` (Python) and `verifyOffline` / `fetchKeys` (TypeScript): check a
  Verdict's Ed25519 signature with no call to Datagoat. `datagoat verify … --offline` on the CLI.
- Public repository, examples, and the sample records' generator.

## 1.5.0

- Contract 1.5.0: daily scoring by `model_ref` on events and snapshots records; `row_not_scoreable`
  names every missing column; answers carry `model_columns`.
