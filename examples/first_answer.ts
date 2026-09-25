// An answer with its reasons, checked on your own machine. Runs free on the samples.
//   npm install @datagoat/sdk && DATAGOAT_API_KEY=dgk_test_… npx tsx first_answer.ts
import { Datagoat, fetchKeys, verifyOffline, yesno } from "@datagoat/sdk";

const dg = new Datagoat({ apiKey: process.env.DATAGOAT_API_KEY! });
const out = await dg.ask({ churn: yesno("churned", { outcome_is_desirable: false }) }, {
  data: { dataset_id: "sample:saas_churn" }, entity_column: "customer_id", subject_kind: "org", cases: { ids: ["cust_0001"] },
});
const churn = out.answers!.churn;
console.log("state:", churn.state);
if (churn.state === "answered") {
  const c = churn.cases![0]!;
  console.log("chance:", c.p);
  for (const r of c.reasons) console.log(`  ${r.range.text}: ${r.strength}, ${r.likelihood_direction} chance`);
  const keys = await fetchKeys();
  for (const v of churn.verdicts ?? []) console.log("verdict:", await verifyOffline(v.verdict, v.signature, keys));
}
