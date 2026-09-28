# @datagoat/sdk

Ask typed questions about cases: `yesno`, `score`, `choice` and `rank`. Datagoat answers from what
happened to cases like them. Every answered case comes back with a chance, the columns that moved
it, and a signed Verdict. When the record can't support an answer, Datagoat refuses rather than
guess.

## Where to start

<!-- generated:chooser (src/core/journeys.ts; npm run docs:build) -->
| Journey | Start here when the user… | Not here when… | First call |
|---|---|---|---|
| Try it | has no data yet, or wants to see an answer and a refusal before using their own | they already have a table of past cases (Ask your data) | `describe`, then `ask` (a sample's ready-to-run ask) |
| Ask your data | has a table of past cases with a yes/no outcome (churned, converted, faulted) and a question about it | they will score cases every day for their own customers (Ship a product) | `addDataset` (upload: true for a file a person holds), then `suggest` |
| Ship a product | will score many customers' cases repeatedly, on a schedule, inside their own product | it is a one-off question about one table (Ask your data) | `ask` (with namespace and model_ttl_days), then `ask` (by model_ref, no fit) |
| Run it | already has a model_ref in use and is learning what happened to the cases it scored | no model has been fitted yet (Ask your data) | `reportOutcomes`, then `drift` |
| Prove it | must show someone the calls were right, or measure whether acting on them worked | they only need the answer (Ask your data) | `verify`, then `trackRecord` (or `evidence`, whether acting on the calls worked) |
<!-- /generated:chooser -->

```bash
npm install @datagoat/sdk
```

A free test key for the samples: `await register()`, or `POST https://api.datagoat.io/v1/agents/register`.
Every quickstart below runs as written on the free samples with a test key, except where it says a
live key.

### Try it

```ts
import { Datagoat, yesno } from "@datagoat/sdk";

const dg = new Datagoat({ apiKey: process.env.DATAGOAT_API_KEY! });
const about: any = await dg.describe();        // journeys, question types, shapes, limits, prices, samples
const t = about.samples[0].try;                // each sample carries a ready-to-run ask
const { questions, ...rest } = t;
const out = await dg.ask(questions, rest);
const a = out.answers!.q;
a.state;                   // "answered" | "refused" | "not_yet": read it first
a.cases[0].p;              // the chance, from the engine
a.cases[0].reasons;        // the columns that moved it, which way, and each value's range
await dg.verifyAll(out);   // true: every Verdict is genuine
```

### Ask your data

```ts
const sug: any = await dg.suggest({ data: { dataset_id: "sample:telco_churn" }, entity_column: "account_id" }); // your table: addDataset first
const worth = sug.candidates.filter((c: any) => c.worth_asking);
await dg.preflight("sample:telco_churn", { outcome_column: "churned", entity_column: "account_id" });
const ans = await dg.ask({ churn: yesno("churned", { outcome_is_desirable: false }) },
  { data: { dataset_id: "sample:telco_churn" }, entity_column: "account_id", subject_kind: "org", cases: { ids: ["acct_0001"] } });
ans.answers!.churn.excluded_columns;           // the columns the model left out, and why
```

A first fit on a large record answers `pending`; `ask` polls it for you. Sending the ask again
instead of polling would start (and bill) a second fit.

### Ship a product

```ts
import { fromModel } from "@datagoat/sdk";

const fit = await dg.ask({ churn: yesno("churned", { outcome_is_desirable: false }) }, {
  data: { dataset_id: "sample:saas_churn" }, entity_column: "customer_id", subject_kind: "org", cases: { ids: ["cust_0001"] },
  namespace: "acme", model_ttl_days: 180,                                   // one namespace per customer
});
const ref = fit.answers!.churn.model_ref;
const today = await dg.ask({ churn: fromModel(ref) }, {
  data: { dataset_id: "sample:saas_churn" }, entity_column: "customer_id", subject_kind: "org", cases: { ids: ["cust_0002"] },
});
today.fits_run;                                                              // 0: scored from the model, no fit
// With a live key, new cases can be rows with no record: cases: { rows: [...] }.
```

### Run it

```ts
await dg.drift(ref);                     // keep | refit | abandon | no_check_yet
await dg.extendModel(ref, 90);           // renew a model you keep
// Live key with "Can report outcomes": sent in chunks, each written whole or not at all.
// await dg.reportOutcomes(ref, [{ entity_id: "c1", outcome: 1, observed_at: "2026-10-01", event_id: "c1:won" }]);
```

### Prove it

```ts
import { fetchKeys, verifyOffline } from "@datagoat/sdk";

const v = a.verdicts![0];
await verifyOffline(v.verdict, v.signature, await fetchKeys());   // "valid", with no call to Datagoat
await dg.trackRecord(ref);                                         // the model's calls against reported outcomes
```

## Errors

Every error is a `DatagoatError` with `problem.code`, `problem.remedy`, `problem.field` and
`problem.doc_url`. Catch a family by its class: `ValidationError` (`field`, and `errors` for
`invalid_outcomes`), `NotFoundError` and its `ModelUnavailableError` (`model_deleted`,
`model_expired`, `model_ref_missing`: ask again with the record to fit a new model),
`RateLimitError`, `PaymentRequiredError`. A refusal is not an error: it is an answer with
`state: "refused"`. The client retries rate limits and safe calls on its own.

## More

Records that aren't one row per case take a shape: `events`, `series`, `panel`, `signals`,
`traces` or `snapshots`. At volume, `askMany` asks about more than 10,000 cases in chunks on one
fit; `export: "csv"` adds a CSV of every case; `response_format: "concise"` leaves Verdicts out of
the response (`page.answer_url` keeps them).

- Docs: https://datagoat.io/docs
- SDK reference: https://datagoat.io/docs/sdks
- Quick start: https://datagoat.io/docs/quickstart
