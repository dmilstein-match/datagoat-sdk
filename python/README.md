# datagoat

Ask typed questions about cases: `yesno`, `score`, `choice` and `rank`. Datagoat answers from what
happened to cases like them. Every answered case comes back with a chance, the columns that moved
it, and a signed Verdict. When the record can't support an answer, Datagoat refuses rather than
guess.

## Where to start

<!-- generated:chooser (src/core/journeys.ts; npm run docs:build) -->
| Journey | Start here when the user… | Not here when… | First call |
|---|---|---|---|
| Try it | has no data yet, or wants to see an answer and a refusal before using their own | they already have a table of past cases (Ask your data) | `describe`, then `ask` (a sample's ready-to-run ask) |
| Ask your data | has a table of past cases with a yes/no outcome (churned, converted, faulted) and a question about it | they will score cases for many customers of their own product (Ship a product) | `add_dataset` (upload: true, one per source), then `map` (returns the ask; `backtest` and `ask` follow; a schedule needs fetch_url sources) |
| Ship a product | will score many customers' cases repeatedly, on a schedule, inside their own product | it is a one-off question about one table (Ask your data) | `ask` (with namespace and model_ttl_days), then `ask` (by model_ref, no fit) |
| Run it | already has a model_ref in use and is learning what happened to the cases it scored | no model has been fitted yet (Ask your data) | `report_outcomes`, then `schedule` (or refit_of + `drift` by hand) |
| Prove it | must show someone the calls were right, or measure whether acting on them worked | they only need the answer (Ask your data) | `verify`, then `track_record` (or `evidence`, whether acting on the calls worked) |
<!-- /generated:chooser -->

```bash
pip install datagoat
datagoat signup      # a free test key for the sample records
datagoat sample      # ask about sample:saas_churn and verify the answer
```

Every quickstart below runs as written on the free samples with a test key, except where it says
a live key.

### Try it

```python
from datagoat import Client, yesno

dg = Client()                            # DATAGOAT_API_KEY, or the key `datagoat signup` saved
about = dg.describe()                    # journeys, question types, shapes, limits, prices, samples
sample = about["samples"][0]             # each sample carries a ready-to-run ask under "try"
t = sample["try"]
out = dg.ask(t["questions"], dataset_id=sample["dataset_id"], entity_column=t["entity_column"],
             subject_kind=t["subject_kind"], cases=t["cases"],
             **({"time_column": t["time_column"]} if "time_column" in t else {}),
             **({"shape": t["shape"]} if "shape" in t else {}))
a = out["answers"]["q"]
a["state"]                 # "answered" | "refused" | "not_yet": read it first
a["cases"][0]["p"]         # the chance, from the engine
a["cases"][0]["reasons"]   # the columns that moved it, which way, and each value's range
dg.verify_all(out)         # True: every Verdict is genuine
```

### Ask your data

```python
sug = dg.suggest("account_id", dataset_id="sample:telco_churn")       # your table: add_dataset(rows=...) first
worth = [c for c in sug["candidates"] if c["worth_asking"]]
dg.map(["sample:telco_churn"])      # the table's report: resolutions, fitness, advisory (preflight is deprecated)
out = dg.ask({"churn": yesno("churned", outcome_is_desirable=False)}, dataset_id="sample:telco_churn",
             entity_column="account_id", subject_kind="org", cases={"ids": ["acct_0001"]})
out["answers"]["churn"]["excluded_columns"]    # the columns the model left out, and why
```

A first fit on a large record answers `pending`; `ask` polls it for you. Sending the ask again
instead of polling would start (and bill) a second fit.

### Ship a product

```python
from datagoat import from_model

fit = dg.ask({"churn": yesno("churned", outcome_is_desirable=False)}, dataset_id="sample:saas_churn",
             entity_column="customer_id", subject_kind="org", cases={"ids": ["cust_0001"]},
             namespace="acme", model_ttl_days=180)          # one namespace per customer
ref = fit["answers"]["churn"]["model_ref"]
today = dg.ask({"churn": from_model(ref)}, dataset_id="sample:saas_churn",
               entity_column="customer_id", subject_kind="org", cases={"ids": ["cust_0002"]})
today["fits_run"]                                           # 0: scored from the model, no fit
# With a live key, new cases can be rows with no record: cases={"rows": [...]}.
```

### Run it

```python
dg.drift(ref)                            # keep | refit | abandon | no_check_yet
dg.extend_model(ref, 90)                 # renew a model you keep
# Live key with "Can report outcomes": sent in chunks, each written whole or not at all.
# dg.report_outcomes(ref, [{"entity_id": "c1", "outcome": 1, "observed_at": "2026-10-01", "event_id": "c1:won"}])
```

### Prove it

```python
from datagoat import fetch_keys, verify_offline

v = a["verdicts"][0]
verify_offline(v["verdict"], v["signature"], fetch_keys())   # "valid", with no call to Datagoat
dg.track_record(ref)                                         # the model's calls against reported outcomes
```

## Watching a fit

A first fit on a large record answers `pending`, and `ask` waits for it. With `on_progress`, it
reads the task's event stream while it waits and passes on each stage the server reports, with
its `message` (the sentence to show, verbatim), `elapsed_ms`, `facts` and, when known, `frac`:

```python
out = dg.ask({"churn": yesno("churned", outcome_is_desirable=False)}, dataset_id="sample:saas_churn",
             entity_column="customer_id", subject_kind="org", cases={"ids": ["cust_0001"]},
             on_progress=lambda e: print(e.get("message") or e.get("status")))
# dg.events(task_id) is the same stream as an iterator of {event, id, data}
```

A dropped connection resumes from the last event. Where there is no stream, `ask` polls as before
and `on_progress` gets each pending poll body. The answer itself always comes from `poll`.

```bash
pip install "datagoat[watch]"        # adds rich, for the live checklist
datagoat ask '{"churn": {"type": "yesno", "outcome_column": "churned"}}' \
  --data sample:saas_churn --entity customer_id --cases cust_0001 --watch
```

`--watch` ticks each finished stage with its time and spins on the current one (plain lines when
not on a terminal, on stderr); `--events` prints every event, then the answer, as JSON lines.

## Errors

Every error is a `DatagoatError` with `problem.code`, `problem.remedy`, `problem.field` and
`problem.doc_url`. Catch a family by its class: `ValidationError` (`field`, and `errors` for
`invalid_outcomes`), `NotFoundError` and its `ModelUnavailableError` (`model_deleted`,
`model_expired`, `model_ref_missing`: ask again with the record to fit a new model),
`RateLimitError`, `PaymentRequiredError`. A refusal is not an error: it is an answer with
`state: "refused"`. The client retries rate limits and safe calls on its own.

## More

Records that aren't one row per case take a shape: `events`, `series`, `panel`, `signals`,
`traces` or `snapshots`. At volume, `ask_many` asks about more than 10,000 cases in chunks on one
fit; `export="csv"` adds a CSV of every case; `response_format="concise"` leaves Verdicts out of the
response (`page.answer_url` keeps them), and `"compact"` also shows each case as `entity_id`, `p`,
`p_display` (and `band`, `level`, `says` when present). On the command line:
`datagoat verify verdict.json signature.json --offline`.

- Docs: https://datagoat.io/docs
- SDK reference: https://datagoat.io/docs/sdks
- Quick start: https://datagoat.io/docs/quickstart
