# @datagoat/sdk

Ask typed questions about cases: `yesno`, `score`, `choice` and `rank`. Datagoat answers from what
happened to cases like them. Every answered case comes back with a chance, the columns that moved
it, and a signed Verdict. When the record can't support an answer, Datagoat refuses rather than
guess.

```bash
npm install @datagoat/sdk
```

Every call sends a **record** (past cases and their outcomes), **questions**, and the **cases**
to answer:

```ts
import { Datagoat, yesno, score } from "@datagoat/sdk";

const dg = new Datagoat({ apiKey: process.env.DATAGOAT_API_KEY! });
const out = await dg.ask(
  { churn: yesno("churned", { outcome_is_desirable: false }),
    risk: score("churned", { outcome_is_desirable: false }) },
  { data: { dataset_id: "sample:saas_churn" }, entity_column: "customer_id", subject_kind: "org",
    cases: { ids: ["cust_0001"] } },
);
const a = out.answers!.churn;
a.state;                   // "answered" | "refused" | "not_yet": read it first
a.cases[0].p;              // 0.7005: the chance cust_0001 churns
a.cases[0].reasons;        // the columns that moved it, which way, and each value's band ("tenure_months 17 to under 26")
a.pattern;                 // where churn is most common ("support_tickets over 3", …)
a.cases[0].pattern_match;  // { matched: 3, of: 4, met: [...] }
await dg.verifyAll(out);   // true: every Verdict is genuine
```

A free test key for the samples: `await register()`, or `POST https://api.datagoat.io/v1/agents/register`.

Check a Verdict yourself, with no call to Datagoat (Ed25519 through WebCrypto, against the published keys):

```ts
import { fetchKeys, verifyOffline } from "@datagoat/sdk";
const keys = await fetchKeys();                        // https://api.datagoat.io/.well-known/jwks.json; keep it
const v = a.verdicts![0];
await verifyOffline(v.verdict, v.signature, keys);     // "valid" | "invalid_signature" | "expired" | "unknown_key"
```

Records that aren't one row per case take a shape: `events`, `series`, `panel`, `signals`,
`traces` or `snapshots`.

Building a product for your own customers:

```ts
await dg.suggest({ data: { dataset_id: "ds_…" }, entity_column: "customer_id" }); // the questions worth trying on the table
const fit = await dg.ask({ churn: yesno("churned", { outcome_is_desirable: false }) }, {
  data: { dataset_id: "ds_…" }, entity_column: "customer_id", subject_kind: "org", cases: { ids: ["c1"] },
  namespace: "acme", model_ttl_days: 180,                                          // one namespace per customer
});
const ref = fit.answers!.churn.model_ref;
await dg.ask({ churn: fromModel(ref) }, { entity_column: "customer_id", subject_kind: "org", cases: { rows: newRows } }); // no record, no fit
await dg.extendModel(ref, 365); await dg.deleteModel(ref);
```

Guide: https://datagoat.io/docs/build

At volume: `dg.askMany(questions, { ..., cases: { ids } })` asks about more than 10,000 cases in
chunks on one fit and joins them in order; `export: "csv"` on `ask` adds a CSV of every case
(`await dg.download((out.export as { results_url: string }).results_url)`). The client retries rate limits and safe calls on
its own.

- Docs: https://datagoat.io/docs
- SDK reference: https://datagoat.io/docs/sdks
- Quick start: https://datagoat.io/docs/quickstart
