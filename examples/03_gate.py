"""Gate an action on the answer: state first, then the engine's band, then your own threshold.
Prints what an agent would do for three accounts; it takes no action."""
from datagoat import Client, yesno

dg = Client()
out = dg.ask({"churn": yesno("churned", outcome_is_desirable=False)}, band=True,
             dataset_id="sample:telco_churn", entity_column="account_id", subject_kind="org",
             cases={"ids": ["acct_0001", "acct_0002", "acct_0003"]})
a = out["answers"]["churn"]
if a["state"] != "answered":
    print("no action from this answer:", a["state"])   # refused or not_yet: take the fallback
else:
    verdict_id = a["verdicts"][0]["verdict"]["verdict_id"]
    for c in a["cases"]:
        key = f"{verdict_id}:{c['entity_id']}:retention_call"   # one action per Verdict and case, even on retry
        if c["band"] == "act" and c["max_autonomy"] == "L3":
            plan = "act, then queue for review"
        elif c["band"] == "act" and c["max_autonomy"] == "L2":
            plan = "ask a person, act on approval"
        elif c["band"] == "escalate":
            plan = "suggest to a person"
        else:
            plan = "no action for this case"
        print(f'{c["entity_id"]}: p {c["p"]:.3f} | {c["band"]} / {c["band_reason"]} / {c["max_autonomy"]} -> {plan} [{key}]')
