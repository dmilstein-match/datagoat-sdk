"""An answer with its reasons, checked on your own machine. Runs free on the samples.

    pip install datagoat && datagoat signup && python 01_first_answer.py
"""
from datagoat import Client, fetch_keys, score, verify_offline, yesno

dg = Client()
out = dg.ask({"churn": yesno("churned", outcome_is_desirable=False),
              "risk": score("churned", outcome_is_desirable=False)},
             dataset_id="sample:saas_churn", entity_column="customer_id", subject_kind="org",
             cases={"ids": ["cust_0001"]})

churn = out["answers"]["churn"]
print("state:", churn["state"])                    # read it first
if churn["state"] == "answered":
    case = churn["cases"][0]
    print("chance:", case["p"], "| level:", out["answers"]["risk"]["cases"][0]["level"])
    for r in case["reasons"]:                      # in the order given, words from the answer
        print(f'  {r["range"]["text"]}: {r["strength"]}, {r["likelihood_direction"]} chance')
    keys = fetch_keys()
    for v in churn["verdicts"]:
        print("verdict:", verify_offline(v["verdict"], v["signature"], keys))
