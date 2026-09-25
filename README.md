# Datagoat SDKs

Ask yes/no, score, choice and rank questions about cases, answered from what happened to cases like
them. Deterministic, with reasons, a signed Verdict per answer, and an honest refusal when the
record can't support an answer.

This repository holds the Python and TypeScript clients, the OpenAPI description, the free sample
records, runnable examples, and a way to check any Verdict on your own machine with no call to
Datagoat. The service itself is hosted at `https://api.datagoat.io`; docs are at
[datagoat.io/docs](https://datagoat.io/docs).

## Install

```bash
pip install datagoat           # Python 3.9+, standard library only; CLI: datagoat
npm install @datagoat/sdk      # Node 20+, no dependencies
```

## A first answer, free

```bash
datagoat signup                # a free test key (dgk_test_…) for the sample records, no account
datagoat sample                # ask sample:saas_churn about one customer, and verify the answer
```

```python
from datagoat import Client, yesno
dg = Client()                  # DATAGOAT_API_KEY, or the key `datagoat signup` saved
out = dg.ask({"churn": yesno("churned", outcome_is_desirable=False)},
             dataset_id="sample:saas_churn", entity_column="customer_id", subject_kind="org",
             cases={"ids": ["cust_0001"]})
a = out["answers"]["churn"]
a["state"]                     # "answered" | "refused" | "not_yet": read it first
a["cases"][0]["p"], a["cases"][0]["reasons"]
```

## Check a Verdict yourself

Every answered question carries a Verdict signed with Ed25519. Check it against the published keys,
with no call to Datagoat; change one number and it fails.

```python
from datagoat import fetch_keys, verify_offline
v = a["verdicts"][0]
verify_offline(v["verdict"], v["signature"], fetch_keys())   # "valid" | "invalid_signature" | "expired" | "unknown_key"
```

```ts
import { fetchKeys, verifyOffline } from "@datagoat/sdk";
await verifyOffline(v.verdict, v.signature, await fetchKeys());
```

The signed bytes are the signature's `protected` header, a dot, and the Verdict as compact JSON with
sorted keys; the keys are at `https://api.datagoat.io/.well-known/jwks.json`. Both verifiers are
short enough to read in a few minutes: [python/datagoat/verify.py](python/datagoat/verify.py),
[typescript/src/verify.ts](typescript/src/verify.ts).

## Examples

Each runs on the free samples with a test key. See [examples/](examples/).

| Example | Shows |
|---|---|
| [01_first_answer.py](examples/01_first_answer.py) | an answer with its reasons, verified offline |
| [02_refused_then_answered.py](examples/02_refused_then_answered.py) | a refusal, then the same question answered once the activity log is added |
| [03_gate.py](examples/03_gate.py) | gating an action on state, band and a threshold |
| [04_scheduled_scorer.py](examples/04_scheduled_scorer.py) | one namespace per customer, fit once, score new cases with no fit |
| [05_verify_offline.py](examples/05_verify_offline.py) | checking a stored Verdict, with no network |
| [first_answer.ts](examples/first_answer.ts) | the first answer, in TypeScript |

## Layout

| Path | Holds |
|---|---|
| `python/` | the `datagoat` package (PyPI) and its tests |
| `typescript/` | `@datagoat/sdk` (npm) and its tests |
| `openapi/openapi.json` | every operation's request and response schema |
| `samples/` | the free sample records (synthetic) and their seeded generator |
| `examples/` | the examples above |

## Agents

The MCP server is `https://api.datagoat.io/mcp`. Agent skills, one per journey, and a Claude Code
plugin: [dmilstein-match/datagoat-skills](https://github.com/dmilstein-match/datagoat-skills).

## Contributing and security

This repository is published from Datagoat's main repository, so a change lands there first and
comes back here with the next release. Issues and pull requests are welcome:
[CONTRIBUTING.md](CONTRIBUTING.md). Security reports: [SECURITY.md](SECURITY.md).

Apache-2.0. The sample records are synthetic.
