import assert from "node:assert/strict";
import { test } from "node:test";
import { Datagoat, DatagoatError, RETRY_SAFE, choice, register, yesno } from "../src/index.ts";

test("builders send only what is stated; choice demands polarity", () => {
  assert.deepEqual(yesno("y"), { type: "yesno", outcome_column: "y" });
  assert.throws(() => choice({ option_column: "c", options: ["a", "b"], outcome_column: "y" } as never), TypeError);
});

test("pending is followed; problems are raised with code and field", async () => {
  const seen: string[] = [];
  const script = [{ status: "pending", task_id: "tk_1", retry_after_ms: 0 }, { status: "done", answers: {} }];
  const f = (async (url: string) => { seen.push(new URL(url).pathname); return new Response(JSON.stringify(script.shift())); }) as unknown as typeof fetch;
  const dg = new Datagoat({ apiKey: "dgk_test_x", baseUrl: "http://x", fetch: f });
  const out = await dg.ask({ q: yesno("y") }, { data: { dataset_id: "sample:saas_churn" }, entity_column: "a", subject_kind: "org", cases: { ids: ["1"] } });
  assert.equal(out.status, "done");
  assert.deepEqual(seen, ["/v1/ask", "/v1/poll"]);
  const bad = (async () => new Response(JSON.stringify({ code: "unknown_case_id", detail: "d", remedy: "r", field: "cases.ids" }), { status: 422 })) as unknown as typeof fetch;
  await assert.rejects(new Datagoat({ apiKey: "k", baseUrl: "http://x", fetch: bad }).drift("mr1_x"), (e: DatagoatError) => e.problem.field === "cases.ids");
});

test("live: register, ask a sample, verify", { skip: !process.env.DG_E2E_URL }, async () => {
  const { api_key } = await register(process.env.DG_E2E_URL);
  const dg = new Datagoat({ apiKey: api_key, baseUrl: process.env.DG_E2E_URL });
  const out = await dg.ask({ churn: yesno("churned", { outcome_is_desirable: false }) }, { data: { dataset_id: "sample:saas_churn" }, entity_column: "customer_id", subject_kind: "org", cases: { ids: ["cust_0001"] } });
  assert.equal(out.answers!.churn.state, "answered");
  assert.equal(await dg.verifyAll(out), true);
});

test("a shape rides on the ask; deleteDataset calls its route", async () => {
  const bodies: Array<[string, any]> = [];
  const f = (async (url: string, init: RequestInit) => { bodies.push([new URL(url).pathname, JSON.parse(String(init.body))]); return new Response(JSON.stringify({ status: "done", answers: {}, dataset_id: "ds_1", deleted: true })); }) as unknown as typeof fetch;
  const dg = new Datagoat({ apiKey: "dgk_live_x", baseUrl: "http://x", fetch: f });
  await dg.ask({ q: yesno("maintenance_due") }, {
    data: { dataset_id: "sample:sensor_stream" }, entity_column: "asset_id", subject_kind: "object", time_column: "ts", cases: { ids: ["asset_001"] },
    shape: { kind: "signals", signal_columns: ["vibration"], windows: [1, 3, 7], snapshot_every: "1d", horizon: 3 },
  });
  assert.equal(bodies[0]![1].shape.kind, "signals");
  await dg.deleteDataset("ds_1");
  assert.deepEqual(bodies[1], ["/v1/delete-dataset", { dataset_id: "ds_1" }]);
});

test("shape builders send only what is stated", async () => {
  const { events, series, panel, signals, traces } = await import("../src/index.ts");
  assert.deepEqual(events({ label: { lapsed: true }, horizon_days: 90 }), { kind: "events", label: { lapsed: true }, horizon_days: 90 });
  assert.deepEqual(series({ value_columns: ["sales"], windows: [4, 12] }), { kind: "series", value_columns: ["sales"], windows: [4, 12] });
  assert.deepEqual(panel({ label: { trend_of: "usage" } }), { kind: "panel", label: { trend_of: "usage" } });
  assert.equal(signals({ signal_columns: ["v"], windows: [1, 3, 7], snapshot_every: "1d", horizon: 3 }).kind, "signals");
  assert.deepEqual(traces({ tool_column: "tool" }), { kind: "traces", tool_column: "tool" });
});

test("attest and evidence call their routes", async () => {
  const seen: string[] = [];
  const f = (async (url: string) => { seen.push(new URL(url).pathname); return new Response(JSON.stringify({ ok: true })); }) as unknown as typeof fetch;
  const dg = new Datagoat({ apiKey: "dgk_live_x", baseUrl: "http://x", fetch: f });
  await dg.attest({ model_ref: "mr1_x", entity_id: "a", lever_token: "hsct1.t", post_value: "two_year", acted_at: "2026-10-01" });
  await dg.evidence("mr1_x");
  await dg.trackRecord("mr1_x");
  await dg.profile({ namespace: "acme" });
  assert.deepEqual(seen, ["/v1/attest", "/v1/evidence", "/v1/track-record", "/v1/profile"]);
});

// -- real-world volume: retries, pages, exports, many cases --------------------------------------

type Step = { body: unknown; status?: number; headers?: Record<string, string> } | "drop";
function scripted(steps: Step[]) {
  const seen: Array<{ path: string; body: any; auth: string | null; method: string }> = [];
  const f = (async (url: string, init: RequestInit = {}) => {
    const h = new Headers(init.headers);
    seen.push({ path: new URL(url).pathname, body: init.body ? JSON.parse(String(init.body)) : null, auth: h.get("authorization"), method: init.method ?? "GET" });
    const s = steps.shift();
    if (s === undefined) throw new Error("script ran out");
    if (s === "drop") throw new TypeError("fetch failed");
    return new Response(typeof s.body === "string" ? s.body : JSON.stringify(s.body), { status: s.status ?? 200, headers: s.headers });
  }) as unknown as typeof fetch;
  const dg = new Datagoat({ apiKey: "dgk_live_x", baseUrl: "http://x", fetch: f });
  const waits: number[] = [];
  dg.sleep = async (ms) => { waits.push(ms); };
  return { dg, seen, waits };
}
const LIMITED: Step = { body: { code: "rate_limited", detail: "d", remedy: "r", retry_after_ms: 1500 }, status: 429, headers: { "retry-after": "2" } };
const BUSY: Step = { body: { code: "engine_error", detail: "busy", remedy: "retry" }, status: 503 };
const DONE = { status: "done", task_id: "tk_1", answers: { churn: { type: "yesno", state: "answered", cases: [] } } };
const askOpts = { data: { dataset_id: "ds" }, entity_column: "a", subject_kind: "org" as const, cases: { ids: ["1"] } };

test("a 429 is retried after the wait the API names, for any operation", async () => {
  const { dg, seen, waits } = scripted([LIMITED, { body: { dataset_id: "ds_1" } }]);
  assert.deepEqual(await dg.addDataset({ rows: [{ a: 1 }] }), { dataset_id: "ds_1" });
  assert.deepEqual(waits, [1500]);
  assert.equal(seen.length, 2);
});

test("a 503 is retried only where sending twice is safe", async () => {
  let t = scripted([BUSY, { body: DONE }]);
  await t.dg.ask({ q: yesno("y") }, { ...askOpts, idempotency_key: "k1" });
  assert.deepEqual(t.seen.map((s) => s.body.idempotency_key), ["k1", "k1"]);
  t = scripted([BUSY]);
  await assert.rejects(t.dg.addDataset({ dataset_id: "ds_1", rows: [{ a: 1 }] }), (e: DatagoatError) => e.problem.status === 503);
  assert.equal(t.seen.length, 1, "an append is never re-sent");
  t = scripted([BUSY]);
  await assert.rejects(t.dg.attest({ model_ref: "m", entity_id: "a", lever_token: "hsct1.t", post_value: 1, acted_at: "2026-10-01" }));
  assert.equal(t.seen.length, 1, "an attest without event_id is not retried");
  t = scripted([BUSY, { body: { written: true } }]);
  await t.dg.attest({ model_ref: "m", entity_id: "a", lever_token: "hsct1.t", post_value: 1, acted_at: "2026-10-01", event_id: "e1" });
  assert.equal(t.seen.length, 2);
});

test("retries are bounded; problems are never retried; a dropped connection is", async () => {
  let t = scripted([BUSY, BUSY, BUSY, BUSY]);
  await assert.rejects(t.dg.poll("tk_1"));
  assert.equal(t.seen.length, 3);
  assert.ok(t.waits.every((w) => w > 0 && w <= 30_000));
  t = scripted([{ body: { code: "unknown_case_id", detail: "d", remedy: "r" }, status: 422 }]);
  await assert.rejects(t.dg.poll("tk_1"));
  assert.equal(t.seen.length, 1);
  t = scripted(["drop", { body: { status: "valid" } }]);
  assert.equal(await t.dg.verify({ verdict_id: "v" }, { signature: "s" }), "valid");
  t = scripted(["drop", "drop", "drop"]);
  await assert.rejects(t.dg.describe(), (e: DatagoatError) => e.problem.code === "network_error");
});

test("a paged answer is joined whole, in order", async () => {
  const first = { status: "done", task_id: "tk_1", page: { answer_url: "u", expires_at: "t", next_cursor: "c2" }, answers: {
    churn: { type: "yesno", state: "answered", cases: [{ entity_id: "a" }, { entity_id: "b" }], page: { offset: 0, returned: 2, total: 3 }, verdicts: [{ verdict: {} }] },
    risk: { type: "score", state: "refused", reasons: ["no_finding_cleared"] } } };
  const p2 = { status: "done", page: { answer_url: "u", expires_at: "t" }, answers: { churn: { type: "yesno", state: "answered", cases: [{ entity_id: "c" }], page: { offset: 2, returned: 1, total: 3 } } } };
  const { dg, seen } = scripted([{ body: first }, { body: p2 }]);
  const out: any = await dg.ask({ churn: yesno("y"), risk: yesno("y") }, askOpts);
  assert.deepEqual(out.answers.churn.cases.map((c: any) => c.entity_id), ["a", "b", "c"]);
  assert.equal(out.page, undefined);
  assert.equal(out.answers.churn.page, undefined);
  assert.deepEqual(out.answers.risk, first.answers.risk);
  assert.deepEqual(seen.map((s) => s.path), ["/v1/ask", "/v1/page"]);
  assert.deepEqual(seen[1]!.body, { cursor: "c2" });
});

test("export is requested, and download sends no key", async () => {
  const { dg, seen } = scripted([{ body: { ...DONE, export: { format: "csv", results_url: "http://x/v1/exports/tok", expires_at: "t" } } }, { body: "question_id\nchurn\n" }]);
  const out: any = await dg.ask({ churn: yesno("y") }, { ...askOpts, export: "csv" });
  assert.equal(seen[0]!.body.export, "csv");
  const r = await dg.download(out.export.results_url);
  assert.equal(await r.text(), "question_id\nchurn\n");
  assert.equal(seen[1]!.auth, null);
  assert.equal(seen[1]!.method, "GET");
  await assert.rejects(dg.ask({ churn: yesno("y") }, { ...askOpts, export: "xlsx" as never }));
});

const chunk = (ids: string[], task: string, over: Record<string, unknown> = {}) => ({ status: "done", task_id: task, fits_run: task === "t0" ? 1 : 0, billable_decisions: ids.length,
  answers: { churn: { type: "yesno", state: "answered", model_ref: "mr1_a", cases: ids.map((entity_id) => ({ entity_id, p: 0.5 })), verdicts: [{ verdict: { ids } }], ...over } } });

test("askMany chunks resumably and joins in order", async () => {
  const ids = Array.from({ length: 7 }, (_, i) => `c${i}`);
  const { dg, seen } = scripted([{ body: chunk(ids.slice(0, 3), "t0") }, { body: chunk(ids.slice(3, 6), "t1") }, { body: chunk(ids.slice(6), "t2") }]);
  const progress: Array<[number, number]> = [];
  const out: any = await dg.askMany({ churn: yesno("y") }, { ...askOpts, cases: { ids }, chunkSize: 3, idempotency_key: "job", onChunk: (d, n) => progress.push([d, n]) });
  assert.deepEqual(seen.map((s) => s.body.cases.ids), [ids.slice(0, 3), ids.slice(3, 6), ids.slice(6)]);
  assert.deepEqual(seen.map((s) => s.body.idempotency_key), ["job:0", "job:1", "job:2"]);
  assert.deepEqual(out.answers.churn.cases.map((c: any) => c.entity_id), ids);
  assert.equal(out.answers.churn.verdicts.length, 3);
  assert.deepEqual(out.task_ids, ["t0", "t1", "t2"]);
  assert.equal(out.fits_run, 1);
  assert.equal(out.billable_decisions, 7);
  assert.deepEqual(progress, [[1, 3], [2, 3], [3, 3]]);
});

test("askMany stops on a refusal, rejects rank, and never joins chunks that disagree", async () => {
  let t = scripted([{ body: chunk(["a"], "t0", { state: "refused", cases: undefined }) }]);
  const out: any = await t.dg.askMany({ churn: yesno("y") }, { ...askOpts, cases: { ids: ["a", "b"] }, chunkSize: 1 });
  assert.equal(t.seen.length, 1);
  assert.equal(out.answers.churn.state, "refused");
  const { rank } = await import("../src/index.ts");
  await assert.rejects(t.dg.askMany({ r: rank("y") }, { ...askOpts }), /whole record/);
  await assert.rejects(t.dg.askMany({ q: yesno("y") }, { ...askOpts, cases: { ids: [] } }), /no cases/);
  t = scripted([{ body: chunk(["a"], "t0") }, { body: chunk(["b"], "t1", { model_ref: "mr1_b" }) }]);
  const mixed: any = await t.dg.askMany({ churn: yesno("y") }, { ...askOpts, cases: { ids: ["a", "b"] }, chunkSize: 1 });
  assert.equal(mixed.answers.churn, undefined);
  assert.deepEqual(mixed.unjoined.churn.map((p: any) => p.model_ref), ["mr1_a", "mr1_b"]);
});

test("a cut-off 200 is not an answer, and is retried where safe", async () => {
  let t = scripted([{ body: '{"status": "do' }, { body: DONE }]);
  assert.equal((await t.dg.poll("tk_1")).status, "done");
  assert.equal(t.seen.length, 2);
  t = scripted([{ body: '{"dataset_id": "ds' }]);
  await assert.rejects(t.dg.addDataset({ dataset_id: "ds_1", rows: [{ a: 1 }] }), (e: DatagoatError) => e.problem.code === "incomplete_response");
  assert.equal(t.seen.length, 1);
});

test("a body over the request limit fails before sending", async () => {
  const t = scripted([]);
  await assert.rejects(t.dg.ask({ q: yesno("y") }, { ...askOpts, data: { csv: "a,b\n" + "1,2\n".repeat(1_200_000) } }), (e: DatagoatError) => e.problem.code === "request_too_large");
  assert.equal(t.seen.length, 0);
});

test("askMany refuses wait: false; a joined answer has no next cursor", async () => {
  const t = scripted([
    { body: { status: "done", task_id: "t", page: { answer_url: "u", expires_at: "e", next_cursor: "c1" }, answers: { q: { type: "yesno", state: "answered", cases: [{ entity_id: "a" }], verdicts_withheld: { count: 1 } } } } },
    { body: { status: "done", page: { answer_url: "u", expires_at: "e" }, answers: { q: { type: "yesno", state: "answered", cases: [{ entity_id: "b" }] } } } },
  ]);
  await assert.rejects(t.dg.askMany({ q: yesno("y") }, { ...askOpts, wait: false }), /every chunk/);
  const out: any = await t.dg.poll("t");
  assert.deepEqual(out.answers.q.cases.map((c: any) => c.entity_id), ["a", "b"]);
  assert.deepEqual(out.page, { answer_url: "u", expires_at: "e" });
});

test("compact (spec v3 S10): asked, polled and paged compact; poll and page take the format per call", async () => {
  const t = scripted([
    { body: { status: "pending", task_id: "tk_9", retry_after_ms: 0 } },
    { body: { status: "done", task_id: "tk_9", page: { answer_url: "u", expires_at: "e", next_cursor: "c1" }, answers: { q: { type: "yesno", state: "answered", cases: [{ entity_id: "a", p: 0.4, p_display: "40%" }], verdicts_withheld: { count: 1, reason: "response_format_compact" } } } } },
    { body: { status: "done", page: { answer_url: "u", expires_at: "e" }, answers: { q: { type: "yesno", state: "answered", cases: [{ entity_id: "b", p: 0.1, p_display: "10%" }] } } } },
  ]);
  const out: any = await t.dg.ask({ q: yesno("y") }, { ...askOpts, response_format: "compact" });
  assert.deepEqual(out.answers.q.cases.map((c: any) => c.entity_id), ["a", "b"]);
  assert.deepEqual(t.seen.map((s) => s.path), ["/v1/ask", "/v1/poll", "/v1/page"]);
  assert.equal(t.seen[0]!.body.response_format, "compact");
  assert.deepEqual(t.seen[1]!.body, { task_id: "tk_9", response_format: "compact" });
  assert.deepEqual(t.seen[2]!.body, { cursor: "c1", response_format: "compact" });
  assert.deepEqual(out.page, { answer_url: "u", expires_at: "e" }, "the link to the whole answer stays");
  // The default sends no format; a format poll and page do not take is refused before any call.
  const d = scripted([{ body: { status: "done", answers: {} } }]);
  await d.dg.page("c1");
  assert.deepEqual(d.seen[0]!.body, { cursor: "c1" });
  await assert.rejects(d.dg.page("c1", "concise" as never), /full" or "compact/);
  await assert.rejects(d.dg.poll("tk_9", "short" as never), /full" or "compact/);
  assert.equal(d.seen.length, 1);
});

test("building a product: fromModel with no data, namespaces, lifetimes, snapshots, suggest, models", async () => {
  const { fromModel, snapshots } = await import("../src/index.ts");
  const ref = "mr1_" + "a".repeat(32);
  const bodies: Array<[string, any]> = [];
  const f = (async (url: string, init: RequestInit) => { bodies.push([new URL(url).pathname, JSON.parse(String(init.body))]); return new Response(JSON.stringify({ status: "done", answers: {}, model_ref: ref, model_expires_at: "2027-01-01T00:00:00Z", deleted: true, candidates: [] })); }) as unknown as typeof fetch;
  const dg = new Datagoat({ apiKey: "dgk_live_x", baseUrl: "http://x", fetch: f });
  await dg.ask({ churn: fromModel(ref, { type: "score", levels: ["low", "high"], cuts: [0.5] }) }, { entity_column: "customer_id", subject_kind: "org", cases: { rows: [{ customer_id: "n1" }] }, namespace: "acme" });
  assert.equal("data" in bodies[0]![1], false);
  assert.deepEqual(bodies[0]![1].questions.churn, { type: "score", model_ref: ref, levels: ["low", "high"], cuts: [0.5] });
  assert.equal(bodies[0]![1].namespace, "acme");
  await dg.ask({ won: yesno("won") }, {
    data: { dataset_id: "ds_1" }, entity_column: "deal_id", subject_kind: "org", time_column: "ts", model_ttl_days: 365, cases: { ids: ["d1@demo"] },
    shape: snapshots({ snapshots: { dataset_id: "ds_2" }, snapshot_id_column: "snap", snapshot_time_column: "at", lookback_days: [7, 30] }),
  });
  assert.deepEqual(bodies[1]![1].shape, { kind: "snapshots", snapshots: { dataset_id: "ds_2" }, snapshot_id_column: "snap", snapshot_time_column: "at", lookback_days: [7, 30] });
  // P2: the form without a snapshot table, asked about today's open cases.
  const grid = snapshots({ snapshot_every: "4w", horizon: { value: 90, unit: "days" }, label: { lapsed: true }, event_column: "event", lookbacks_days: [7, 30] });
  assert.deepEqual(grid, { kind: "snapshots", snapshot_every: "4w", horizon: { value: 90, unit: "days" }, label: { lapsed: true }, event_column: "event", lookbacks_days: [7, 30] });
  assert.deepEqual(snapshots({ snapshot_every: "1w", horizon: { value: 30, unit: "days" }, outcome_time_column: "cancelled_at", terminal: true }),
    { kind: "snapshots", snapshot_every: "1w", horizon: { value: 30, unit: "days" }, outcome_time_column: "cancelled_at", terminal: true });
  assert.throws(() => snapshots({ snapshot_every: "4w", horizon: { value: 90, unit: "days" } }), /label/);
  assert.throws(() => snapshots({ snapshot_every: "4w", horizon: { value: 90, unit: "days" }, label: { lapsed: true }, outcome_time_column: "x" }), /not both/);
  await dg.ask({ quiet: yesno("lapsed_90d") }, { data: { dataset_id: "ds_1" }, entity_column: "account_id", subject_kind: "org", time_column: "ts", shape: grid, cases: { open: true } });
  const sentGrid = bodies.splice(2, 1)[0]![1];
  assert.deepEqual(sentGrid.cases, { open: true });
  assert.equal(sentGrid.shape.snapshot_every, "4w");
  assert.equal(bodies[1]![1].model_ttl_days, 365);
  await dg.suggest({ data: { dataset_id: "sample:saas_churn" }, entity_column: "customer_id" });
  await dg.extendModel(ref, 200);
  await dg.deleteModel(ref, { namespace: "acme" });
  await dg.drift(ref);
  assert.deepEqual(bodies.slice(2), [
    ["/v1/suggest", { data: { dataset_id: "sample:saas_churn" }, entity_column: "customer_id" }],
    ["/v1/extend-model", { model_ref: ref, days: 200 }],
    ["/v1/delete-model", { model_ref: ref, namespace: "acme" }],
    ["/v1/drift", { model_ref: ref }],
  ]);
  assert.throws(() => fromModel(ref, { type: "choice" as never }));
});

// Offline verification against a real production Verdict (python/tests/fixtures/verdict.json).
import { readFileSync } from "node:fs";
import { canonical, pyNumber, verifyOffline } from "../src/verify.ts";
const FX = JSON.parse(readFileSync(new URL("../../python/tests/fixtures/verdict.json", import.meta.url), "utf8"));
const BEFORE_EXPIRY = new Date("2026-10-01T00:00:00Z");

test("offline: a real Verdict is valid; one changed number or reason is not", async () => {
  assert.equal(await verifyOffline(FX.verdict, FX.signature, FX.jwks, BEFORE_EXPIRY), "valid");
  const n = structuredClone(FX.verdict); n.entities[0].score = 0.95;
  assert.equal(await verifyOffline(n, FX.signature, FX.jwks, BEFORE_EXPIRY), "invalid_signature");
  const r = structuredClone(FX.verdict); r.entities[0].drivers[0].likelihood_direction = "lower";
  assert.equal(await verifyOffline(r, FX.signature, FX.jwks, BEFORE_EXPIRY), "invalid_signature");
});

test("offline: expired, unknown key, missing signature", async () => {
  assert.equal(await verifyOffline(FX.verdict, FX.signature, FX.jwks, new Date("2027-01-01T00:00:00Z")), "expired");
  assert.equal(await verifyOffline(FX.verdict, FX.signature, { keys: [] }, BEFORE_EXPIRY), "unknown_key");
  assert.equal(await verifyOffline(FX.verdict, null, FX.jwks), "invalid_signature");
});

test("offline: numbers and text are written the way Python's json.dumps writes them", () => {
  // Expected strings produced by Python 3: json.dumps(x, sort_keys=True, separators=(",", ":")).
  const cases: [unknown, string][] = [
    [0.7001631935516078, "0.7001631935516078"], [12000, "12000"], [1e-5, "1e-05"], [0.0001, "0.0001"],
    [1.5e-7, "1.5e-07"], [1e16, "1e+16"], [2.5e21, "2.5e+21"], [-3.25, "-3.25"], [123456789012345.6, "123456789012345.6"],
  ];
  for (const [x, want] of cases) assert.equal(pyNumber(x as number), want);
  assert.equal(canonical({ b: "é", a: [true, null] }), '{"a":[true,null],"b":"\\u00e9"}');
  assert.equal(canonical({ b: "é" }, false), '{"b":"é"}');
});

test("map: the public contract on the wire; a confirm is never re-sent on a 5xx; mapping_expired carries manifest; mapping_answers_incomplete names its slots in detail", async () => {
  const bodies: Array<[string, any]> = [];
  const replies: Array<[unknown, number]> = [
    [{ status: "proposed", questions: [{ slot: "outcome", options: ["lapsed", "event_type:e:event=cancelled"], why: "two" }] }, 200],
    [{ code: "engine_error", detail: "busy", remedy: "retry" }, 503],
    [{ status: 410, code: "mapping_expired", detail: "gone", remedy: "map again", field: "mapping_id", manifest: { entity: "account_id" } }, 410],
    [{ status: 422, code: "mapping_answers_incomplete", detail: "Some questions have no answer (outcome).", remedy: "answer", field: "answers.outcome" }, 422],
  ];
  const f = (async (url: string, init: RequestInit) => {
    bodies.push([new URL(url).pathname, JSON.parse(String(init.body))]);
    const [body, status] = replies.shift()!;
    return new Response(JSON.stringify(body), { status });
  }) as unknown as typeof fetch;
  const dg = new Datagoat({ apiKey: "dgk_live_x", baseUrl: "http://x", fetch: f });
  const out = await dg.map({ sources: ["sample:parley_events", { fetch_url: "https://x.example/b.csv", label: "billing" }], outcome_words: ["cancel"], horizon: 90, snapshot_every: "4w" });
  assert.equal((out.questions as any[])[0].slot, "outcome");
  assert.deepEqual(bodies[0], ["/v1/map", {
    sources: [{ dataset_id: "sample:parley_events" }, { fetch_url: "https://x.example/b.csv", label: "billing" }],
    horizon: { value: 90, unit: "days" }, outcome_words: ["cancel"], snapshot_every: "4w",
  }]);
  await assert.rejects(dg.map({ mapping_id: "mp_" + "a".repeat(25), closed_since: "2025-10-01" }), (e: DatagoatError) => e.problem.status === 503);
  assert.equal(bodies.length, 2, "a confirm stores a mapping: it is not re-sent");
  await assert.rejects(dg.map({ mapping_id: "mp_" + "b".repeat(25) }), (e: DatagoatError) => (e.problem.manifest as any)?.entity === "account_id");
  await assert.rejects(dg.map({ sources: ["ds_x"], answers: {} }), (e: DatagoatError) => e.problem.detail.includes("(outcome)") && e.problem.field === "answers.outcome" && !("slots" in e.problem));
  assert.throws(() => dg.map({}), /sources/);
});

test("preflight carries a @deprecated JSDoc tag naming map and contract 2.0.0 (spec v3 S2), and is still served", async () => {
  const { readFileSync } = await import("node:fs");
  const src = readFileSync(new URL("../src/index.ts", import.meta.url), "utf8");
  const doc = /\/\*\*((?:(?!\*\/)[^])*)\*\/\s*preflight\(/.exec(src);
  assert.ok(doc, "a JSDoc block sits on preflight");
  const tag = doc![1]!.slice(doc![1]!.indexOf("@deprecated"));
  assert.match(tag, /^@deprecated/);
  assert.match(tag, /\bmap\(/, "names map, the replacement");
  assert.match(tag, /2\.0\.0/, "and the removal version");
  const seen: string[] = [];
  const f = (async (url: string) => { seen.push(new URL(url).pathname); return new Response(JSON.stringify({ dataset_id: "ds_x" })); }) as unknown as typeof fetch;
  await new Datagoat({ apiKey: "k", baseUrl: "http://x", fetch: f }).preflight("ds_x");
  assert.deepEqual(seen, ["/v1/preflight"]);
});

test("backtest: data.dataset_id and cutoffs on the wire, an idempotency key always sent; a pending walk is polled to its result; retried on a 5xx with the same key", async () => {
  const bodies: Array<[string, any]> = [];
  const done = { status: "done", task_id: "tk_" + "a".repeat(24), backtest_id: "bt1_x", decision: "supported", cutoffs: [], summary: {}, verdict: { verdict_id: "v", kind: "backtest" }, signature: { kid: "k" }, fits_run: 6 };
  const replies: Array<[unknown, number]> = [
    [{ code: "engine_error", detail: "busy", remedy: "retry" }, 503],
    [{ status: "pending", task_id: "tk_" + "a".repeat(24), retry_after_ms: 1000 }, 202],
    [done, 200],
  ];
  const f = (async (url: string, init: RequestInit) => {
    bodies.push([new URL(url).pathname, JSON.parse(String(init.body))]);
    const [body, status] = replies.shift()!;
    return new Response(JSON.stringify(body), { status });
  }) as unknown as typeof fetch;
  const dg = new Datagoat({ apiKey: "dgk_live_x", baseUrl: "http://x", fetch: f });
  dg.sleep = async () => {};
  const out = await dg.backtest("sample:parley_record", { subject_kind: "org", every: "4w", last: 6, baseline: "none", idempotency_key: "bt-1" });
  assert.equal(out.decision, "supported");
  assert.deepEqual(bodies.map((b) => b[0]), ["/v1/backtest", "/v1/backtest", "/v1/poll"]);
  assert.deepEqual(bodies[0]![1], { data: { dataset_id: "sample:parley_record" }, cutoffs: { every: "4w", last: 6 }, baseline: "none", subject_kind: "org", idempotency_key: "bt-1" });
  assert.deepEqual(bodies[1]![1], bodies[0]![1], "retried with the same idempotency key: the task is not started twice");
  assert.ok(RETRY_SAFE.has("backtest"));
  bodies.length = 0;
  replies.push([done, 200]);
  await dg.backtest("ds_" + "b".repeat(25), { subject_kind: "person", acknowledge_decision_support: true });
  assert.equal(typeof bodies[0]![1].idempotency_key, "string");
  assert.equal("cutoffs" in bodies[0]![1], false, "no cutoffs stated: the record's own grid");
  assert.equal(bodies[0]![1].acknowledge_decision_support, true);
});

test("schedule / deleteSchedule: the public contract on the wire; neither is retried on a 5xx (spec v3 §2 Bookkeeping)", async () => {
  const bodies: Array<[string, any]> = [];
  const replies: Array<[unknown, number]> = [
    [{ schedule: { schedule_id: "sc_" + "a".repeat(25), state: "active", next_run_at: "2026-10-01T00:00:00Z" }, watch_url: "https://datagoat.io/watch/x" }, 200],
    [{ code: "engine_error", detail: "busy", remedy: "retry" }, 503],
    [{ code: "engine_error", detail: "busy", remedy: "retry" }, 503],
  ];
  const f = (async (url: string, init: RequestInit) => {
    bodies.push([new URL(url).pathname, JSON.parse(String(init.body))]);
    const [body, status] = replies.shift()!;
    return new Response(JSON.stringify(body), { status });
  }) as unknown as typeof fetch;
  const dg = new Datagoat({ apiKey: "dgk_live_x", baseUrl: "http://x", fetch: f });
  dg.sleep = async () => {};
  const out = await dg.schedule("mp_" + "a".repeat(25), { cadence: "monthly", subject_kind: "org", namespace: "acme" });
  assert.equal(out.schedule.state, "active");
  assert.deepEqual(bodies[0], ["/v1/schedule", { mapping_id: "mp_" + "a".repeat(25), cadence: "monthly", subject_kind: "org", namespace: "acme" }]);
  await assert.rejects(dg.schedule("mp_" + "a".repeat(25), { cadence: "daily", subject_kind: "org" }), (e: DatagoatError) => e.problem.status === 503);
  await assert.rejects(dg.deleteSchedule("sc_x"), (e: DatagoatError) => e.problem.status === 503);
  assert.deepEqual(bodies.map((b) => b[0]), ["/v1/schedule", "/v1/schedule", "/v1/delete-schedule"], "each sent once");
  assert.equal(RETRY_SAFE.has("schedule"), false);
  assert.equal(RETRY_SAFE.has("delete-schedule"), false);
});
