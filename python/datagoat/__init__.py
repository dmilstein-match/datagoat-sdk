"""datagoat: ask yes/no, score, choice and rank questions about cases.

    from datagoat import Client, yesno, score, choice, rank
    dg = Client()
    out = dg.ask({"churn": yesno("churned", outcome_is_desirable=False)},
                 dataset_id="sample:saas_churn", entity_column="customer_id",
                 subject_kind="org", cases={"ids": ["cust_0001"]})
    out["answers"]["churn"]["cases"][0]["p"]         # the chance, with reasons and a signed Verdict

A record that is not one row per case takes a shape: events, series, panel, signals, traces or
snapshots. For a product: `suggest` lists the questions worth trying on a table, `from_model`
answers from an existing model with no fit, and `namespace` keeps each of your customers' models
apart. `verify_offline` checks a Verdict's signature with no call to Datagoat.
"""
from ._version import __version__
from .client import Client, DatagoatError, Problem, register
from .verify import fetch_keys, verify_offline
from .questions import choice, events, from_model, panel, rank, score, series, signals, snapshots, traces, yesno

__all__ = ["Client", "DatagoatError", "Problem", "register", "yesno", "score", "choice", "rank", "from_model",
           "events", "series", "panel", "signals", "traces", "snapshots", "verify_offline", "fetch_keys", "__version__"]
