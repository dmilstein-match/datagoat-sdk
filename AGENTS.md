# AGENTS.md — Datagoat

Datagoat answers yes/no, score, choice and rank questions about cases from a record of past
outcomes, with reasons and a signed Verdict, and refuses when the record holds no pattern that
holds on rows the model never saw. This file is for coding agents that call it.

## Which journey

`dg_describe` (`POST https://api.datagoat.io/v1/describe`, no key needed) lists these with a ready-to-run call and `start`, the journey for the caller.

| Journey | Fits when the user… | Not when… | First call |
|---|---|---|---|
| [Try it](https://datagoat.io/docs/quickstart) | has no data yet, or wants to see an answer and a refusal before using their own | they already have a table of past cases (Ask your data) | `dg_describe`, then `dg_ask` (a sample's ready-to-run ask) |
| [Ask your data](https://datagoat.io/docs/ask-your-data) | has a table of past cases with a yes/no outcome (churned, converted, faulted) and a question about it | they will score cases every day for their own customers (Ship a product) | `dg_add_dataset` (upload: true for a file a person holds), then `dg_suggest` |
| [Ship a product](https://datagoat.io/docs/build) | will score many customers' cases repeatedly, on a schedule, inside their own product | it is a one-off question about one table (Ask your data) | `dg_ask` (with namespace and model_ttl_days), then `dg_ask` (by model_ref, no fit) |
| [Run it](https://datagoat.io/docs/run) | already has a model_ref in use and is learning what happened to the cases it scored | no model has been fitted yet (Ask your data) | `dg_report_outcomes`, then `dg_drift` |
| [Prove it](https://datagoat.io/docs/prove) | must show someone the calls were right, or measure whether acting on them worked | they only need the answer (Ask your data) | `dg_verify`, then `dg_track_record` (or dg_evidence, whether acting on the calls worked) |

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
- The journeys as Arazzo 1.1.0 workflows: `https://api.datagoat.io/journeys.arazzo.yaml`.
- A free test key for the samples: `POST https://api.datagoat.io/v1/agents/register` with no credential.
- SDKs: `pip install datagoat`, `npm install @datagoat/sdk`. Skills: `/plugin marketplace add dmilstein-match/datagoat-skills`.

## Journeys

| Journey | Ends with | Docs | Skills | Operations |
|---|---|---|---|---|
| Try it | A signed answer and an honest refusal, on free samples, in five minutes. | https://datagoat.io/docs/quickstart | datagoat-first-run | dg_describe, dg_ask, dg_verify |
| Ask your data | The questions worth asking of your own table, and an answer or an honest refusal for each. | https://datagoat.io/docs/ask-your-data | datagoat-ask | dg_add_dataset, dg_suggest, dg_preflight, dg_ask, dg_poll, dg_page |
| Ship a product | A model per customer that scores new cases with no fit, gated before any action. | https://datagoat.io/docs/build | datagoat-product, datagoat-gate | dg_ask, dg_profile, dg_extend_model, dg_delete_model |
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

## Optional

Every docs page in one file: https://datagoat.io/llms-full.txt. The map: https://datagoat.io/llms.txt. The skills: https://datagoat.io/.well-known/agent-skills/index.json.

The free sample records:

- `sample:saas_churn` (table): 800 synthetic SaaS accounts: tenure, charges, support tickets, logins, plan, seats. Which will churn?
- `sample:b2b_leads` (table): 800 synthetic B2B leads: pages viewed, demo requests, company size, touch latency. Which will convert?
- `sample:telco_churn` (table): 800 synthetic telecom accounts: contract, tech support, tenure, charges. Which will churn?
- `sample:customer_events` (events): An event log: a year of visits, purchases, refunds and support tickets from 800 synthetic shop customers. Which customers have gone quiet?
- `sample:store_weekly` (series): A time series: 60 synthetic stores over 60 weeks, with sales, footfall, stock cover and late deliveries. Which stores will run out of stock next week?
- `sample:usage_panel` (panel): A panel: 800 synthetic SaaS accounts over 12 monthly periods. The outcome is read from the trend in usage. Which accounts are declining?
- `sample:sensor_stream` (signals): Signals: four sensors on 40 synthetic machines every six hours for 30 days, with fault intervals in the same table. Which machines will fault in the next three days?
- `sample:agent_traces` (traces): Traces: 800 synthetic AI-agent runs with the agent, task and tool of each, one tool degrading mid-month. Which runs will fail?
- `sample:deal_activity` (snapshots): Snapshots: sales activity (calls, emails, meetings) on 1,200 synthetic B2B deals, read as of the moment each deal entered each of three stages (the snapshot table sample:deal_stages). Which deals will be won?
- `sample:deal_stages` (part of sample:deal_activity): The snapshot table of sample:deal_activity: one row per deal per stage entered (3,600 rows), with the deal's amount, when it closed and whether it was won. Asked alone it is refused: the stage and amount do not predict the outcome; the activity before each stage does.
