/**
 * @datagoat/sdk: ask yes/no, score, choice and rank questions about cases.
 * No dependencies; uses the platform `fetch`. `verifyOffline` checks a Verdict with no call to Datagoat.
 */
export { canonical, fetchKeys, JWKS_URL, pyNumber, verifyOffline } from "./verify.js";
export type { Jwk, Jwks, VerifyStatus } from "./verify.js";
export const DEFAULT_BASE = "https://api.datagoat.io";
/** The API's request limit; a bigger body would be refused before reaching Datagoat. */
export const MAX_REQUEST_BYTES = 4_500_000;
/** Upload pieces (uploadFile): the API takes at most PIECE_MAX_BYTES a piece and MAX_PIECES pieces;
 *  the client cuts at about PIECE_TARGET_BYTES, at a row boundary. */
export const PIECE_MAX_BYTES = 4_000_000;
export const PIECE_TARGET_BYTES = 3_500_000;
export const MAX_PIECES = 250;
const UPLOAD_RETRIES = 5;

/** A filename the API accepts (letters, digits, dot, dash, underscore; at most 120). */
export function uploadName(name: string): string {
  return name.replace(/^.*[\\/]/, "").replace(/[^A-Za-z0-9._-]/g, "_").slice(0, 120) || "dataset.csv";
}

async function readSource(source: string | Uint8Array | ArrayBuffer | Blob, filename?: string): Promise<{ bytes: Uint8Array; name: string }> {
  if (typeof source === "string") {
    const { readFile } = await import("node:fs/promises");
    return { bytes: new Uint8Array(await readFile(source)), name: uploadName(filename ?? source) };
  }
  const named = (source as { name?: unknown }).name;
  const name = uploadName(filename ?? (typeof named === "string" ? named : "dataset.csv"));
  if (source instanceof Uint8Array) return { bytes: source, name };
  if (source instanceof ArrayBuffer) return { bytes: new Uint8Array(source), name };
  return { bytes: new Uint8Array(await source.arrayBuffer()), name };
}

/** "gzip" or "parquet" when the bytes are such a file (gzip 1f 8b; Parquet PAR1 at both ends), else
 *  null (a CSV). The server and the engine recognise them the same way, by their bytes. */
export function binaryFormat(b: Uint8Array): "gzip" | "parquet" | null {
  if (b.length >= 2 && b[0] === 0x1f && b[1] === 0x8b) return "gzip";
  const par1 = (i: number) => b[i] === 0x50 && b[i + 1] === 0x41 && b[i + 2] === 0x52 && b[i + 3] === 0x31;
  return b.length >= 12 && par1(0) && par1(b.length - 4) ? "parquet" : null;
}

/** A gzip or Parquet file's upload pieces: byte slices, joined verbatim by the server. */
export function byteSlices(b: Uint8Array, size = PIECE_TARGET_BYTES): Uint8Array[] {
  const out: Uint8Array[] = [];
  for (let i = 0; i < b.length; i += size) out.push(b.subarray(i, i + size));
  return out;
}

/**
 * Cut a CSV into upload pieces: the header, a newline, then whole rows, each piece at most `target`
 * bytes unless one row alone is bigger. A row ends at a newline outside double quotes, so a quoted
 * field with line breaks is never split. A leading byte-order mark is dropped; every piece is
 * checked to be UTF-8.
 */
export function csvPieces(bytes: Uint8Array, target = PIECE_TARGET_BYTES): Uint8Array[] {
  const b = bytes.length >= 3 && bytes[0] === 0xef && bytes[1] === 0xbb && bytes[2] === 0xbf ? bytes.subarray(3) : bytes;
  let quoted = false;
  let header: Uint8Array | null = null;
  let body = 0;
  let row = 0;
  const out: Uint8Array[] = [];
  const utf8 = new TextDecoder("utf-8", { fatal: true });
  const piece = (h: Uint8Array, rows: Uint8Array) => {
    const p = new Uint8Array(h.length + 1 + rows.length);
    p.set(h, 0);
    p[h.length] = 0x0a;
    p.set(rows, h.length + 1);
    if (p.length > PIECE_MAX_BYTES) throw new Error(`one row of this file is over ${PIECE_MAX_BYTES.toLocaleString("en-US")} bytes; upload it with transport: "presigned"`);
    try { utf8.decode(p); } catch { throw new Error('this file is not UTF-8 text; save it as "CSV UTF-8" and upload it again'); }
    out.push(p);
  };
  const blank = (x: Uint8Array) => x.every((c) => c === 0x20 || c === 0x09 || c === 0x0a || c === 0x0d);
  for (let i = 0; i < b.length; i++) {
    const c = b[i];
    if (c === 0x22) { quoted = !quoted; continue; }
    if (c !== 0x0a || quoted) continue;
    if (header === null) {
      header = b.subarray(0, i > 0 && b[i - 1] === 0x0d ? i - 1 : i);
      body = row = i + 1;
      continue;
    }
    if (row > body && header.length + 1 + (i + 1 - body) > target) {
      piece(header, b.subarray(body, row));
      body = row;
    }
    row = i + 1;
  }
  if (header === null && !blank(b)) { header = b; body = row = b.length; }
  if (header !== null) {
    let tail = b.subarray(body);
    if (!blank(tail) && row > body && header.length + 1 + tail.length > target) {
      piece(header, b.subarray(body, row));
      tail = b.subarray(row);
    }
    if (!blank(tail)) piece(header, tail);
  }
  if (!out.length) throw new Error("the file needs a header row and at least one row of data");
  return out;
}

export interface Problem {
  status: number; code: string; detail: string; remedy: string; field?: string; request_id?: string;
  /** The code's entry on https://datagoat.io/docs/errors. */
  doc_url?: string;
  /** invalid_outcomes: every bad row, as {index, field, detail}. */
  errors?: Array<{ index?: number; field?: string; detail?: string; [k: string]: unknown }>;
  /** Only when the analysis engine raised the problem: the engine's id for that call (an ask's
   *  task_id). Quote it beside request_id to support. */
  engine_request_id?: string;
  /** mapping_expired: the manifest of the mapping whose record is gone (what it was built from). */
  manifest?: Record<string, unknown>;
}

/** One source of a mapping: a dataset (ds_… or sample:…) or a URL, with an optional label. */
export type MapSource = { dataset_id: string; label?: string } | { fetch_url: string; fetch_headers?: Record<string, string>; label?: string };

/** dg_map's answers to a proposal's questions (each one of its options). */
export interface MapAnswers {
  entity?: Array<{ source: string; column: string }>;
  time_column?: Array<{ source: string; column: string }>;
  join?: { table: string; right_key: string };
  outcome?: { offer_id: string };
}

/** A problem the API answered with. Each family has its own subclass, so a catch can tell them
 *  apart: ValidationError (`field` names what to fix), NotFoundError (and ModelUnavailableError
 *  for a model_ref that no longer answers), RateLimitError, PaymentRequiredError. A refusal is
 *  never an exception: it is an answer with state "refused". */
export class DatagoatError extends Error {
  /** Milliseconds the API asked to wait before retrying (a 429 names it). */
  retryAfterMs?: number;
  /** reportOutcomes over several chunks: rows of the list already sent and written before the chunk that failed. */
  writtenBefore = 0;
  constructor(readonly problem: Problem) { super(`${problem.code}: ${problem.detail} (${problem.remedy})`); this.name = new.target.name; }
  get retryable(): boolean { return [429, 502, 503, 504].includes(this.problem.status); }
  get code(): string { return this.problem.code; }
}
/** 400/422: the request is wrong. `field` names the argument; for invalid_outcomes, `errors` lists every bad row. */
export class ValidationError extends DatagoatError {
  get field(): string | undefined { return this.problem.field; }
  get errors(): NonNullable<Problem["errors"]> { return this.problem.errors ?? []; }
}
/** 404/410: what the call names does not exist here, or no longer does (`gone` for 410). */
export class NotFoundError extends DatagoatError {
  get gone(): boolean { return this.problem.status === 410; }
}
/** A model_ref that does not answer: model_deleted, model_expired (410) or model_ref_missing (404).
 *  Asking again with the record fits a new model. */
export class ModelUnavailableError extends NotFoundError {}
/** 429: this minute's requests are spent; retryAfterMs says how long. */
export class RateLimitError extends DatagoatError {}
/** 402: asking about your own data needs a card on file. The samples stay free. */
export class PaymentRequiredError extends DatagoatError {}

const MODEL_CODES = new Set(["model_deleted", "model_expired", "model_ref_missing"]);

/** The typed error for a problem, by its code family. */
export function errorFor(p: Problem): DatagoatError {
  if (MODEL_CODES.has(p.code)) return new ModelUnavailableError(p);
  if (p.status === 404 || p.status === 410) return new NotFoundError(p);
  if (p.status === 400 || p.status === 422) return new ValidationError(p);
  if (p.status === 429) return new RateLimitError(p);
  if (p.status === 402) return new PaymentRequiredError(p);
  return new DatagoatError(p);
}

/** Operations where sending the same request twice cannot do the work twice: reads, an ask and a
 *  backtest (their idempotency_key returns the first call's task), deletes, outcome reports
 *  (duplicates are recognised) and a profile (sent whole, it replaces the same profile again). An
 *  attest is safe only with an event_id; add-dataset (which may append) and map (a confirm stores a
 *  mapping) never are, except after a 429, which means the request was turned away before any work. */
export const RETRY_SAFE: ReadonlySet<string> = new Set(["ask", "poll", "page", "preflight", "verify", "describe", "drift", "evidence", "track-record", "profile", "report-outcomes", "delete-dataset", "suggest", "extend-model", "delete-model", "backtest"]);

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
  | SnapshotsGrid
  | { kind: "snapshots"; snapshots: DataSource; snapshot_id_column: string; snapshot_time_column: string; outcome_time_column?: string; event_column?: string; value_columns?: string[]; lookback_days?: [30, 90] | [7, 30] | [90, 365] };
/** The snapshots shape without a snapshot table: one snapshot of each case every `snapshot_every`,
 *  each with the outcome in the `horizon` days after it (blank until that window has passed). The
 *  outcome is `label` or `outcome_time_column`, not both; the reading names the column
 *  (lapsed_90d, {name}_next_90d, {column}_next_90d). Ask with `cases: { open: true }` for every case today. */
export type SnapshotsGrid = {
  kind: "snapshots"; snapshot_every: "1d" | "1w" | "4w"; horizon: { value: 1 | 7 | 30 | 60 | 90; unit: "days" };
  label?: { lapsed: true } | { name: string; when: Predicate }; outcome_time_column?: string;
  event_column?: string; value_columns?: string[]; lookbacks_days?: [30, 90] | [7, 30] | [90, 365]; terminal?: boolean; as_of?: string;
};
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
/** An event log read as each case was at many moments. Without `snapshots` (a snapshot table): one
 *  snapshot per case every `snapshot_every` (1d, 1w, 4w), the outcome in the `horizon` days after
 *  each (see SnapshotsGrid). With it: one row per case per moment you choose, with the outcome.
 *  Whole cases are held out either way. */
export const snapshots = (o: Without<"snapshots">): Shape => {
  if (!("snapshots" in o) || o.snapshots === undefined) {
    const g = o as Omit<SnapshotsGrid, "kind">;
    if ((g.label === undefined) === (g.outcome_time_column === undefined)) throw new Error("send label ({ lapsed: true } or { name, when }) or outcome_time_column, not both");
  }
  return { kind: "snapshots", ...stated(o) } as Shape;
};

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
  /** By id, as rows, or `{ open: true }`: today's open cases, found by the engine (on a snapshots or
   *  mapped record, each case at as_of; each answer's quality.record then counts the labelled and
   *  open rows the fit read). */
  cases?: { ids: string[] } | { rows: Record<string, unknown>[] } | { open: true }; time_column?: string; shape?: Shape; band?: boolean;
  acknowledge_decision_support?: boolean; idempotency_key?: string;
  /** "csv" adds export.results_url: every case, one row per question per case, for 24 hours. */
  export?: "csv";
  /** "concise" leaves each answer's Verdicts out (verdicts_withheld); page.answer_url has the whole answer.
   *  "compact" also shows each case as entity_id, p, p_display and, when the answer has them, band,
   *  level and says (about a twentieth of the full size); a pending fit is then polled compact. */
  response_format?: "full" | "concise" | "compact";
  /** Follow a pending first fit to its answer (default true), for at most timeoutMs (default 15 min). */
  wait?: boolean; timeoutMs?: number;
  /** Called for each stage the server reports while a fit runs, from the task's event stream
   *  (`message` is the sentence to show, verbatim). Where the stream cannot be opened, the task is
   *  polled as before and this gets each pending poll body (`status: "pending"`). */
  onProgress?: (e: StageEvent | PendingPoll) => void;
  /** Every event of the task's stream, as { event, id, data }. */
  onEvent?: (e: TaskEvent) => void;
}
/** dg_backtest's options (the dataset_id is the first argument). */
export interface BacktestOptions {
  subject_kind: "person" | "org" | "object" | "event" | "other";
  /** How far apart the cutoffs are: the record's snapshot_every (the default) or a multiple of it. */
  every?: "1d" | "1w" | "4w";
  /** How many cutoffs, the most recent whose horizon has passed: 3 to 12 (default 6). */
  last?: number;
  /** single_column (default): one numeric column ranked on its own, graded beside the model. */
  baseline?: "single_column" | "none";
  acknowledge_decision_support?: boolean;
  idempotency_key?: string;
  response_format?: "full" | "concise";
  wait?: boolean; timeoutMs?: number;
  onProgress?: AskOptions["onProgress"]; onEvent?: AskOptions["onEvent"];
}
export type Answer = Record<string, unknown> & { status: "done" | "pending"; task_id?: string; answers?: Record<string, any> };
/** dg_poll and dg_page take "full" or "compact" (spec v3 S10). */
export type PageFormat = "full" | "compact";
function pageFormat(f: string): PageFormat {
  if (f !== "full" && f !== "compact") throw new Error('response_format is "full" or "compact"');
  return f;
}

/** A pending poll body: what onProgress gets when there is no event stream to read. */
export type PendingPoll = Answer & { status: "pending" };
/** A stage the server reported, verbatim. `message` is the sentence; nothing here composes one. */
export interface StageEvent {
  task_id: string; stage: string; frac?: number; fit?: number; of?: number;
  facts: { rows?: number; outcome?: string; training_rows?: number; held_out_rows?: number; bootstrap?: unknown; [k: string]: unknown };
  message: string; elapsed_ms: number; [k: string]: unknown;
}
/** One event of GET /v1/tasks/{task_id}/events: `stage`, `answer` (one per question), then `done` or `error`. */
export interface TaskEvent { event: "stage" | "answer" | "done" | "error" | (string & {}); id?: number | string; data: unknown }

/** The server sends a heartbeat every 15 s; three missed ones mean the connection is gone. */
const EVENTS_READ_TIMEOUT_MS = 45_000;
/** Reconnects in a row that bring nothing back (not even a heartbeat) before the stream is given up. */
const EVENTS_MAX_EMPTY_RECONNECTS = 3;
/** The stream could not be opened, or dropped and could not be reopened. ask() then polls. */
class StreamUnavailable extends Error { constructor(readonly error: DatagoatError) { super(error.message); } }
/** The ask's deadline passed while watching the stream. */
class DeadlinePassed extends Error {}
const pollTimeout = (taskId: string) => new DatagoatError({ status: 504, code: "poll_timeout", detail: "the task is still running", remedy: `poll("${taskId}") later; do not re-submit (that would fit twice)` });

export class Datagoat {
  readonly base: string;
  private readonly auth: string;
  private readonly maxRetries: number;
  /** Replaceable in tests. */
  sleep: (ms: number) => Promise<void> = (ms) => new Promise((res) => setTimeout(res, ms));
  /** The server sends the task's current state as soon as the stream opens. A stream with nothing
   *  in it after this long is being held back (a buffering proxy), and ask() polls instead. */
  eventsFirstByteMs = 10_000;
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
      const err = errorFor({
        status: r.status, code: String(json.code ?? "http_error"), detail: String(json.detail ?? r.statusText), remedy: String(json.remedy ?? "see https://datagoat.io/docs"),
        ...(json.field ? { field: String(json.field) } : {}), ...(json.request_id ? { request_id: String(json.request_id) } : {}),
        ...(json.doc_url ? { doc_url: String(json.doc_url) } : {}), ...(Array.isArray(json.errors) ? { errors: json.errors as Problem["errors"] } : {}),
        ...(json.engine_request_id ? { engine_request_id: String(json.engine_request_id) } : {}),
        ...(json.manifest && typeof json.manifest === "object" ? { manifest: json.manifest as Record<string, unknown> } : {}),
      });
      err.retryAfterMs = retryAfterMs(r, json);
      throw err;
    }
    return json as T;
  }

  /** Ask typed questions about cases. Each answer's state is answered | refused | not_yet. The
   *  answer comes back whole at any size. A pending first fit is polled to the end: sending the
   *  ask again instead of polling would start (and bill) a second fit. */
  async ask(questions: Record<string, Question>, o: AskOptions): Promise<Answer> {
    if (!Object.keys(questions).length) throw new Error("ask needs at least one question");
    if (o.export !== undefined && o.export !== "csv") throw new Error('export is "csv"');
    const { wait = true, timeoutMs = 15 * 60_000, onProgress, onEvent, ...rest } = o;
    // dg_poll and dg_page take full or compact: a compact ask is polled and paged compact.
    const later: PageFormat | undefined = o.response_format === "compact" ? "compact" : undefined;
    const out = await this.call<Answer>("ask", { ...rest, questions, idempotency_key: o.idempotency_key ?? crypto.randomUUID() });
    return this.whole(await this.follow(out, wait, timeoutMs, onProgress, onEvent, later), later);
  }

  /** What a record dg_map built from a log supported in its own past (sample:parley_record, or a
   *  confirm's dataset_id): a fit at each of the last cutoffs on the record's grid, graded on what
   *  came next beside a naive baseline, a summary, a decision and one signed Verdict (kind
   *  backtest). `every` and `last` are the cutoffs (default the record's snapshot_every, 6). It
   *  runs as a task: a pending walk is followed to its result, as ask does (never re-sent: that
   *  would start a second walk). Costs one fit per cutoff that ran; free on the samples. */
  async backtest(dataset_id: string, o: BacktestOptions): Promise<Answer> {
    const { wait = true, timeoutMs = 15 * 60_000, onProgress, onEvent, every, last, ...rest } = o;
    const cutoffs = stated({ every, last });
    const out = await this.call<Answer>("backtest", {
      data: { dataset_id }, ...(Object.keys(cutoffs).length ? { cutoffs } : {}), ...stated(rest),
      idempotency_key: o.idempotency_key ?? crypto.randomUUID(),
    });
    return this.whole(await this.follow(out, wait, timeoutMs, onProgress, onEvent));
  }

  /** Follow a pending task to its result, bounded; never re-submits (that would fit twice). */
  private async follow(first: Answer, wait: boolean, timeoutMs: number, onProgress: AskOptions["onProgress"], onEvent: AskOptions["onEvent"], later?: PageFormat): Promise<Answer> {
    let out = first;
    const deadline = Date.now() + timeoutMs;
    let watched = false;
    while (wait && out.status === "pending") {
      const taskId = String(out.task_id);
      // With a callback, watch the event stream first; without one, or with no stream, poll as before.
      if (!watched && (onProgress || onEvent)) {
        watched = true;
        if (await this.watch(taskId, deadline, onProgress, onEvent)) { out = await this.poll(taskId, later); continue; }
      }
      onProgress?.(out as PendingPoll);
      if (Date.now() >= deadline) throw pollTimeout(taskId);
      await this.sleep(Math.max(500, Number(out.retry_after_ms ?? 2000)));
      out = await this.poll(taskId, later);
    }
    return out;
  }

  /** Watch a task's stream to done or error: true when it got there, false when there is no stream
   *  (the caller polls). A callback's own error is not caught here. */
  private async watch(taskId: string, deadline: number, onProgress: AskOptions["onProgress"], onEvent: AskOptions["onEvent"]): Promise<boolean> {
    try {
      for await (const ev of this.stream(taskId, deadline)) {
        onEvent?.(ev);
        if (ev.event === "stage" && ev.data && typeof ev.data === "object") onProgress?.(ev.data as StageEvent);
      }
      return true;
    } catch (e) {
      if (e instanceof StreamUnavailable) return false;
      if (e instanceof DeadlinePassed) throw pollTimeout(taskId);
      throw e;
    }
  }

  /**
   * The event stream of a pending ask (GET /v1/tasks/{task_id}/events), as { event, id, data }:
   * `stage` events (the stage, its `message` sentence, `frac`, `fit`/`of` and the facts so far, all
   * as the server sent them), one `answer` per question, then `done` or `error`, where it stops. A
   * dropped connection is resumed from the last event id. The full answer comes from poll(taskId).
   * Throws the problem when the stream cannot be opened (a server without it answers 404), and
   * poll_timeout after timeoutMs (default 15 minutes). Works wherever fetch streams a body.
   * Leaving a `for await` loop early (break, return, throw) closes the connection.
   */
  async *events(taskId: string, o: { lastEventId?: string; timeoutMs?: number } = {}): AsyncGenerator<TaskEvent, void, undefined> {
    try {
      yield* this.stream(taskId, Date.now() + (o.timeoutMs ?? 15 * 60_000), o.lastEventId);
    } catch (e) {
      if (e instanceof StreamUnavailable) throw e.error;
      if (e instanceof DeadlinePassed) throw pollTimeout(taskId);
      throw e;
    }
  }

  private async openEvents(taskId: string, lastId: string | undefined, signal: AbortSignal): Promise<Response> {
    const headers: Record<string, string> = { accept: "text/event-stream", authorization: this.auth, "cache-control": "no-store" };
    if (lastId && !/[\r\n\0]/.test(lastId)) headers["last-event-id"] = lastId; // a header value never carries a line break
    let r: Response;
    try {
      r = await this.f(`${this.base}/v1/tasks/${encodeURIComponent(taskId)}/events`, { method: "GET", headers, signal });
    } catch (e) {
      throw new StreamUnavailable(new DatagoatError({ status: 503, code: "network_error", detail: String((e as Error).message), remedy: "check the connection, or poll the task" }));
    }
    if (!r.ok) {
      const json = (await r.json().catch(() => ({}))) as Record<string, unknown>;
      throw new StreamUnavailable(errorFor({ status: r.status, code: String(json.code ?? "http_error"), detail: String(json.detail ?? r.statusText), remedy: String(json.remedy ?? `poll("${taskId}")`), ...(json.doc_url ? { doc_url: String(json.doc_url) } : {}) }));
    }
    if (!(r.headers.get("content-type") ?? "").startsWith("text/event-stream") || !r.body) {
      await r.body?.cancel().catch(() => undefined);
      throw new StreamUnavailable(new DatagoatError({ status: r.status, code: "network_error", detail: "the response is not an event stream", remedy: `poll("${taskId}")` }));
    }
    return r;
  }

  /** Every event of a task's stream until done or error, reconnecting with Last-Event-ID when the
   *  connection drops (the server also closes it at its own time limit). Parses text/event-stream
   *  by the WHATWG rules: `field: value` lines, a blank line ends an event, `:` starts a comment. */
  private async *stream(taskId: string, deadline: number, lastEventId?: string): AsyncGenerator<TaskEvent, void, undefined> {
    let lastId = lastEventId;
    let retryMs = 1000;
    let empty = 0;
    for (let first = true; ; first = false) {
      if (Date.now() >= deadline) throw new DeadlinePassed();
      // First reconnect at once; after one that brought nothing back, wait a little.
      if (!first && empty) await this.sleep(Math.min(retryMs, 5000, Math.max(0, deadline - Date.now())));
      const ac = new AbortController();
      let timer: ReturnType<typeof setTimeout> | undefined;
      let alive = false, timedOut = false;
      // Re-armed on every chunk: the first-byte limit until something arrives, then the heartbeat
      // limit, never past the deadline.
      const arm = () => {
        clearTimeout(timer);
        timer = setTimeout(() => { timedOut = true; ac.abort(); }, Math.max(1, Math.min(alive ? EVENTS_READ_TIMEOUT_MS : this.eventsFirstByteMs, deadline - Date.now())));
      };
      arm();
      let r: Response;
      try { r = await this.openEvents(taskId, lastId, ac.signal); } catch (e) { clearTimeout(timer); if (Date.now() >= deadline) throw new DeadlinePassed(); throw e; }
      const reader = r.body!.getReader();
      const dec = new TextDecoder();
      let buf = "", event: string | undefined, data: string[] = [];
      try {
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          alive = true;
          arm();
          buf += dec.decode(value, { stream: true });
          for (let i = buf.search(/\r\n|\n|\r/); i >= 0; i = buf.search(/\r\n|\n|\r/)) {
            if (buf[i] === "\r" && i === buf.length - 1) break; // a CR that may be half of a CRLF: wait for more
            const line = buf.slice(0, i);
            buf = buf.slice(i + (buf.startsWith("\r\n", i) ? 2 : 1));
            if (line === "") {
              if (data.length) {
                const text = data.join("\n");
                let payload: unknown = text;
                try { payload = JSON.parse(text); } catch { /* not JSON: passed on as text */ }
                const ev: TaskEvent = { event: event ?? "message", ...(lastId !== undefined ? { id: /^\d+$/.test(lastId) ? Number(lastId) : lastId } : {}), data: payload };
                yield ev;
                if (ev.event === "done" || ev.event === "error") return;
              }
              event = undefined; data = [];
              continue;
            }
            if (line.startsWith(":")) continue; // a heartbeat
            const c = line.indexOf(":");
            const name = c < 0 ? line : line.slice(0, c);
            let v = c < 0 ? "" : line.slice(c + 1);
            if (v.startsWith(" ")) v = v.slice(1);
            if (name === "event") event = v;
            else if (name === "data") data.push(v);
            else if (name === "id" && !v.includes("\0")) lastId = v;
            else if (name === "retry" && /^\d+$/.test(v)) retryMs = Number(v);
          }
          if (Date.now() >= deadline) throw new DeadlinePassed();
        }
      } catch (e) {
        if (e instanceof DeadlinePassed || Date.now() >= deadline) throw new DeadlinePassed();
        if (timedOut && !alive) throw new StreamUnavailable(new DatagoatError({ status: 503, code: "network_error", detail: "the event stream sent nothing", remedy: `poll("${taskId}")` }));
        // anything else is a dropped connection: resume below
      } finally {
        clearTimeout(timer);
        reader.cancel().catch(() => undefined);
      }
      empty = alive ? 0 : empty + 1;
      if (empty >= EVENTS_MAX_EMPTY_RECONNECTS) throw new StreamUnavailable(new DatagoatError({ status: 503, code: "network_error", detail: "the event stream keeps closing", remedy: `poll("${taskId}")` }));
    }
  }

  /** REST answers come whole; should one ever arrive paged, fetch the rest (in the same format) and join it. */
  private async whole(out: Answer, format?: PageFormat): Promise<Answer> {
    let cursor = (out.page as { next_cursor?: string } | undefined)?.next_cursor;
    if (out.status !== "done" || !cursor) return out;
    const pages: Answer[] = [];
    while (cursor) {
      const p = await this.page(cursor, format);
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

  /** The next page of a paged answer (answers over MCP are paged; REST answers come whole). The
   *  format is chosen per call: the same cursor gives full or compact cases. */
  async page(cursor: string, response_format?: PageFormat) {
    return this.call<Answer>("page", { cursor, ...(response_format !== undefined ? { response_format: pageFormat(response_format) } : {}) });
  }

  /** Fetch an export or answer link (valid 24 hours). The link is its own credential: no key is sent.
   *  Returns the Response, so a large file can be streamed (`res.body`) or read (`await res.text()`). */
  async download(url: string): Promise<Response> {
    const r = await this.f(url, { method: "GET" });
    if (!r.ok) {
      const json = (await r.json().catch(() => ({}))) as Record<string, unknown>;
      throw errorFor({ status: r.status, code: String(json.code ?? "http_error"), detail: String(json.detail ?? r.statusText), remedy: String(json.remedy ?? "ask again for a new link"), ...(json.doc_url ? { doc_url: String(json.doc_url) } : {}) });
    }
    return r;
  }

  addDataset(body: DataSource & { dataset_id?: string; upload?: true; filename?: string }) { return this.call("add-dataset", body); }

  /**
   * Upload a CSV, a gzip-compressed CSV or a Parquet file of any size and return its dataset_id.
   * `source` is a file path (Node), bytes or a Blob. A gzip or Parquet file (recognised by its
   * bytes) goes as byte slices joined verbatim, and the engine reads it. By default the file goes in pieces to this client's own base URL (api.datagoat.io): no
   * other host is contacted, so it works where only the API host is reachable (an agent's sandbox,
   * a locked-down network). Each piece is at most 4 MB, cut at a row boundary with the header on
   * every piece; a failed piece is sent again, and the server ignores a piece it already has.
   * Redirects are not followed. `transport: "presigned"` instead PUTs the whole file to the
   * presigned upload_url on the storage host.
   */
  async uploadFile(source: string | Uint8Array | ArrayBuffer | Blob, o: { filename?: string; transport?: "pieces" | "presigned"; onPiece?: (n: number) => void } = {}): Promise<string> {
    const transport = o.transport ?? "pieces";
    if (transport !== "pieces" && transport !== "presigned") throw new Error('transport is "pieces" or "presigned"');
    const { bytes, name } = await readSource(source, o.filename);
    // A gzip or Parquet file goes as its own bytes, cut anywhere and joined verbatim (the engine
    // reads it); a CSV is cut at row boundaries with the header on every piece.
    const format = binaryFormat(bytes);
    // Cut (and check) the whole file before a dataset is made for it.
    const pieces = transport !== "pieces" ? [] : format ? byteSlices(bytes) : csvPieces(bytes);
    if (pieces.length > MAX_PIECES) throw new Error(`the file needs more than ${MAX_PIECES} pieces of ${PIECE_MAX_BYTES.toLocaleString("en-US")} bytes; upload it with transport: "presigned"`);
    const d = await this.addDataset({ upload: true, filename: name });
    if (transport === "presigned") {
      const r = await this.f(String(d.upload_url), { method: "PUT", headers: { "content-type": format ? "application/octet-stream" : "text/csv" }, body: bytes as unknown as BodyInit });
      if (!r.ok) throw new DatagoatError({ status: r.status, code: "upload_failed", detail: `the presigned PUT answered ${r.status}`, remedy: "upload the file again" });
      return String(d.dataset_id);
    }
    const url = d.upload_pieces_url;
    if (typeof url !== "string" || !url.includes("/v1/uploads/")) {
      throw new DatagoatError({ status: 502, code: "upload_pieces_unavailable", detail: "the server returned no upload_pieces_url", remedy: 'upload with transport: "presigned"' });
    }
    // The token is the credential; the pieces go to THIS client's host, never to another one.
    const token = url.replace(/\/$/, "").split("/").pop()!;
    const at = `${this.base}/v1/uploads/${encodeURIComponent(token)}`;
    const session = crypto.randomUUID().replace(/-/g, "");
    for (let i = 0; i < pieces.length; i++) {
      await this.sendPiece(`${at}/${i}`, pieces[i]!, format ? "application/octet-stream" : "text/csv", session);
      o.onPiece?.(i + 1);
    }
    await this.sendPiece(`${at}/complete`, JSON.stringify({ pieces: pieces.length }), "application/json", session);
    return String(d.dataset_id);
  }

  /** POST one piece, again after a dropped connection, a 429 or a 5xx (a repeated piece is a no-op
   *  on the server). No key is sent: the upload link is the credential. A redirect is an error, so
   *  no byte of the file goes to a host the caller did not name. */
  private async sendPiece(url: string, body: Uint8Array | string, contentType: string, session: string): Promise<Record<string, unknown>> {
    for (let attempt = 0; ; attempt++) {
      let wait: number | undefined;
      let r: Response | null = null;
      try {
        r = await this.f(url, { method: "POST", redirect: "manual", headers: { "content-type": contentType, "x-upload-session": session }, body: body as unknown as BodyInit });
      } catch (e) {
        if (attempt >= UPLOAD_RETRIES) throw new DatagoatError({ status: 503, code: "network_error", detail: String((e as Error).message), remedy: "check the connection, then upload the file again" });
      }
      if (r) {
        if (r.type === "opaqueredirect" || (r.status >= 300 && r.status < 400)) {
          const where = r.headers.get("location");
          throw new DatagoatError({ status: r.status || 302, code: "upload_redirect_refused", detail: `the upload was redirected${where ? ` (to ${where})` : ""}; pieces are sent only to ${this.base}`, remedy: "check baseUrl: it must be the API itself" });
        }
        const json = (await r.json().catch(() => ({}))) as Record<string, unknown>;
        if (r.ok) return json;
        const err = errorFor({ status: r.status, code: String(json.code ?? "http_error"), detail: String(json.detail ?? r.statusText), remedy: String(json.remedy ?? "send the file again"), ...(json.field ? { field: String(json.field) } : {}), ...(json.request_id ? { request_id: String(json.request_id) } : {}) });
        if (![429, 502, 503, 504].includes(r.status) || attempt >= UPLOAD_RETRIES) throw err;
        wait = retryAfterMs(r, json);
      }
      await this.sleep(wait === undefined ? backoffMs(attempt + 1) : Math.min(wait, 60_000));
    }
  }
  /** Delete a stored dataset now; otherwise it is deleted 24 hours after its last use. */
  deleteDataset(dataset_id: string) { return this.call<{ dataset_id: string; deleted: true }>("delete-dataset", { dataset_id }); }
  /** A task's status, then its answer; "compact" shows a finished answer's cases compact, as ask does. */
  async poll(task_id: string, response_format?: PageFormat) {
    return this.whole(await this.call<Answer>("poll", { task_id, ...(response_format !== undefined ? { response_format: pageFormat(response_format) } : {}) }), response_format);
  }
  /**
   * Is the table worth asking about? Free. entity_column is reported as an identifier, never a predictor.
   * @deprecated Served until contract 2.0.0, when dg_preflight is removed. `map({ sources: [dataset_id] })`
   * on the same table reports the same and more (resolutions, fitness, blocking, advisory, case_variants).
   */
  preflight(dataset_id: string, o: { outcome_column?: string; entity_column?: string; predictors?: string[] } = {}) { return this.call("preflight", { dataset_id, ...stated(o) }); }
  /**
   * Record what really happened. Up to 10,000 rows (the API's most per call) go in ONE call,
   * written whole or not at all: a bad row writes nothing and throws ValidationError
   * (invalid_outcomes) whose `errors` index every bad row. A longer list goes in chunks of
   * `chunkSize`; each chunk is atomic, but chunks before a failing one stay written. Any error from
   * a later chunk (validation, rate limit, server, network) says so: `problem.detail` names the
   * rows already written, `writtenBefore` counts them, and `errors` index rows in `outcomes` as
   * given. Resending is safe when every row has an event_id. With `partial: true` the valid rows
   * are written and `results` lists every row by its index in `outcomes`.
   */
  async reportOutcomes(model_ref: string, outcomes: Array<{ entity_id: string; outcome: boolean | number | string; observed_at: string; event_id?: string }>, o: { namespace?: string; partial?: boolean; chunkSize?: number } = {}) {
    const { chunkSize = 10_000, partial, namespace } = o;
    if (!outcomes.length) throw new Error("no outcomes");
    if (!(chunkSize >= 1 && chunkSize <= 10_000)) throw new Error("chunkSize is 1 to 10,000");
    const total: { written: number; duplicates: number; results?: Array<Record<string, unknown>> } = { written: 0, duplicates: 0 };
    const results: Array<Record<string, unknown>> = [];
    for (let start = 0; start < outcomes.length; start += chunkSize) {
      let out: Record<string, unknown>;
      try {
        out = await this.call("report-outcomes", { model_ref, outcomes: outcomes.slice(start, start + chunkSize), ...stated({ namespace, partial }) });
      } catch (e) {
        if (!(e instanceof DatagoatError) || !start) throw e;
        // A later chunk failed: rows 0..start-1 were written. Same class, indices in the whole
        // list, and the rows already written named.
        const Cls = e.constructor as new (p: Problem) => DatagoatError;
        const err = new Cls({
          ...e.problem,
          detail: `${e.problem.detail} (rows 0 to ${start - 1} were already sent and written: ${total.written} written, ${total.duplicates} duplicate; rows ${start} onward were not)`,
          ...(e.problem.errors ? { errors: e.problem.errors.map((x) => (typeof x.index === "number" ? { ...x, index: x.index + start } : x)) } : {}),
        });
        err.retryAfterMs = e.retryAfterMs;
        err.writtenBefore = start;
        throw err;
      }
      total.written += Number(out.written ?? 0);
      total.duplicates += Number(out.duplicates ?? 0);
      for (const r of (out.results as Array<Record<string, unknown>> | undefined) ?? []) results.push(typeof r.index === "number" ? { ...r, index: r.index + start } : r);
    }
    if (partial) total.results = results;
    return total;
  }
  drift(model_ref: string, o: { namespace?: string } = {}) { return this.call("drift", { model_ref, ...stated(o) }); }
  /** Which yes/no questions a table could be asked, and whether each is worth asking. Free; fits nothing. */
  suggest(o: { data: DataSource; entity_column: string; time_column?: string; include_categories?: boolean }) { return this.call("suggest", o); }
  /** Map a table, or one or two event logs with at most one table, into a record. Free. Without
   *  `answers` it proposes and stores nothing: `questions` lists each slot where two or more
   *  candidates survive, with its options, and those choices are the user's to make. With
   *  `answers` it confirms: `mapping_id`, `dataset_id`, `record` and a ready-to-run `ask` (add
   *  `subject_kind`). `mapping_id` alone replays a confirm; `closed_since` adds `closed`. A string
   *  source is a dataset_id; `horizon` is days (1, 7, 30, 60 or 90). Never retried on a 5xx: a
   *  confirm stores a mapping (RETRY_SAFE). */
  map(o: {
    sources?: Array<MapSource | string>; answers?: MapAnswers; outcome_words?: string[]; horizon?: 1 | 7 | 30 | 60 | 90 | { value: number; unit: "days" };
    snapshot_every?: "1d" | "1w" | "4w"; as_of?: string; mapping_id?: string; closed_since?: string;
  }) {
    if (!o.sources && !o.mapping_id) throw new Error("map needs sources (a proposal or a confirm) or mapping_id (a replay)");
    const { sources, horizon, ...rest } = o;
    return this.call<Record<string, unknown>>("map", {
      ...(sources ? { sources: sources.map((s) => (typeof s === "string" ? { dataset_id: s } : s)) } : {}),
      ...(horizon !== undefined ? { horizon: typeof horizon === "number" ? { value: horizon, unit: "days" } : horizon } : {}),
      ...stated(rest),
    });
  }
  /** Keep a model answering until `days` (1 to 365) from now. */
  extendModel(model_ref: string, days: number, o: { namespace?: string } = {}) { return this.call<{ model_ref: string; model_expires_at: string }>("extend-model", { model_ref, days, ...stated(o) }); }
  /** Delete a model now; its model_ref stops answering. */
  deleteModel(model_ref: string, o: { namespace?: string } = {}) { return this.call<{ model_ref: string; deleted: true }>("delete-model", { model_ref, ...stated(o) }); }
  /** Keep a confirmed mapping answered on a cadence (daily, weekly or monthly: a calendar month). Each run
   *  fetches the mapping's sources again, rebuilds the record, fits (refit_of from the second run; skipped
   *  when the labelled readings are unchanged), reads drift, scores today's open cases and reports the
   *  outcomes that closed, each step an ordinary call with idempotency_key "<run_id>:<step>". Returns
   *  `schedule` and the first run's `watch_url`. Needs a live key with "Can report outcomes"
   *  (schedule_key_required) and fetch_url sources (schedule_sources_not_refetchable). Not retried on a
   *  5xx (see RETRY_SAFE). describe() lists schedules. */
  schedule(mapping_id: string, o: { cadence: "daily" | "weekly" | "monthly"; subject_kind: string; acknowledge_decision_support?: boolean; namespace?: string }) {
    return this.call<{ schedule: Record<string, unknown> & { schedule_id: string; state: string; next_run_at: string | null }; watch_url?: string }>("schedule", {
      mapping_id, cadence: o.cadence, subject_kind: o.subject_kind, ...(o.acknowledge_decision_support ? { acknowledge_decision_support: true } : {}), ...(o.namespace !== undefined ? { namespace: o.namespace } : {}),
    });
  }
  /** End a schedule now: no further run; its stored key id, scopes and encrypted headers are deleted. */
  deleteSchedule(schedule_id: string) { return this.call<{ schedule_id: string; deleted: true }>("delete-schedule", { schedule_id }); }
  /** Record that you acted on a case through one of its levers. Returns compliant, dose_fraction and evaluated_feature. */
  attest(a: { model_ref: string; entity_id: string; lever_token: string; post_value: number | string | boolean | null; acted_at: string; event_id?: string; namespace?: string }) { return this.call("attest", a); }
  /** Did acting work? Outcomes of cases acted on vs not, once each group has 30 with an outcome. */
  evidence(model_ref: string, o: { namespace?: string } = {}) { return this.call("evidence", { model_ref, ...stated(o) }); }
  /** How the model's earlier calls held up against the outcomes reported for them. */
  trackRecord(model_ref: string, o: { namespace?: string } = {}) { return this.call("track-record", { model_ref, ...stated(o) }); }
  /** A namespace's profile: with words, exclude, fixed or display it replaces it; without, it reads it.
   *  fixed: columns an action never changes (tenure, age): still read by the model, and no lever is offered on them. */
  profile(o: { namespace?: string; words?: { case?: string; outcome?: string; columns?: Record<string, string> }; exclude?: string[]; fixed?: string[]; display?: "chance" | "bands" } = {}) {
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
