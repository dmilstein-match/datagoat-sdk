"""The same question refused, then answered once the evidence it needed is added.

The deal table alone (stage and amount) holds no pattern that survives on unseen deals, so the
answer is a refusal. Read with the deals' activity log as of each stage, it answers.
"""
from datagoat import Client, snapshots, yesno

dg = Client()
won = {"won": yesno("won", outcome_is_desirable=True)}
case = {"ids": ["deal_0001@demo"]}

alone = dg.ask(won, dataset_id="sample:deal_stages", entity_column="snapshot_id", subject_kind="org",
               group_column="deal_id", cases=case)
a = alone["answers"]["won"]
print("deal table alone:", a["state"], a.get("reasons"))   # refused: never retried

with_log = dg.ask(won, dataset_id="sample:deal_activity", entity_column="deal_id", subject_kind="org",
                  time_column="ts", cases=case,
                  shape=snapshots(dataset_id="sample:deal_stages", snapshot_id_column="snapshot_id",
                                  snapshot_time_column="entered_at", outcome_time_column="closed_at",
                                  event_column="activity", value_columns=["attendees"], lookback_days=[7, 30]))
b = with_log["answers"]["won"]
print("with the activity log:", b["state"])
if b["state"] == "answered":
    print("  chance:", b["cases"][0]["p"])
    print("  pattern:", [c["text"] for c in b["pattern"]])
    print("  held out:", b["quality"].get("held_out"))
