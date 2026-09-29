"""`datagoat` on the command line.

  datagoat signup                 a free test key for the sample datasets, saved for later calls
  datagoat sample                 ask about sample:saas_churn and verify every Verdict
  datagoat describe               question types, prices and the samples
  datagoat ask QUESTIONS --data DATASET_ID --entity COLUMN [--cases IDS | --open] [--kind org]
                                  [--watch | --events]
                                e.g. datagoat ask '{"churn":{"type":"yesno","outcome_column":"churned"}}' \\
                                       --data sample:saas_churn --entity customer_id --cases cust_0001,cust_0002
                                  --watch draws each stage the server reports while a fit runs (a
                                  checklist with datagoat[watch] on a terminal, else plain lines; on
                                  stderr); --events prints every event, then the answer, as JSON lines
  datagoat map SOURCE [SOURCE ...] [--outcome WORDS] [--horizon DAYS] [--every 1d|1w|4w]
                                  [--as-of ISO] [--answers JSON]
  datagoat map --mapping-id ID [--closed-since ISO]
                                  map a table, or event logs with a table, into a record (free).
                                  SOURCE is a dataset_id, a sample id or an https URL. Without
                                  --answers it proposes: each question it lists is yours to answer,
                                  e.g. --answers '{"outcome":{"offer_id":"outcome_time:parley_billing:cancelled_at"}}'
                                  ('{}' takes every single survivor). --mapping-id replays a confirm
  datagoat backtest DATASET_ID [--every 1d|1w|4w] [--last N] [--baseline single_column|none]
                                  [--kind org] [--watch]
                                  what a record map built from a log supported in its own past:
                                  a fit at each past cutoff, graded on what came next beside a naive
                                  rule; prints the result and checks its Verdict (kind backtest)
  datagoat schedule MAPPING_ID --cadence daily|weekly|monthly [--kind org]
  datagoat schedule --delete SCHEDULE_ID
                                  keep a confirmed map (every source an https URL) answered on a
                                  cadence: each run fetches the sources again, refits, reads drift,
                                  scores today's open cases and reports the outcomes that closed;
                                  needs a live key with "Can report outcomes"
  datagoat upload FILE [--presigned]
                                  store a CSV, .csv.gz or Parquet file of any size and print its
                                  dataset_id; the file goes
                                  in pieces to api.datagoat.io only (--presigned: one PUT to the
                                  storage host instead)
  datagoat verify VERDICT.json SIGNATURE.json [--offline]
                                  --offline checks the signature here, against the published keys
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List, Optional, TextIO

from .client import CREDENTIALS, Client, DatagoatError, register, saved_key
from .questions import score, yesno


def _save(key: str) -> None:
    CREDENTIALS.parent.mkdir(parents=True, exist_ok=True)
    CREDENTIALS.write_text(key + "\n")
    os.chmod(CREDENTIALS, 0o600)


def say(text: str, file: Optional[TextIO] = None) -> None:
    """Print a line that survives any console encoding: a character the console cannot show (on a
    Windows cp1252 console, 退会) is written escaped rather than raising after the ask is billed."""
    f = file or sys.stdout
    try:
        print(text, file=f, flush=True)
    except UnicodeEncodeError:
        enc = getattr(f, "encoding", None) or "ascii"
        print(text.encode(enc, "backslashreplace").decode(enc), file=f, flush=True)


def _print_and_verify(dg: Client, out: dict) -> int:
    say(json.dumps(out, indent=2))
    ok = dg.verify_all(out)
    print("verify:", "valid" if ok else "INVALID (do not act on an unverified Verdict)")
    return 0 if ok else 1


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="datagoat", description="Ask questions about cases.")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("signup")
    s.add_argument("--force", action="store_true", help="replace a saved key")
    sub.add_parser("sample")
    sub.add_parser("describe")
    a = sub.add_parser("ask")
    a.add_argument("questions")
    a.add_argument("--data", required=True, help="a dataset_id or sample id")
    a.add_argument("--entity", required=True, help="the column naming each case")
    which = a.add_mutually_exclusive_group()
    which.add_argument("--cases", help="comma-separated ids; omit to rank the record")
    which.add_argument("--open", action="store_true", help="today's open cases, found by the engine (e.g. the record dg_map built)")
    a.add_argument("--time", help="the time column (needed to hold out the most recent cases, e.g. snapshot_at)")
    a.add_argument("--group", help="the column naming the case each row belongs to (whole cases held out)")
    a.add_argument("--kind", default="org", choices=["person", "org", "object", "event", "other"])
    shown = a.add_mutually_exclusive_group()
    shown.add_argument("--watch", action="store_true", help="draw each stage the server reports while a fit runs (stderr)")
    shown.add_argument("--events", action="store_true", help="print every event, then the answer, as JSON lines")
    m = sub.add_parser("map")
    m.add_argument("sources", nargs="*", help="dataset ids, sample ids or https URLs (one table, or one or two logs with at most one table)")
    m.add_argument("--outcome", help="the outcome in your words, comma-separated (e.g. cancel)")
    m.add_argument("--horizon", type=int, choices=[1, 7, 30, 60, 90], help="days ahead the outcome is looked for (default 30)")
    m.add_argument("--every", choices=["1d", "1w", "4w"], help="how often each case is read (default 1w)")
    m.add_argument("--as-of", dest="as_of", help="the moment the record is read at (ISO-8601)")
    m.add_argument("--answers", help="JSON answers to the proposal's questions; confirms the mapping")
    m.add_argument("--mapping-id", dest="mapping_id", help="replay a confirmed mapping")
    m.add_argument("--closed-since", dest="closed_since", help="with --mapping-id: also list the outcomes known since (ISO-8601)")
    b = sub.add_parser("backtest")
    b.add_argument("dataset_id", help="a dataset_id a map confirm returned (a record built from a log), or sample:parley_record")
    b.add_argument("--every", choices=["1d", "1w", "4w"], help="how far apart the cutoffs are (default: the record's own snapshot_every)")
    b.add_argument("--last", type=int, choices=range(3, 13), metavar="3..12", help="how many cutoffs (default 6)")
    b.add_argument("--baseline", choices=["single_column", "none"], help="the naive rule graded beside the model (default single_column)")
    b.add_argument("--kind", default="org", choices=["person", "org", "object", "event", "other"])
    b.add_argument("--watch", action="store_true", help="draw each stage the server reports while it walks forward (stderr)")
    sc = sub.add_parser("schedule")
    sc.add_argument("mapping_id", nargs="?", help="a confirmed mapping whose sources are all https URLs")
    sc.add_argument("--cadence", choices=["daily", "weekly", "monthly"], help="how often it runs (monthly: a calendar month)")
    sc.add_argument("--kind", default="org", choices=["person", "org", "object", "event", "other"])
    sc.add_argument("--delete", metavar="SCHEDULE_ID", help="delete this schedule instead")
    u = sub.add_parser("upload")
    u.add_argument("file")
    u.add_argument("--presigned", action="store_true", help="PUT the file to the presigned storage URL instead of sending pieces to the API")
    v = sub.add_parser("verify")
    v.add_argument("verdict")
    v.add_argument("signature")
    v.add_argument("--offline", action="store_true", help="check the signature locally, with no call to Datagoat")
    args = p.parse_args(argv)

    try:
        if args.cmd == "signup":
            if saved_key() and not args.force:
                print(f"A key is already saved in {CREDENTIALS}. Use --force to replace it.")
                return 0
            r = register()
            _save(r["api_key"])
            print(f"Saved a test key to {CREDENTIALS}. It works on the sample datasets only.")
            print("For your own data, create a live key at https://datagoat.io/keys and set DATAGOAT_API_KEY.")
            return 0
        if args.cmd == "describe":
            print(json.dumps(Client(api_key="dgk_test_none").describe(), indent=2))
            return 0
        if args.cmd == "verify":
            with open(args.verdict) as f1, open(args.signature) as f2:
                verdict, signature = json.load(f1), json.load(f2)
            if args.offline:
                from .verify import fetch_keys, verify_offline
                status = verify_offline(verdict, signature, fetch_keys())
            else:
                status = Client(api_key="dgk_test_none").verify(verdict, signature)
            print(status)
            return 0 if status == "valid" else 1
        dg = Client()
        if args.cmd == "map":
            if not args.sources and not args.mapping_id:
                print("map needs sources, or --mapping-id to replay", file=sys.stderr)
                return 2
            sources = [{"fetch_url": x} if x.startswith("https://") else {"dataset_id": x} for x in args.sources] or None
            out = dg.map(sources, answers=json.loads(args.answers) if args.answers is not None else None,
                         outcome_words=[w.strip() for w in args.outcome.split(",") if w.strip()] if args.outcome else None,
                         horizon=args.horizon, snapshot_every=args.every, as_of=args.as_of,
                         mapping_id=args.mapping_id, closed_since=args.closed_since)
            say(json.dumps(out, indent=2))
            if out.get("status") == "proposed" and out.get("questions"):
                # The choices are the user's: listed here, never made for them.
                for q in out["questions"]:
                    where = f" ({q['source']})" if q.get("source") else ""
                    print(f"question {q['slot']}{where}: {' | '.join(q['options'])}", file=sys.stderr)
                print("answer each with --answers to confirm", file=sys.stderr)
            return 0
        if args.cmd == "schedule":
            if args.delete:
                say(json.dumps(dg.delete_schedule(args.delete), indent=2))
                return 0
            if not args.mapping_id or not args.cadence:
                print("schedule needs MAPPING_ID and --cadence (or --delete SCHEDULE_ID)", file=sys.stderr)
                return 2
            out = dg.schedule(args.mapping_id, cadence=args.cadence, subject_kind=args.kind)
            say(json.dumps(out, indent=2))
            if out.get("watch_url"):
                print(f"first run: {out['watch_url']}", file=sys.stderr)
            return 0
        if args.cmd == "backtest":
            kw = dict(subject_kind=args.kind, every=args.every, last=args.last, baseline=args.baseline)
            if args.watch:
                from .watch import make_watch
                w = make_watch()
                try:
                    out = dg.backtest(args.dataset_id, on_progress=w.on_progress, on_event=w.on_event, **kw)
                finally:
                    w.close()
            else:
                out = dg.backtest(args.dataset_id, **kw)
            say(json.dumps(out, indent=2))
            # One Verdict of kind backtest: never act on an unverified one.
            ok = isinstance(out.get("verdict"), dict) and dg.verify(out["verdict"], out.get("signature")) == "valid"
            print("verify:", "valid" if ok else "INVALID (do not act on an unverified Verdict)")
            return 0 if ok else 1
        if args.cmd == "sample":
            out = dg.ask({"churn": yesno("churned", outcome_is_desirable=False),
                          "risk": score("churned", outcome_is_desirable=False)},
                         dataset_id="sample:saas_churn", entity_column="customer_id",
                         subject_kind="org", cases={"ids": ["cust_0001"]})
            return _print_and_verify(dg, out)
        if args.cmd == "upload":
            def landed(i: int) -> None:
                print(f"piece {i} sent", file=sys.stderr, flush=True)
            print(dg.upload_file(args.file, transport="presigned" if args.presigned else "pieces", on_piece=landed))
            return 0
        if args.cmd == "ask":
            cases = ({"open": True} if args.open
                     else {"ids": [c.strip() for c in args.cases.split(",") if c.strip()]} if args.cases else None)
            kw = dict(dataset_id=args.data, entity_column=args.entity, subject_kind=args.kind, cases=cases)
            if args.time:
                kw["time_column"] = args.time
            if args.group:
                kw["group_column"] = args.group
            if args.events:
                def line(obj: dict) -> None:
                    say(json.dumps(obj))  # ASCII escapes: valid JSON on any console
                # Stream events arrive through on_event; without a stream, the pending poll bodies.
                out = dg.ask(json.loads(args.questions), on_event=line,
                             on_progress=lambda ev: line(ev) if ev.get("status") == "pending" else None, **kw)
                line(out)
                ok = dg.verify_all(out)
                print("verify:", "valid" if ok else "INVALID (do not act on an unverified Verdict)", file=sys.stderr)
                return 0 if ok else 1
            if args.watch:
                from .watch import make_watch
                w = make_watch()
                try:
                    out = dg.ask(json.loads(args.questions), on_progress=w.on_progress, on_event=w.on_event, **kw)
                finally:
                    w.close()
                return _print_and_verify(dg, out)
            out = dg.ask(json.loads(args.questions), **kw)
            return _print_and_verify(dg, out)
    except DatagoatError as e:
        print(f"{e.problem.code}: {e.problem.detail}\n{e.problem.remedy}", file=sys.stderr)
        return 1
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
