import assert from "node:assert/strict";
import { test } from "node:test";
import { Datagoat, DatagoatError, PIECE_MAX_BYTES, binaryFormat, byteSlices, csvPieces, uploadName } from "../src/index.ts";

const enc = new TextEncoder();
const dec = new TextDecoder();

/** A 15 MB CSV with quoted fields that hold commas, quotes and line breaks. */
function bigCsv(target: number): Uint8Array {
  const parts: string[] = ["customer_id,plan,note,seats,churned\r\n"];
  let size = parts[0]!.length;
  for (let i = 0; size < target; i++) {
    const line = `c${String((i * 7919) % 10_000_000).padStart(7, "0")},${["basic", "pro", "équipe"][i % 3]},"said ""hi""\nthen left, maybe",${(i * 31) % 500},${i % 3 === 0 ? 1 : 0}\r\n`;
    parts.push(line);
    size += enc.encode(line).length;
  }
  return enc.encode(parts.join(""));
}

/** The API's side of the token walk, reached ONLY at https://api.datagoat.io; any other host fails
 *  the way a sandbox's egress rule would. */
function fakeApi(opts: { failOnce?: string; redirect?: boolean; piecesHost?: string } = {}) {
  const requests: Array<{ method: string; url: string; auth: string | null }> = [];
  let header: string | null = null;
  let session: string | null = null;
  const rows: string[] = [];
  let completed: number | null = null;
  let failed = false;
  const f = (async (input: string, init: RequestInit = {}) => {
    const url = new URL(input);
    const headers = new Headers(init.headers);
    requests.push({ method: init.method ?? "GET", url: input, auth: headers.get("authorization") });
    if (url.host !== "api.datagoat.io") throw new TypeError(`fetch failed: egress to ${url.host} is blocked`);
    const json = (b: unknown, status = 200, h: Record<string, string> = {}) => new Response(JSON.stringify(b), { status, headers: { "content-type": "application/json", ...h } });
    if (url.pathname === "/v1/add-dataset") {
      return json({ dataset_id: "ds_1", status: "awaiting_upload", rows: null, columns: null, upload_url: "https://bucket.s3.amazonaws.com/put?sig=x", upload_method: "PUT",
        upload_page: "https://datagoat.io/upload/v1.tok.mac", upload_pieces_url: `${opts.piecesHost ?? "https://api.datagoat.io"}/v1/uploads/v1.tok.mac` });
    }
    assert.ok(url.pathname.startsWith("/v1/uploads/v1.tok.mac/"), url.pathname);
    if (opts.redirect) return new Response(null, { status: 307, headers: { location: "https://elsewhere.example/v1/uploads/x/0" } });
    assert.equal(headers.get("authorization"), null, "the link is the credential; no key travels with a piece");
    assert.equal(init.redirect, "manual");
    const idx = url.pathname.split("/").pop()!;
    if (idx === opts.failOnce && !failed) { failed = true; return json({ code: "engine_unavailable", detail: "d", remedy: "r" }, 503); }
    const s = headers.get("x-upload-session")!;
    assert.match(s, /^[0-9a-f]{32}$/);
    const body = init.body as Uint8Array | string;
    if (idx === "complete") {
      assert.equal(s, session);
      completed = JSON.parse(String(body)).pieces;
      return json({ dataset_id: "ds_1", status: "open", pieces: rows.length });
    }
    const bytes = typeof body === "string" ? enc.encode(body) : body;
    assert.ok(bytes.length <= PIECE_MAX_BYTES);
    const text = dec.decode(bytes);
    const cut = text.indexOf("\n");
    const i = Number(idx);
    if (i === 0) { header = text.slice(0, cut); session = s; }
    assert.equal(s, session);
    assert.equal(text.slice(0, cut), header, "the header rides on every piece");
    if (i < rows.length) return json({ pieces: rows.length });
    assert.equal(i, rows.length, "pieces arrive in order");
    rows.push(text.slice(cut + 1));
    return json({ dataset_id: "ds_1", status: "awaiting_upload", pieces: rows.length });
  }) as unknown as typeof fetch;
  return { f, requests, rows, get header() { return header; }, get completed() { return completed; } };
}

test("uploadFile: 15 MB in pieces with only api.datagoat.io reachable; a failed piece is sent again", async () => {
  const file = bigCsv(15_500_000);
  assert.ok(file.length >= 15_000_000);
  const api = fakeApi({ failOnce: "2" });
  const dg = new Datagoat({ apiKey: "dgk_live_x", fetch: api.f });
  dg.sleep = async () => {};
  const seen: number[] = [];
  const id = await dg.uploadFile(file, { filename: "big export.csv", onPiece: (n) => seen.push(n) });
  assert.equal(id, "ds_1");
  const text = dec.decode(file);
  const cut = text.indexOf("\r\n");
  assert.equal(api.header, text.slice(0, cut));
  assert.equal(api.rows.join(""), text.slice(cut + 2), "the pieces carry every row, whole and in order");
  assert.ok(api.rows.length >= 5);
  assert.equal(api.completed, api.rows.length);
  assert.deepEqual(seen, api.rows.map((_, i) => i + 1));
  assert.ok(api.requests.every((r) => new URL(r.url).host === "api.datagoat.io"), "nothing went to another host");
  assert.equal(api.requests.filter((r) => r.url.endsWith("/2")).length, 2, "the failed piece was retried");
});

test("uploadFile: the pieces go to the client's own host even when the link names another; a redirect is refused", async () => {
  const api = fakeApi({ piecesHost: "https://elsewhere.example" });
  await new Datagoat({ apiKey: "k", fetch: api.f }).uploadFile(enc.encode("id,y\n1,0\n"));
  assert.ok(api.requests.every((r) => new URL(r.url).host === "api.datagoat.io"));
  const bounced = fakeApi({ redirect: true });
  await assert.rejects(new Datagoat({ apiKey: "k", fetch: bounced.f }).uploadFile(enc.encode("id,y\n1,0\n")),
    (e: DatagoatError) => e.problem.code === "upload_redirect_refused");
  assert.ok(bounced.requests.every((r) => new URL(r.url).host === "api.datagoat.io"), "nothing followed the redirect");
});

test("uploadFile: presigned is opt-in; bad files are refused before a dataset is made", async () => {
  const api = fakeApi();
  const dg = new Datagoat({ apiKey: "k", fetch: api.f });
  await assert.rejects(dg.uploadFile(enc.encode("id,y\n")), /header row and at least one row/);
  await assert.rejects(dg.uploadFile(new Uint8Array([0x69, 0x64, 0x0a, 0xe9, 0x0a])), /UTF-8/);
  await assert.rejects(dg.uploadFile(enc.encode("id\n1\n"), { transport: "s3" as never }), /transport/);
  assert.equal(api.requests.length, 0);
  // Presigned goes to the storage host: in a sandbox that host is unreachable, which is why it is opt-in.
  await assert.rejects(dg.uploadFile(enc.encode("id,y\n1,0\n"), { transport: "presigned" }), /egress/);
  assert.equal(new URL(api.requests.at(-1)!.url).host, "bucket.s3.amazonaws.com");
});

test("csvPieces: lossless, bounded, never inside quotes; uploadName is what the API accepts", () => {
  const src = "\ufeffid,note\r\n" + Array.from({ length: 400 }, (_, i) => `${i},"a\nb, ""c"""\r\n`).join("");
  const pieces = csvPieces(enc.encode(src), 300).map((p) => dec.decode(p));
  assert.ok(pieces.length > 10 && pieces.every((p) => p.startsWith("id,note\n") && enc.encode(p).length <= 300));
  assert.equal(pieces.map((p) => p.slice("id,note\n".length)).join(""), src.slice("\ufeffid,note\r\n".length));
  assert.deepEqual(csvPieces(enc.encode("a,b\n1,2")).map((p) => dec.decode(p)), ["a,b\n1,2"]);
  assert.equal(uploadName("C:\\data\\my file (1).csv"), "my_file__1_.csv");
});

test("uploadFile: a gzip or Parquet file goes as byte slices with application/octet-stream", async () => {
  const { gzipSync } = await import("node:zlib");
  const gz = new Uint8Array(gzipSync(bigCsv(8_000_000), { level: 0 }));
  const pq = new Uint8Array([0x50, 0x41, 0x52, 0x31, ...new Uint8Array(5000).fill(7), 8, 0, 0, 0, 0x50, 0x41, 0x52, 0x31]);
  assert.equal(binaryFormat(gz), "gzip");
  assert.equal(binaryFormat(pq), "parquet");
  assert.equal(binaryFormat(enc.encode("PAR1,x\n1,2\n")), null, "a CSV that starts PAR1 is still CSV");
  for (const file of [gz, pq]) {
    const types = new Set<string>();
    const got: Uint8Array[] = [];
    const f = (async (input: string, init: RequestInit = {}) => {
      const url = new URL(input);
      if (url.host !== "api.datagoat.io") throw new TypeError("blocked");
      if (url.pathname === "/v1/add-dataset") return new Response(JSON.stringify({ dataset_id: "ds_2", upload_pieces_url: "https://api.datagoat.io/v1/uploads/v1.t.m" }));
      if (!url.pathname.endsWith("/complete")) { types.add(new Headers(init.headers).get("content-type")!); got.push(init.body as Uint8Array); }
      return new Response(JSON.stringify({ status: "awaiting_upload" }));
    }) as unknown as typeof fetch;
    assert.equal(await new Datagoat({ apiKey: "k", fetch: f }).uploadFile(file), "ds_2");
    assert.deepEqual([...types], ["application/octet-stream"]);
    assert.deepEqual(new Uint8Array(Buffer.concat(got)), file, "the pieces are the file, byte for byte");
    assert.ok(got.every((p) => p.length <= PIECE_MAX_BYTES));
  }
  assert.deepEqual(byteSlices(new Uint8Array(10), 4).map((s) => s.length), [4, 4, 2]);
});
