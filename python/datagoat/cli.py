"""`datagoat` on the command line.

  datagoat signup                 a free test key for the sample datasets, saved for later calls
  datagoat sample                 ask about sample:saas_churn and verify every Verdict
  datagoat describe               question types, prices and the samples
  datagoat ask QUESTIONS --data DATASET_ID --entity COLUMN [--cases IDS] [--kind org]
                                  e.g. datagoat ask '{"churn":{"type":"yesno","outcome_column":"churned"}}' \\
                                       --data sample:saas_churn --entity customer_id --cases cust_0001,cust_0002
  datagoat verify VERDICT.json SIGNATURE.json [--offline]
                                  --offline checks the signature here, against the published keys
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List, Optional

from .client import CREDENTIALS, Client, DatagoatError, register, saved_key
from .questions import score, yesno


def _save(key: str) -> None:
    CREDENTIALS.parent.mkdir(parents=True, exist_ok=True)
    CREDENTIALS.write_text(key + "\n")
    os.chmod(CREDENTIALS, 0o600)


def _print_and_verify(dg: Client, out: dict) -> int:
    print(json.dumps(out, indent=2))
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
    a.add_argument("--cases", help="comma-separated ids; omit to rank the record")
    a.add_argument("--kind", default="org", choices=["person", "org", "object", "event", "other"])
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
        if args.cmd == "sample":
            out = dg.ask({"churn": yesno("churned", outcome_is_desirable=False),
                          "risk": score("churned", outcome_is_desirable=False)},
                         dataset_id="sample:saas_churn", entity_column="customer_id",
                         subject_kind="org", cases={"ids": ["cust_0001"]})
            return _print_and_verify(dg, out)
        if args.cmd == "ask":
            cases = {"ids": [c.strip() for c in args.cases.split(",") if c.strip()]} if args.cases else None
            out = dg.ask(json.loads(args.questions), dataset_id=args.data, entity_column=args.entity,
                         subject_kind=args.kind, cases=cases)
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
