"""Check a stored Verdict with no network: a real Verdict and signature from Datagoat, with the key
they were signed with (in verdict.fixture.json). Change any number in it and it fails."""
import copy
import datetime as dt
import json
from pathlib import Path

from datagoat import verify_offline

fx = json.loads((Path(__file__).parent / "verdict.fixture.json").read_text())
when = dt.datetime(2026, 10, 1, tzinfo=dt.timezone.utc)   # a moment before this Verdict expires
print("as stored:", verify_offline(fx["verdict"], fx["signature"], fx["jwks"], now=when))
tampered = copy.deepcopy(fx["verdict"])
tampered["entities"][0]["score"] = 0.95
print("one number changed:", verify_offline(tampered, fx["signature"], fx["jwks"], now=when))
assert verify_offline(fx["verdict"], fx["signature"], fx["jwks"], now=when) == "valid"
assert verify_offline(tampered, fx["signature"], fx["jwks"], now=when) == "invalid_signature"
