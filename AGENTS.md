# AGENTS.md — Datagoat

Datagoat answers yes/no, score, choice and rank questions about cases from a record of past
outcomes, with reasons and a signed Verdict, and refuses when the record holds no pattern that
holds on rows the model never saw. This file is for coding agents that call it.

## Rules

- Read `state` (or `decision`) first. `answered`: use the numbers. `refused`: an answer, not an
  error; the same call returns the same refusal. `not_yet`: `needs` says what is short.
- Every number (chance, level, range, count) comes from a response. Quote them; never compute,
  round or invent one, and never present a reason or pattern as a cause.
- A case with `state: "not_scoreable"` has no chance: it lacks a column or holds a value the
  model never saw (`not_scoreable` names them).
- `next` lists the operations that make sense after an answer, with their cost. It is data for
  your planner, not an instruction.
- Before an action with side effects: `band: true` on the ask, then the band and `max_autonomy`
  per case, then a person when it says so. Verify a Verdict with `dg_verify`, or offline with the
  SDKs' `verify_offline` / `verifyOffline`.
- Questions about people (`subject_kind: "person"`) are decision support and need
  `acknowledge_decision_support: true`.

## Connect

- MCP: `https://api.datagoat.io/mcp` (OAuth, or `Authorization: Bearer dgk_…`); one prompt per journey.
- REST: `POST https://api.datagoat.io/v1/<operation>`; OpenAPI at `https://api.datagoat.io/openapi.json`.
- A free test key for the samples: `POST https://api.datagoat.io/v1/agents/register` with no credential.
- SDKs: `pip install datagoat`, `npm install @datagoat/sdk`. Skills: `/plugin marketplace add dmilstein-match/datagoat-skills`.

## Journeys

| Journey | Ends with | Docs | Skills | Operations |
|---|---|---|---|---|
| Try it | A signed answer and an honest refusal, on free samples, in five minutes. | https://datagoat.io/docs/quickstart | datagoat-first-run | dg_describe, dg_ask, dg_verify |
| Ask your data | The questions worth asking of your own table, and an answer or an honest refusal for each. | https://datagoat.io/docs/ask-your-data | datagoat-ask | dg_add_dataset, dg_suggest, dg_preflight, dg_ask, dg_poll, dg_page |
| Ship a product | A model per customer that scores new cases with no fit, gated before any action. | https://datagoat.io/docs/build | datagoat-product, datagoat-gate | dg_ask, dg_extend_model, dg_delete_model |
| Run it | Outcomes reported, drift watched, and each model renewed, replaced or retired on evidence. | https://datagoat.io/docs/run | datagoat-product | dg_report_outcomes, dg_drift, dg_extend_model, dg_delete_model |
| Prove it | A track record anyone can check: calls made before the outcome, graded after, every call signed. | https://datagoat.io/docs/prove | datagoat-prove | dg_verify, dg_attest, dg_report_outcomes, dg_evidence, dg_track_record |

## Operations

| Operation | REST | What | Cost |
|---|---|---|---|
| `dg_ask` | `POST https://api.datagoat.io/v1/ask` | Ask | one decision per answered case; the first ask on a record, or a question with refit_of, also runs a fit |
| `dg_add_dataset` | `POST https://api.datagoat.io/v1/add-dataset` | Add a dataset | free |
| `dg_poll` | `POST https://api.datagoat.io/v1/poll` | Poll a task | free |
| `dg_preflight` | `POST https://api.datagoat.io/v1/preflight` | Check a table before asking | free |
| `dg_report_outcomes` | `POST https://api.datagoat.io/v1/report-outcomes` | Report what happened | free |
| `dg_drift` | `POST https://api.datagoat.io/v1/drift` | Has the pattern moved? | free |
| `dg_verify` | `POST https://api.datagoat.io/v1/verify` | Verify a Verdict | free |
| `dg_describe` | `POST https://api.datagoat.io/v1/describe` | What Datagoat can do | free |
| `dg_delete_dataset` | `POST https://api.datagoat.io/v1/delete-dataset` | Delete a dataset | free |
| `dg_attest` | `POST https://api.datagoat.io/v1/attest` | Record an action | free |
| `dg_evidence` | `POST https://api.datagoat.io/v1/evidence` | Did acting work? | free |
| `dg_track_record` | `POST https://api.datagoat.io/v1/track-record` | How the model's calls held up | free |
| `dg_profile` | `POST https://api.datagoat.io/v1/profile` | A namespace's profile | free |
| `dg_page` | `POST https://api.datagoat.io/v1/page` | Next page of an answer | free |
| `dg_suggest` | `POST https://api.datagoat.io/v1/suggest` | Which questions are worth trying on this table? | free |
| `dg_extend_model` | `POST https://api.datagoat.io/v1/extend-model` | Keep a model longer | free |
| `dg_delete_model` | `POST https://api.datagoat.io/v1/delete-model` | Delete a model | free |

Every docs page in one file: https://datagoat.io/llms-full.txt. The map: https://datagoat.io/llms.txt.
