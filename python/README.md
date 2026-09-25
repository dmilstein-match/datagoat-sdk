# datagoat

Ask typed questions about cases: `yesno`, `score`, `choice` and `rank`. Datagoat answers from what
happened to cases like them. Every answered case comes back with a chance, the columns that moved
it, and a signed Verdict. When the record can't support an answer, Datagoat refuses rather than
guess.

```bash
pip install datagoat
datagoat signup      # a free test key for the sample records
datagoat sample      # ask about sample:saas_churn and verify the answer
```

Every call sends a **record** (past cases and their outcomes), **questions**, and the **cases**
to answer:

```python
from datagoat import Client, yesno, score

dg = Client()        # DATAGOAT_API_KEY, or the key `datagoat signup` saved
out = dg.ask(
    {"churn": yesno("churned", outcome_is_desirable=False),
     "risk":  score("churned", outcome_is_desirable=False)},
    dataset_id="sample:saas_churn", entity_column="customer_id", subject_kind="org",
    cases={"ids": ["cust_0001"]},
)
a = out["answers"]["churn"]
a["state"]                 # "answered" | "refused" | "not_yet": read it first
a["cases"][0]["p"]         # 0.7005: the chance cust_0001 churns
a["cases"][0]["reasons"]   # the columns that moved it, which way, and each value's band ("tenure_months 17 to under 26")
a["pattern"]               # where churn is most common ("support_tickets over 3", …)
a["cases"][0]["pattern_match"]  # {"matched": 3, "of": 4, "met": [...]}
dg.verify_all(out)         # True: every Verdict is genuine
```

Check a Verdict yourself, with no call to Datagoat (Ed25519, against the published keys):

```python
from datagoat import fetch_keys, verify_offline
keys = fetch_keys()                                   # https://api.datagoat.io/.well-known/jwks.json; keep it
v = a["verdicts"][0]
verify_offline(v["verdict"], v["signature"], keys)    # "valid" | "invalid_signature" | "expired" | "unknown_key"
```

On the command line: `datagoat verify verdict.json signature.json --offline`.

Records that aren't one row per case take a shape: `events`, `series`, `panel`, `signals`,
`traces` or `snapshots`.

Building a product for your own customers:

```python
dg.suggest("customer_id", dataset_id="ds_…")          # the questions worth trying on the table
fit = dg.ask({"churn": yesno("churned", outcome_is_desirable=False)}, dataset_id="ds_…",
             entity_column="customer_id", subject_kind="org", cases={"ids": ["c1"]},
             namespace="acme", model_ttl_days=180)     # one namespace per customer
ref = fit["answers"]["churn"]["model_ref"]
dg.ask({"churn": from_model(ref)}, entity_column="customer_id", subject_kind="org",
       cases={"rows": new_rows})                       # no record, no fit
dg.extend_model(ref, 365); dg.delete_model(ref)
```

Guide: https://datagoat.io/docs/build

At volume: `dg.ask_many(questions, cases={"ids": ids}, ...)` asks about more than 10,000 cases in
chunks on one fit and joins them in order; `export="csv"` on `ask` adds a CSV of every case
(`dg.download(out["export"]["results_url"], "results.csv")`). The client retries rate limits and
safe calls on its own.

- Docs: https://datagoat.io/docs
- SDK reference: https://datagoat.io/docs/sdks
- Quick start: https://datagoat.io/docs/quickstart
