import assert from "node:assert/strict";
import { test } from "node:test";
import { Datagoat, DatagoatError, ModelUnavailableError, NotFoundError, PaymentRequiredError, RateLimitError, ValidationError, yesno } from "../src/index.ts";

const problem = (code: string, status: number, extra: Record<string, unknown> = {}) =>
  new Response(JSON.stringify({ type: `https://datagoat.io/problems/${code}`, title: code, status, detail: "d", code, remedy: "r", doc_url: `https://datagoat.io/docs/errors#${code}`, ...extra }), { status });

test("problems are typed by family under DatagoatError; a refusal is an answer, never an exception", async () => {
  const cases: Array<[string, number, new (...a: never[]) => DatagoatError, Record<string, unknown>?]> = [
    ["invalid_request", 422, ValidationError, { field: "questions.q.cuts" }],
    ["model_deleted", 410, ModelUnavailableError],
    ["model_expired", 410, ModelUnavailableError],
    ["model_ref_missing", 404, ModelUnavailableError],
    ["unknown_dataset", 404, NotFoundError],
    ["payment_required", 402, PaymentRequiredError],
    ["rate_limited", 429, RateLimitError, { retry_after_ms: 0 }],
  ];
  for (const [code, status, cls, extra] of cases) {
    const f = (async () => problem(code, status, extra)) as unknown as typeof fetch;
    const dg = new Datagoat({ apiKey: "dgk_live_x", baseUrl: "http://x", fetch: f, maxRetries: 0 });
    await assert.rejects(dg.drift("mr1_x"), (e: unknown) => {
      assert.ok(e instanceof cls, `${code} is a ${cls.name}`);
      assert.ok(e instanceof DatagoatError);
      assert.equal((e as DatagoatError).code, code);
      assert.equal((e as DatagoatError).problem.doc_url, `https://datagoat.io/docs/errors#${code}`);
      return true;
    });
  }
  const v = (async () => problem("invalid_request", 422, { field: "questions.q.cuts" })) as unknown as typeof fetch;
  await assert.rejects(new Datagoat({ apiKey: "k", baseUrl: "http://x", fetch: v }).drift("mr1_x"), (e: ValidationError) => e.field === "questions.q.cuts");
  const gone = (async () => problem("model_expired", 410)) as unknown as typeof fetch;
  await assert.rejects(new Datagoat({ apiKey: "k", baseUrl: "http://x", fetch: gone }).drift("mr1_x"), (e: NotFoundError) => e.gone === true);
  const refused = (async () => new Response(JSON.stringify({ status: "done", answers: { q: { type: "yesno", state: "refused", reasons: ["too_few_predictors"], needs: { columns: 2 }, have: { columns: 2 } } } }))) as unknown as typeof fetch;
  const out = await new Datagoat({ apiKey: "k", baseUrl: "http://x", fetch: refused }).ask({ q: yesno("y") }, { data: { dataset_id: "ds_x" }, entity_column: "a", subject_kind: "org", cases: { ids: ["1"] } });
  assert.equal(out.answers!.q.state, "refused");
});

test("reportOutcomes sends chunks and surfaces every bad row by its index in the whole list; partial collects results", async () => {
  const rows = Array.from({ length: 5 }, (_, i) => ({ entity_id: `c${i}`, outcome: 1, observed_at: "2026-10-01" }));
  const bodies: any[] = [];
  const script = [
    new Response(JSON.stringify({ written: 2, duplicates: 0 })),
    problem("invalid_outcomes", 422, { field: "outcomes", errors: [{ index: 1, field: "outcome", detail: "not yes/no" }] }),
  ];
  const f = (async (_u: string, init: RequestInit) => { bodies.push(JSON.parse(String(init.body))); return script.shift()!; }) as unknown as typeof fetch;
  await assert.rejects(new Datagoat({ apiKey: "k", baseUrl: "http://x", fetch: f }).reportOutcomes("mr1_x", rows, { chunkSize: 2 }), (e: ValidationError) => {
    assert.ok(e instanceof ValidationError);
    assert.deepEqual(e.errors, [{ index: 3, field: "outcome", detail: "not yes/no" }], "index in the whole list");
    assert.match(e.problem.detail, /rows 0 to 1 were already sent and written: 2 written/);
    assert.equal(e.writtenBefore, 2);
    return true;
  });
  assert.deepEqual(bodies.map((b) => b.outcomes.length), [2, 2]);
  assert.ok(bodies.every((b) => !("partial" in b)), "partial only when stated");
  const part = (n: number) => new Response(JSON.stringify({ written: n, duplicates: 0, results: Array.from({ length: n }, (_, i) => ({ index: i, status: "written" })) }));
  const s2 = [part(2), part(2), part(1)];
  const seen: any[] = [];
  const g = (async (_u: string, init: RequestInit) => { seen.push(JSON.parse(String(init.body))); return s2.shift()!; }) as unknown as typeof fetch;
  const out = await new Datagoat({ apiKey: "k", baseUrl: "http://x", fetch: g }).reportOutcomes("mr1_x", rows, { chunkSize: 2, partial: true });
  assert.equal(out.written, 5);
  assert.deepEqual(out.results!.map((r) => r.index), [0, 1, 2, 3, 4]);
  assert.ok(seen.every((b) => b.partial === true));
});

test("the new fields are sent only when stated", async () => {
  const bodies: Array<[string, any]> = [];
  const f = (async (url: string, init: RequestInit) => { bodies.push([new URL(url).pathname, JSON.parse(String(init.body))]); return new Response(JSON.stringify({ status: "done", answers: {} })); }) as unknown as typeof fetch;
  const dg = new Datagoat({ apiKey: "dgk_live_x", baseUrl: "http://x", fetch: f });
  await dg.ask({ q: yesno("y") }, { data: { dataset_id: "ds_x" }, entity_column: "a", subject_kind: "org", cases: { ids: ["1"] }, response_format: "concise" });
  assert.equal(bodies[0]![1].response_format, "concise");
  await dg.preflight("ds_x", { entity_column: "customer_id" });
  assert.deepEqual(bodies[1], ["/v1/preflight", { dataset_id: "ds_x", entity_column: "customer_id" }]);
  await dg.profile({ namespace: "acme", fixed: ["tenure_months"] });
  assert.deepEqual(bodies[2], ["/v1/profile", { namespace: "acme", fixed: ["tenure_months"] }]);
});

test("reportOutcomes: up to 10,000 rows is one atomic call; any later chunk failure names what was written", async () => {
  const rows = Array.from({ length: 5 }, (_, i) => ({ entity_id: `c${i}`, outcome: 1, observed_at: "2026-10-01" }));
  const sizes: number[] = [];
  const one = (async (_u: string, init: RequestInit) => { sizes.push(JSON.parse(String(init.body)).outcomes.length); return new Response(JSON.stringify({ written: 5, duplicates: 0 })); }) as unknown as typeof fetch;
  await new Datagoat({ apiKey: "k", baseUrl: "http://x", fetch: one }).reportOutcomes("mr1_x", rows);
  assert.deepEqual(sizes, [5]);
  const failures: Array<[() => Response, new (...a: never[]) => DatagoatError]> = [
    [() => problem("engine_unavailable", 503), DatagoatError],
    [() => problem("rate_limited", 429, { retry_after_ms: 0 }), RateLimitError],
  ];
  for (const [fail, cls] of failures) {
    const script = [new Response(JSON.stringify({ written: 3, duplicates: 1 })), fail()];
    const f = (async () => script.shift()!) as unknown as typeof fetch;
    await assert.rejects(new Datagoat({ apiKey: "k", baseUrl: "http://x", fetch: f, maxRetries: 0 }).reportOutcomes("mr1_x", rows, { chunkSize: 4 }), (e: DatagoatError) => {
      assert.ok(e instanceof cls);
      assert.equal(e.writtenBefore, 4);
      assert.match(e.problem.detail, /rows 0 to 3 were already sent and written: 3 written, 1 duplicate; rows 4 onward were not/);
      return true;
    });
  }
  // A dropped connection on a later chunk says the same.
  let n = 0;
  const net = (async () => { if (n++ === 0) return new Response(JSON.stringify({ written: 4, duplicates: 0 })); throw new TypeError("fetch failed"); }) as unknown as typeof fetch;
  await assert.rejects(new Datagoat({ apiKey: "k", baseUrl: "http://x", fetch: net, maxRetries: 0 }).reportOutcomes("mr1_x", rows, { chunkSize: 4 }),
    (e: DatagoatError) => e.code === "network_error" && e.writtenBefore === 4);
});
