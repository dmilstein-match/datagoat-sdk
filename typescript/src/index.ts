/**
 * @datagoat/sdk: ask yes/no, score, choice and rank questions about cases.
 * No dependencies; uses the platform `fetch`. `verifyOffline` checks a Verdict with no call to Datagoat.
 */
export { canonical, fetchKeys, JWKS_URL, pyNumber, verifyOffline } from "./verify.js";
export type { Jwk, Jwks, VerifyStatus } from "./verify.js";
export const DEFAULT_BASE = "https://api.datagoat.io";
/** The API's request limit; a bigger body would be refused before reaching Datagoat. */
export const MAX_REQUEST_BYTES = 4_500_000;

export interface Problem { status: number; code: string; detail: string; remedy: string; field?: string; request_id?: string }

export class DatagoatError extends Error {
  /** Milliseconds the API asked to wait before retrying (a 429 names it). */
  retryAfterMs?: number;
  constructor(readonly problem: Problem) { super(`${problem.code}: ${problem.detail} (${problem.remedy})`); }
  get retryable(): boolean { return [429, 502, 503, 504].includes(this.problem.status); }
}

/** Operations where sending the same request twice cannot do the work twice: reads, an ask (its
 *  idempotency_key returns the first call's task), deletes and outcome reports (duplicates are
 *  recognised). An attest is safe only with an event_id; add-dataset (which may append) never is,
 *  except after a 429, which means the request was turned away before any work. */
export const RETRY_SAFE: ReadonlySet<string> = new Set(["ask", "poll", "page", "preflight", "verify", "describe", "drift", "evidence", "report-outcomes", "delete-dataset", "suggest", "extend-model", "delete-model"]);

function mayRetry(op: string, body: unknown, status: number | null): boolean {
  if (status === 429) return true;
  if (status !== null && ![502, 503, 504].includes(status)) return false;
  if (op === "attest") return Boolean((body as { event_id?: string } | null)?.event_id);
  return RETRY_SAFE.has(op);
}

/** 1s, 2s, 4s ... with jitter, so many clients retrying at once spread out. */
const backoffMs = (attempt: number) => Math.min(30_000, 1000 * 2 ** (attempt - 1)) * (0.5 + Math.random() / 2);

function retryAfterMs(r: Response, json: Record<string, unknown>): number | undefined {
  if (typeof json.retry_after_ms === "number" && json.retry_after_ms >= 0) return json.retry_after_ms;
  const h = Number(r.headers.get("retry-after"));
  return r.headers.get("retry-after") !== null && Number.isFinite(h) && h >= 0 ? h * 1000 : undefined;
}

/** A paged answer made whole: each answer's list is its slices joined in order. Paging only ever
 *  slices, so nothing else changes. Verdicts withheld from the first page are not on later pages
 *  either; download(out.page.answer_url) returns them. */
function mergePages(first: Answer, pages: Answer[]): Answer {
  const answers: Record<string, any> = {};
  for (const [q, a] of Object.entries(first.answers ?? {})) answers[q] = { ...a };
  for (const p of pages) {
    for (const [q, a] of Object.entries<any>(p.answers ?? {})) {
      for (const key of ["cases", "ranked"] as const) if (a[key] && answers[q]) answers[q][key] = [...(answers[q][key] ?? []), ...a[key]];
    }
  }
  for (const a of Object.values(answers)) delete a.page;
  return { ...first, answers };
}

/** Chunks of one askMany joined: cases concatenated in order, Verdicts listed chunk by chunk. A
 *  question whose chunks disagree on state or model (only a change to the record between calls
 *  could cause it) is not joined: its per-chunk answers are under `unjoined`. */
function joinChunks(outs: Answer[]): Answer {
  const first = outs[0]!;
  const { task_id: _t, answers: _a, export: _e, ...rest } = first as Answer & { export?: unknown };
  const out: Answer = { ...rest, status: "done", task_ids: outs.map((o) => o.task_id) };
  for (const k of ["fits_run", "billable_decisions"]) if (outs.some((o) => k in o)) out[k] = outs.reduce((n, o) => n + Number(o[k] ?? 0), 0);
  const exports = outs.map((o) => o.export).filter(Boolean);
  if (exports.length) out.exports = exports;
  const answers: Record<string, any> = {};
  const unjoined: Record<string, any[]> = {};
  for (const q of Object.keys(first.answers ?? {})) {
    const parts = outs.map((o) => o.answers?.[q] ?? {});
    const same = parts.every((p) => p.state === parts[0].state && p.model_ref === parts[0].model_ref && JSON.stringify(p.model_refs) === JSON.stringify(parts[0].model_refs));
    if (!same) { unjoined[q] = parts; continue; }
    const a = { ...parts[0] };
    if ("cases" in a) a.cases = parts.flatMap((p) => p.cases ?? []);
    if ("verdicts" in a) a.verdicts = parts.flatMap((p) => p.verdicts ?? []);
    answers[q] = a;
  }
  out.answers = answers;
  if (Object.keys(unjoined).length) out.unjoined = unjoined;
  return out;
}

export type Question = Record<string, unknown> & { type: "yesno" | "score" | "choice" | "rank" };
type Opts = { outcome_is_desirable?: boolean; positive_values?: string[]; refit_of?: string };

const stated = (o: Record<string, unknown>) => Object.fromEntries(Object.entries(o).filter(([, v]) => v !== undefined));

/** The chance the outcome happens for each case. */
export const yesno = (outcome_column: string, o: Opts = {}): Question => ({ type: "yesno", outcome_column, ...stated(o) });
/** That chance as a level. Default levels: unlikely, possible, likely, very_likely. */
export const score = (outcome_column: string, o: Opts & { levels?: string[]; cuts?: number[] } = {}): Question => ({ type: "score", outcome_column, ...stated(o) });
/** Cases in order of the chance. Omit `cases` in ask() to rank the whole record. */
export const rank = (outcome_column: string, o: Opts & { top_k?: number } = {}): Question => ({ type: "rank", outcome_column, ...stated(o) });
/** Answer from an existing model instead of fitting: its outcome and yes values are the model's.
 *  Send the cases as rows (no data needed), or send a newer record and name cases by id. */
export function fromModel(model_ref: string, o: { type?: "yesno" | "score" | "rank"; levels?: string[]; cuts?: number[]; top_k?: number } = {}): Question {
  const { type = "yesno", ...rest } = o;
  if (!["yesno", "score", "rank"].includes(type)) throw new Error("a model answers yesno, score or rank; choice is answered from its record");
  return { type, model_ref, ...stated(rest) };
}
/** The best option: the likeliest for an outcome you want, the least likely for one you avoid.
 *  `outcome_is_desirable` is required here, and only here. */
export function choice(o: { outcome_is_desirable: boolean; option_column?: string; options?: string[]; outcome_column?: string; option_outcomes?: Record<string, string>; positive_values?: string[] }): Question {
  if (typeof o.outcome_is_desirable !== "boolean") throw new TypeError("choice needs outcome_is_desirable: true or false");
  if ((o.option_outcomes === undefined) === (o.option_column === undefined)) throw new Error("choice takes EITHER option_column + options + outcome_column, OR option_outcomes");
  return { type: "choice", ...stated(o) };
}

/** How to read a record that is not one row per case. Pass with time_column. */
export type Shape =
  | { kind: "table" }
  | { kind: "events"; label: { lapsed: true } | { name: string; when: Predicate }; event_column?: string; value_columns?: string[]; horizon?: { value: number; unit: TimeUnit }; lookback?: [number, number]; horizon_days?: 30 | 60 | 90; lookback_days?: [number, number]; as_of?: string }
  | { kind: "series"; value_columns: string[]; windows: number[]; time_unit?: TimeUnit }
  | { kind: "panel"; label: { trend_of: string; direction?: "down" | "up"; alpha?: 0.05 | 0.1 } | { window_of: string; periods?: 1 | 2 | 3 | 4; agg?: "max" | "min" | "last" | "any" }; min_history?: number }
  | { kind: "signals"; signal_columns: string[]; windows: number[]; snapshot_every: "15min" | "1h" | "6h" | "1d" | "1w"; horizon: 1 | 3 | 7 | 30; event_column?: string; event_start_column?: string; event_end_column?: string; min_history?: number; as_of?: string }
  | { kind: "traces"; agent_column?: string; task_column?: string; tool_column?: string }
  | { kind: "snapshots"; snapshots: DataSource; snapshot_id_column: string; snapshot_time_column: string; outcome_time_column?: string; event_column?: string; value_columns?: string[]; lookback_days?: [30, 90] | [7, 30] | [90, 365] };
/** What a record's time column counts. `steps` is a whole-number column with no calendar. */
export type TimeUnit = "minutes" | "hours" | "days" | "weeks" | "steps";
export type Predicate = { column: string; op: "==" | "!=" | "<" | "<=" | ">" | ">=" | "in"; value: unknown } | { all: Predicate[] } | { any: Predicate[] };

/** Shape builders, one per kind: pass the result as `shape`, with `time_column`, to ask(). */
type Without<K> = Omit<Extract<Shape, { kind: K }>, "kind">;
export const events = (o: Without<"events">): Shape => ({ kind: "events", ...stated(o) } as Shape);
export const series = (o: Without<"series">): Shape => ({ kind: "series", ...stated(o) } as Shape);
export const panel = (o: Without<"panel">): Shape => ({ kind: "panel", ...stated(o) } as Shape);
export const signals = (o: Without<"signals">): Shape => ({ kind: "signals", ...stated(o) } as Shape);
export const traces = (o: Without<"traces"> = {}): Shape => ({ kind: "traces", ...stated(o) } as Shape);
/** An event log read as of moments you choose: `snapshots` is the snapshot table (one row per case per
 *  moment, with the outcome). Whole cases are held out. */
export const snapshots = (o: Without<"snapshots">): Shape => ({ kind: "snapshots", ...stated(o) } as Shape);

export interface DataSource { dataset_id?: string; rows?: Record<string, unknown>[]; csv?: string; fetch_url?: string; fetch_headers?: Record<string, string> }
export interface AskOptions {
  /** The record. Optional only when every question is fromModel() and the cases are rows. */
  data?: DataSource; entity_column: string; subject_kind: "person" | "org" | "object" | "event" | "other";
  /** How long a model this call fits keeps answering: 1 to 365 days (default 90). */
  model_ttl_days?: number;
  /** A table with several rows per case: the column naming the case, so whole cases are held out. */
  group_column?: string;
  /** Keeps this call's models and usage apart, e.g. one namespace per customer of your product. */
  namespace?: string;
  cases?: { ids: string[] } | { rows: Record<string, unknown>[] }; time_column?: string; shape?: Shape; band?: boolean;
  acknowledge_decision_support?: boolean; idempotency_key?: string;
  /** "csv" adds export.results_url: every case, one row per question per case, for 24 hours. */
  export?: "csv";
  /** Follow a pending first fit to its answer (default true), for at most timeoutMs (default 15 min). */
  wait?: boolean; timeoutMs?: number;
}
export type Answer = Record<string, unknown> & { status: "done" | "pending"; task_id?: string; answers?: Record<string, any> };

export class Datagoat {
  readonly base: string;
  private readonly auth: string;
  private readonly maxRetries: number;
  /** Replaceable in tests. */
  sleep: (ms: number) => Promise<void> = (ms) => new Promise((res) => setTimeout(res, ms));
  constructor(opts: { apiKey: string; baseUrl?: string; fetch?: typeof fetch; maxRetries?: number }) {
    if (!opts.apiKey) throw new Error("apiKey is required (a dgk_live_ or dgk_test_ key)");
    this.auth = `Bearer ${opts.apiKey}`;
    this.base = (opts.baseUrl ?? DEFAULT_BASE).replace(/\/$/, "");
    this.f = opts.fetch ?? fetch;
    this.maxRetries = Math.max(0, opts.maxRetries ?? 2);
  }
  private readonly f: typeof fetch;

  /** One operation, retried when that is safe (see RETRY_SAFE): a 429 always, after the wait the API
   *  names; a 502/503/504 or a dropped connection only where sending twice cannot do the work twice.
   *  A problem the API answered with (4xx) and a refusal (an answer, not an error) are never retried. */
  private async call<T = Record<string, unknown>>(op: string, body: unknown, keyless = false): Promise<T> {
    for (let attempt = 0; ; attempt++) {
      let wait: number | undefined;
      try {
        return await this.once<T>(op, body, keyless);
      } catch (e) {
        const status = e instanceof DatagoatError ? e.problem.status : null;
        if (!(e instanceof DatagoatError) && !(e instanceof TypeError)) throw e;
        if (attempt >= this.maxRetries || !mayRetry(op, body, status)) {
          if (e instanceof DatagoatError) throw e;
          throw new DatagoatError({ status: 503, code: "network_error", detail: String((e as Error).message), remedy: "check the connection and retry" });
        }
        wait = e instanceof DatagoatError ? e.retryAfterMs : undefined;
      }
      await this.sleep(wait === undefined ? backoffMs(attempt + 1) : Math.min(wait, 60_000));
    }
  }

  private async once<T>(op: string, body: unknown, keyless: boolean): Promise<T> {
    const text = JSON.stringify(body ?? {});
    const size = new TextEncoder().encode(text).length;
    if (size > MAX_REQUEST_BYTES) throw new DatagoatError({ status: 413, code: "request_too_large", detail: `this request is ${size.toLocaleString("en-US")} bytes; a request is at most ${MAX_REQUEST_BYTES.toLocaleString("en-US")}`, remedy: "store the table first with addDataset (upload: true for a file), then ask by dataset_id" });
    const r = await this.f(`${this.base}/v1/${op}`, {
      method: "POST",
      headers: { "content-type": "application/json", ...(keyless ? {} : { authorization: this.auth }) },
      body: text,
    });
    let json: Record<string, unknown>;
    try {
      json = (await r.json()) as Record<string, unknown>;
    } catch {
      // A 200 whose body was cut off (a dropped stream) is not an answer.
      if (r.ok) throw new DatagoatError({ status: 502, code: "incomplete_response", detail: "the response ended before the answer was complete", remedy: "retry; an ask with the same idempotency_key returns the same answer" });
      json = {};
    }
    if (!r.ok) {
      const err = new DatagoatError({ status: r.status, code: String(json.code ?? "http_error"), detail: String(json.detail ?? r.statusText), remedy: String(json.remedy ?? "see https://datagoat.io/docs"), ...(json.field ? { field: String(json.field) } : {}), ...(json.request_id ? { request_id: String(json.request_id) } : {}) });
      err.retryAfterMs = retryAfterMs(r, json);
      throw err;
    }
    return json as T;
  }

  /** Ask typed questions about cases. Each answer's state is answered | refused | not_yet. The
   *  answer comes back whole at any size. */
  async ask(questions: Record<string, Question>, o: AskOptions): Promise<Answer> {
    if (!Object.keys(questions).length) throw new Error("ask needs at least one question");
    if (o.export !== undefined && o.export !== "csv") throw new Error('export is "csv"');
    const { wait = true, timeoutMs = 15 * 60_000, ...rest } = o;
    let out = await this.call<Answer>("ask", { ...rest, questions, idempotency_key: o.idempotency_key ?? crypto.randomUUID() });
    const deadline = Date.now() + timeoutMs;
    while (wait && out.status === "pending") {
      if (Date.now() >= deadline) throw new DatagoatError({ status: 504, code: "poll_timeout", detail: "the task is still running", remedy: `poll("${out.task_id}") later; do not re-submit (that would fit twice)` });
      await this.sleep(Math.max(500, Number(out.retry_after_ms ?? 2000)));
      out = await this.poll(String(out.task_id));
    }
    return this.whole(out);
  }

  /** REST answers come whole; should one ever arrive paged, fetch the rest and join it. */
  private async whole(out: Answer): Promise<Answer> {
    let cursor = (out.page as { next_cursor?: string } | undefined)?.next_cursor;
    if (out.status !== "done" || !cursor) return out;
    const pages: Answer[] = [];
    while (cursor) {
      const p = await this.page(cursor);
      pages.push(p);
      cursor = (p.page as { next_cursor?: string } | undefined)?.next_cursor;
    }
    const merged = mergePages(out, pages);
    // Joined: there is no next page. The link stays only while it holds withheld Verdicts.
    if (Object.values(merged.answers ?? {}).some((a: any) => "verdicts_withheld" in a)) {
      const { next_cursor: _n, ...link } = (out.page ?? {}) as Record<string, unknown>;
      merged.page = link;
    } else delete merged.page;
    return merged;
  }

  /**
   * Ask about more cases than one call takes (10,000), in calls of `chunkSize` cases. Every chunk is
   * answered by the same model (the fit is keyed by the record's content: it runs once, later chunks
   * reuse it), and each answer's cases are joined in the order given. rank is not accepted: a ranking
   * is over the whole record, so ask it with ask() and no cases. If every question is refused or
   * not_yet on the first chunk, that is the answer for all (it is about the record), and no further
   * chunk is sent. Chunk n uses idempotency key `<key>:<n>`: pass idempotency_key yourself and
   * re-running after a failure resumes without fitting or billing finished chunks again. Every chunk
   * is waited for, so `wait: false` is not accepted.
   */
  async askMany(questions: Record<string, Question>, o: AskOptions & { cases: { ids: string[] } | { rows: Record<string, unknown>[] }; chunkSize?: number; onChunk?: (done: number, of: number) => void }): Promise<Answer> {
    for (const [n, q] of Object.entries(questions)) if (q.type === "rank") throw new Error(`question "${n}" is a rank: a ranking is over the whole record; use ask() without cases`);
    if (o.wait === false) throw new Error("askMany waits for every chunk; wait: false would return part of the answer");
    const { chunkSize = 5000, onChunk, cases, ...rest } = o;
    const kind = "ids" in cases ? "ids" : "rows";
    const items: unknown[] = (cases as Record<string, unknown[]>)[kind] ?? [];
    if (!items.length) throw new Error("no cases");
    if (!(chunkSize >= 1 && chunkSize <= 10_000)) throw new Error("chunkSize is 1 to 10,000");
    const base = o.idempotency_key ?? crypto.randomUUID();
    if (base.length > 120) throw new Error("idempotency_key is at most 120 characters here (a chunk number is added)");
    const outs: Answer[] = [];
    const total = Math.ceil(items.length / chunkSize);
    for (let n = 0; n < total; n++) {
      const part = items.slice(n * chunkSize, (n + 1) * chunkSize);
      const out = await this.ask(questions, { ...rest, cases: { [kind]: part } as AskOptions["cases"], idempotency_key: `${base}:${n}` });
      outs.push(out);
      onChunk?.(n + 1, total);
      if (n === 0 && Object.values(out.answers ?? {}).every((a: any) => a.state !== "answered")) break;
    }
    return joinChunks(outs);
  }

  /** The next page of a paged answer (answers over MCP are paged; REST answers come whole). */
  page(cursor: string) { return this.call<Answer>("page", { cursor }); }

  /** Fetch an export or answer link (valid 24 hours). The link is its own credential: no key is sent.
   *  Returns the Response, so a large file can be streamed (`res.body`) or read (`await res.text()`). */
  async download(url: string): Promise<Response> {
    const r = await this.f(url, { method: "GET" });
    if (!r.ok) {
      const json = (await r.json().catch(() => ({}))) as Record<string, unknown>;
      throw new DatagoatError({ status: r.status, code: String(json.code ?? "http_error"), detail: String(json.detail ?? r.statusText), remedy: String(json.remedy ?? "ask again for a new link") });
    }
    return r;
  }

  addDataset(body: DataSource & { dataset_id?: string; upload?: true; filename?: string }) { return this.call("add-dataset", body); }
  /** Delete a stored dataset now; otherwise it is deleted 24 hours after its last use. */
  deleteDataset(dataset_id: string) { return this.call<{ dataset_id: string; deleted: true }>("delete-dataset", { dataset_id }); }
  async poll(task_id: string) { return this.whole(await this.call<Answer>("poll", { task_id })); }
  preflight(dataset_id: string, o: { outcome_column?: string; predictors?: string[] } = {}) { return this.call("preflight", { dataset_id, ...o }); }
  reportOutcomes(model_ref: string, outcomes: Array<{ entity_id: string; outcome: boolean | number | string; observed_at: string; event_id?: string }>, o: { namespace?: string } = {}) { return this.call("report-outcomes", { model_ref, outcomes, ...stated(o) }); }
  drift(model_ref: string, o: { namespace?: string } = {}) { return this.call("drift", { model_ref, ...stated(o) }); }
  /** Which yes/no questions a table could be asked, and whether each is worth asking. Free; fits nothing. */
  suggest(o: { data: DataSource; entity_column: string; time_column?: string; include_categories?: boolean }) { return this.call("suggest", o); }
  /** Keep a model answering until `days` (1 to 365) from now. */
  extendModel(model_ref: string, days: number, o: { namespace?: string } = {}) { return this.call<{ model_ref: string; model_expires_at: string }>("extend-model", { model_ref, days, ...stated(o) }); }
  /** Delete a model now; its model_ref stops answering. */
  deleteModel(model_ref: string, o: { namespace?: string } = {}) { return this.call<{ model_ref: string; deleted: true }>("delete-model", { model_ref, ...stated(o) }); }
  /** Record that you acted on a case through one of its levers. Returns compliant, dose_fraction and evaluated_feature. */
  attest(a: { model_ref: string; entity_id: string; lever_token: string; post_value: number | string | boolean | null; acted_at: string; event_id?: string; namespace?: string }) { return this.call("attest", a); }
  /** Did acting work? Outcomes of cases acted on vs not, once each group has 30 with an outcome. */
  evidence(model_ref: string, o: { namespace?: string } = {}) { return this.call("evidence", { model_ref, ...stated(o) }); }
  /** How the model's earlier calls held up against the outcomes reported for them. */
  trackRecord(model_ref: string, o: { namespace?: string } = {}) { return this.call("track-record", { model_ref, ...stated(o) }); }
  /** A namespace's profile: with words, exclude or display it replaces it; without, it reads it. */
  profile(o: { namespace?: string; words?: { case?: string; outcome?: string; columns?: Record<string, string> }; exclude?: string[]; display?: "chance" | "bands" } = {}) {
    return this.call("profile", stated(o));
  }
  /** valid | invalid_signature | expired | unknown_key. Sends no key. */
  async verify(verdict: Record<string, unknown>, signature: Record<string, unknown> | null): Promise<string> {
    if (!signature) return "invalid_signature";
    return (await this.call<{ status: string }>("verify", { verdict, signature }, true)).status;
  }
  /** True when every Verdict in an ask answer is valid. */
  async verifyAll(answer: Answer): Promise<boolean> {
    for (const a of Object.values(answer.answers ?? {})) for (const v of a.verdicts ?? []) if ((await this.verify(v.verdict, v.signature)) !== "valid") return false;
    return true;
  }
  describe() { return this.call("describe", {}, true); }
}

/** A free test key (sample datasets only). No account needed. */
export async function register(baseUrl = DEFAULT_BASE): Promise<{ api_key: string; mode: "test" }> {
  const r = await fetch(`${baseUrl.replace(/\/$/, "")}/v1/agents/register`, { method: "POST" });
  const json = (await r.json()) as Record<string, unknown>;
  if (!r.ok) throw new DatagoatError({ status: r.status, code: String(json.code), detail: String(json.detail), remedy: String(json.remedy) });
  return json as { api_key: string; mode: "test" };
}
