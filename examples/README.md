# Examples

Every example runs on the free sample records. Get a test key first (no account, no card):

```bash
pip install datagoat
datagoat signup          # or: export DATAGOAT_API_KEY=$(curl -s -X POST https://api.datagoat.io/v1/agents/register | jq -r .api_key)
python 01_first_answer.py
```

| File | Journey | Shows |
|---|---|---|
| `01_first_answer.py` | Try it | an answer with its reasons, each Verdict checked offline |
| `02_refused_then_answered.py` | Ask your data | a refusal from the deal table alone, then an answer once the activity log is read as of each stage |
| `03_gate.py` | Ship a product | state, then the engine's band and `max_autonomy`, then your threshold; an idempotency key per action |
| `04_scheduled_scorer.py` | Ship a product | one namespace per customer, fit once, score new cases from the model with no fit |
| `05_verify_offline.py` | Prove it | a stored Verdict checked with no network; one changed number fails |
| `first_answer.ts` | Try it | the first answer in TypeScript |

Numbers come from the service, not from these files: the samples are deterministic, so the same
call gives the same answer. The samples are synthetic.

CI runs `05_verify_offline.py` on every push and the others weekly against the live service.
