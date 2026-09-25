/**
 * Check a Verdict's signature yourself, with no call to Datagoat. Uses the platform WebCrypto
 * (Node 20+, current browsers); no dependencies.
 *
 *   const keys = await fetchKeys();                                  // once; keep it
 *   await verifyOffline(v.verdict, v.signature, keys);               // "valid" | "invalid_signature" | "expired" | "unknown_key"
 *
 * A Verdict is signed with Ed25519 as a detached JWS (`b64: false`): the signed bytes are the
 * protected header, a dot, and the Verdict as compact JSON with its keys sorted, written the way
 * the engine (Python) writes it. The statuses are the same as `Datagoat.verify`.
 */
export const JWKS_URL = "https://api.datagoat.io/.well-known/jwks.json";

export type VerifyStatus = "valid" | "invalid_signature" | "expired" | "unknown_key";
export interface Jwk { kty: string; crv: string; kid: string; x: string; alg?: string; use?: string }
export interface Jwks { keys: Jwk[] }

const b64d = (s: string): Uint8Array<ArrayBuffer> => {
  const b = atob(s.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (s.length % 4)) % 4));
  const out = new Uint8Array(b.length);
  for (let i = 0; i < b.length; i++) out[i] = b.charCodeAt(i);
  return out;
};

/** A number as Python's json.dumps writes it: an integer as digits; a float by its shortest
 *  repr, in fixed notation from 1e-4 to under 1e16 and otherwise as 1.5e-05 / 1e+16. */
export function pyNumber(n: number): string {
  if (!Number.isFinite(n)) throw new TypeError("a Verdict holds only finite numbers");
  if (Number.isInteger(n) && Math.abs(n) < 1e16) return String(n);
  const [mant, expStr] = n.toExponential().split("e");
  const exp = Number(expStr);
  if (exp >= -4 && exp < 16) return String(n); // JavaScript also writes this range in fixed notation
  const e = Math.abs(exp) < 10 ? `0${Math.abs(exp)}` : String(Math.abs(exp));
  return `${mant}e${exp < 0 ? "-" : "+"}${e}`;
}

function pyString(s: string, ascii: boolean): string {
  const j = JSON.stringify(s);
  if (!ascii) return j;
  return j.replace(/[^\x00-\x7f]/g, (c) => `\\u${c.charCodeAt(0).toString(16).padStart(4, "0")}`);
}

/** The signed text: keys sorted, no spaces, Python's escaping (`ascii`) or raw UTF-8. */
export function canonical(v: unknown, ascii = true): string {
  if (v === null) return "null";
  if (typeof v === "boolean") return v ? "true" : "false";
  if (typeof v === "number") return pyNumber(v);
  if (typeof v === "string") return pyString(v, ascii);
  if (Array.isArray(v)) return `[${v.map((x) => canonical(x, ascii)).join(",")}]`;
  if (typeof v === "object") {
    const o = v as Record<string, unknown>;
    // Python sorts by code point, as does a plain sort of JavaScript's UTF-16 strings for the ASCII keys a Verdict uses.
    return `{${Object.keys(o).sort().map((k) => `${pyString(k, ascii)}:${canonical(o[k], ascii)}`).join(",")}}`;
  }
  throw new TypeError(`cannot sign a ${typeof v}`);
}

export async function fetchKeys(url = JWKS_URL, fetchImpl: typeof fetch = fetch): Promise<Jwks> {
  const r = await fetchImpl(url);
  if (!r.ok) throw new Error(`jwks: HTTP ${r.status}`);
  return (await r.json()) as Jwks;
}

/** valid | invalid_signature | expired | unknown_key, with no network call. Pass the Verdict and
 *  signature exactly as Datagoat returned them. Never act on anything but valid. */
export async function verifyOffline(
  verdict: Record<string, unknown>,
  signature: Record<string, unknown> | null | undefined,
  keys: Jwks,
  now: Date = new Date(),
): Promise<VerifyStatus> {
  const prot = signature?.protected, sig = signature?.signature;
  if (typeof prot !== "string" || typeof sig !== "string") return "invalid_signature";
  let header: { alg?: string; b64?: boolean; kid?: string };
  try { header = JSON.parse(new TextDecoder().decode(b64d(prot))); } catch { return "invalid_signature"; }
  if (header.alg !== "EdDSA" || header.b64 !== false) return "invalid_signature";
  const kid = header.kid ?? (signature?.kid as string | undefined);
  const jwk = keys.keys.find((k) => k.kid === kid && k.crv === "Ed25519");
  if (!jwk) return "unknown_key";
  const key = await crypto.subtle.importKey("jwk", { kty: "OKP", crv: "Ed25519", x: jwk.x }, { name: "Ed25519" }, false, ["verify"]);
  const enc = new TextEncoder();
  let ok = false;
  for (const ascii of [true, false]) {
    const msg = enc.encode(`${prot}.${canonical(verdict, ascii)}`);
    if (await crypto.subtle.verify({ name: "Ed25519" }, key, b64d(sig), msg)) { ok = true; break; }
  }
  if (!ok) return "invalid_signature";
  const exp = verdict.expires_at;
  if (typeof exp === "string" && now.getTime() >= Date.parse(exp)) return "expired";
  return "valid";
}

