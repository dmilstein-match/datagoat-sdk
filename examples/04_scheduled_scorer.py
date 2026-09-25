"""A product's daily job: one namespace per customer, fit once, then score cases from the model with
no fit. Here the "customer" is the telco sample.

On a test key the cases come from the sample record (cases.ids). With a live key, send each day's
new cases as rows instead, with no record at all:

    dg.ask({"churn": from_model(ref)}, entity_column="account_id", subject_kind="org", cases={"rows": today})
"""
from datagoat import Client, from_model, yesno

dg = Client()
fit = dg.ask({"churn": yesno("churned", outcome_is_desirable=False)},
             dataset_id="sample:telco_churn", entity_column="account_id", subject_kind="org",
             cases={"ids": ["acct_0001"]}, namespace="demo-customer", model_ttl_days=30)
answer = fit["answers"]["churn"]
ref = answer["model_ref"]
print("model:", ref, "| expires:", answer["model_expires_at"], "| reads:", answer["model_columns"])

scored = dg.ask({"churn": from_model(ref)}, dataset_id="sample:telco_churn", entity_column="account_id",
                subject_kind="org", cases={"ids": ["acct_0002", "acct_0003"]})
print("fits run:", scored.get("fits_run"))          # 0: scored from the model
for c in scored["answers"]["churn"]["cases"]:
    print(f'  {c["entity_id"]}: p {c["p"]:.3f}')
