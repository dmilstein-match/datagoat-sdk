/** Watching a pending ask: the task's event stream (WATCH.md), its resume by Last-Event-ID, and the
 *  fall back to polling. A local Node server scripts GET /v1/tasks/{id}/events as text/event-stream. */
import assert from "node:assert/strict";
import { createServer, type Server } from "node:http";
import type { AddressInfo } from "node:net";
import { test } from "node:test";
import { Datagoat, DatagoatError, yesno } from "../src/index.ts";

const PENDING = { status: "pending", task_id: "tk_w", retry_after_ms: 0 };
const DONE = { status: "done", task_id: "tk_w", answers: { churn: { type: "yesno", state: "answered", verdicts: [] } } };
const stage = (id: number, name: string, message: string, elapsed_ms: number, extra: Record<string, unknown> = {}) =>
  `id: ${id}\nevent: stage\ndata: ${JSON.stringify({ task_id: "tk_w", stage: name, facts: {}, message, elapsed_ms, ...extra })}\n\n`;
const S1 = stage(1, "reading_data", "Reading your record", 10);
const S2 = stage(2, "profiling", "Profiling 1200 rows · outcome: churned", 900, { frac: 0.05, facts: { rows: 1200, outcome: "churned" } });
const S3 = stage(3, "splitting", "Holding out 240 rows the search never sees", 1500, { frac: 0.3 });
const ANSWER = 'id: 4\nevent: answer\ndata: {"task_id":"tk_w","question":"churn","state":"answered","decision":"act"}\n\n';
const DONE_EV = 'id: 5\nevent: done\ndata: {"task_id":"tk_w","status":"done"}\n\n';

/** Each GET of the events route plays the next script: chunks, then "drop" (close mid-stream) or
 *  "hold" (keep it open with heartbeats); a number is a plain error status. */
type Conn = Array<string> | number;
async function fake(posts: unknown[], conns: Conn[]) {
  const seen: Array<{ method: string; path: string; lastId?: string; auth?: string; accept?: string }> = [];
  const timers = new Set<NodeJS.Timeout>();
  const srv: Server = createServer((req, res) => {
    seen.push({ method: req.method!, path: req.url!, lastId: req.headers["last-event-id"] as string | undefined, auth: req.headers.authorization, accept: req.headers.accept });
    if (req.method === "POST") {
      req.resume();
      req.on("end", () => { res.writeHead(200, { "content-type": "application/json" }); res.end(JSON.stringify(posts.shift())); });
      return;
    }
    const c = conns.shift() ?? 404;
    if (typeof c === "number") { res.writeHead(c, { "content-type": "application/problem+json" }); res.end(JSON.stringify({ code: "not_found", detail: "no such route", remedy: "poll" })); return; }
    res.writeHead(200, { "content-type": "text/event-stream", "cache-control": "no-store" });
    for (const chunk of c) {
      // Let what was written reach the client first; a reset before that is a failed open, not a drop.
      if (chunk === "drop") { setTimeout(() => res.destroy(), 50); return; }
      if (chunk === "silent") { res.flushHeaders(); return; } // headers, then nothing (a buffering proxy)
      if (chunk === "hold") {
        const t = setInterval(() => res.write(": keep-alive\n\n"), 50);
        timers.add(t);
        res.on("close", () => clearInterval(t));
        return;
      }
      res.write(chunk);
    }
    res.end();
  });
  await new Promise<void>((r) => srv.listen(0, "127.0.0.1", r));
  const url = `http://127.0.0.1:${(srv.address() as AddressInfo).port}`;
  const close = () => { for (const t of timers) clearInterval(t); srv.closeAllConnections(); return new Promise<void>((r) => srv.close(() => r())); };
  return { url, seen, close, paths: () => seen.map((s) => `${s.method} ${s.path}`) };
}
const client = (url: string) => { const dg = new Datagoat({ apiKey: "dgk_test_w", baseUrl: url }); dg.sleep = async () => {}; return dg; };
const askOpts = { data: { dataset_id: "sample:saas_churn" }, entity_column: "customer_id", subject_kind: "org" as const, cases: { ids: ["c1"] } };

test("events() iterates the stream, skips heartbeats, joins data lines and stops at done", async () => {
  const split = 'id: 3\nevent: stage\ndata: {"task_id":"tk_w","stage":"splitting",\ndata:  "message":"m3","elapsed_ms":3}\r\n\r\n';
  const f = await fake([], [[": hello\n\n", S1, ": keep-alive\n\n", S2, split, ANSWER, DONE_EV, "hold"]]);
  const got = [];
  try { for await (const ev of client(f.url).events("tk_w")) got.push(ev); } finally { await f.close(); }
  assert.deepEqual(got.map((e) => [e.event, e.id]), [["stage", 1], ["stage", 2], ["stage", 3], ["answer", 4], ["done", 5]]);
  assert.equal((got[1]!.data as any).message, "Profiling 1200 rows · outcome: churned");
  assert.deepEqual(got[2]!.data, { task_id: "tk_w", stage: "splitting", message: "m3", elapsed_ms: 3 });
  assert.equal(f.seen.length, 1);
  assert.equal(f.seen[0]!.path, "/v1/tasks/tk_w/events");
  assert.equal(f.seen[0]!.auth, "Bearer dgk_test_w");
  assert.equal(f.seen[0]!.accept, "text/event-stream");
  assert.equal(f.seen[0]!.lastId, undefined);
});

test("a dropped stream resumes with Last-Event-ID", async () => {
  const f = await fake([], [[S1, S2, "drop"], [S3, DONE_EV]]);
  const ids = [];
  try { for await (const ev of client(f.url).events("tk_w")) ids.push(ev.id); } finally { await f.close(); }
  assert.deepEqual(ids, [1, 2, 3, 5]);
  assert.deepEqual(f.seen.map((s) => s.lastId), [undefined, "2"]);
});

test("events() throws the problem when the server has no stream", async () => {
  const f = await fake([], [404]);
  try {
    await assert.rejects((async () => { for await (const _ of client(f.url).events("tk_w")) { /* none */ } })(), (e: DatagoatError) => e.problem.status === 404);
  } finally { await f.close(); }
});

test("ask({ onProgress }) gets each stage event verbatim, then polls the answer", async () => {
  const f = await fake([PENDING, DONE], [[S1, S2, S3, ANSWER, DONE_EV, "hold"]]);
  const got: any[] = [];
  let out;
  try { out = await client(f.url).ask({ churn: yesno("churned") }, { ...askOpts, onProgress: (e) => got.push(e) }); } finally { await f.close(); }
  assert.deepEqual(out, DONE);
  assert.deepEqual(got.map((g) => g.message), ["Reading your record", "Profiling 1200 rows · outcome: churned", "Holding out 240 rows the search never sees"]);
  assert.deepEqual(got[1].facts, { rows: 1200, outcome: "churned" });
  assert.deepEqual(f.paths(), ["POST /v1/ask", "GET /v1/tasks/tk_w/events", "POST /v1/poll"]);
});

test("ask resumes a dropped stream without polling in between", async () => {
  const f = await fake([PENDING, DONE], [[S1, "drop"], [S2, S3, DONE_EV]]);
  const got: any[] = [];
  try { assert.deepEqual(await client(f.url).ask({ churn: yesno("churned") }, { ...askOpts, onProgress: (e) => got.push(e) }), DONE); } finally { await f.close(); }
  assert.deepEqual(got.map((g) => g.stage), ["reading_data", "profiling", "splitting"]);
  assert.deepEqual(f.paths(), ["POST /v1/ask", "GET /v1/tasks/tk_w/events", "GET /v1/tasks/tk_w/events", "POST /v1/poll"]);
  assert.equal(f.seen[2]!.lastId, "1");
});

test("a server without the stream: ask falls back to polling", async () => {
  const f = await fake([PENDING, PENDING, DONE], [404]);
  const got: any[] = [];
  try { assert.deepEqual(await client(f.url).ask({ churn: yesno("churned") }, { ...askOpts, onProgress: (e) => got.push(e) }), DONE); } finally { await f.close(); }
  assert.deepEqual(f.paths(), ["POST /v1/ask", "GET /v1/tasks/tk_w/events", "POST /v1/poll", "POST /v1/poll"]);
  assert.deepEqual(got, [PENDING, PENDING]);
});

test("a stream that drops and cannot be reopened: ask falls back to polling", async () => {
  const f = await fake([PENDING, DONE], [[S1, "drop"], 503]);
  const got: any[] = [];
  try { assert.deepEqual(await client(f.url).ask({ churn: yesno("churned") }, { ...askOpts, onProgress: (e) => got.push(e) }), DONE); } finally { await f.close(); }
  assert.deepEqual(got.map((g) => g.stage ?? g.status), ["reading_data", "pending"]);
});

test("the stream keeps the ask's deadline", async () => {
  const f = await fake([PENDING], [[S1, "hold"]]);
  const t0 = Date.now();
  try {
    await assert.rejects(client(f.url).ask({ churn: yesno("churned") }, { ...askOpts, timeoutMs: 1000, onProgress: () => {} }), (e: DatagoatError) => e.problem.code === "poll_timeout");
  } finally { await f.close(); }
  assert.ok(Date.now() - t0 < 8000);
});

test("without onProgress the stream is never opened", async () => {
  const f = await fake([PENDING, DONE], [[S1, DONE_EV]]);
  try { assert.deepEqual(await client(f.url).ask({ churn: yesno("churned") }, askOpts), DONE); } finally { await f.close(); }
  assert.deepEqual(f.paths(), ["POST /v1/ask", "POST /v1/poll"]);
});

test("a stream that sends headers then nothing: ask falls back to polling at once", async () => {
  const f = await fake([PENDING, DONE], [["silent"]]);
  const dg = client(f.url);
  dg.eventsFirstByteMs = 500;
  const got: any[] = [];
  const t0 = Date.now();
  try { assert.deepEqual(await dg.ask({ churn: yesno("churned") }, { ...askOpts, onProgress: (e) => got.push(e) }), DONE); } finally { await f.close(); }
  assert.ok(Date.now() - t0 < 3000);
  assert.deepEqual(got, [PENDING]);
  assert.deepEqual(f.paths(), ["POST /v1/ask", "GET /v1/tasks/tk_w/events", "POST /v1/poll"]);
});
