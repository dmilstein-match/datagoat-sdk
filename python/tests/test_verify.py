"""Offline Verdict verification against a real production Verdict (tests/fixtures/verdict.json)."""
import copy
import datetime as dt
import json
from pathlib import Path

from datagoat.verify import verify_offline

FX = json.loads((Path(__file__).parent / "fixtures" / "verdict.json").read_text())
BEFORE_EXPIRY = dt.datetime(2026, 10, 1, tzinfo=dt.timezone.utc)


def test_real_verdict_is_valid():
    assert verify_offline(FX["verdict"], FX["signature"], FX["jwks"], now=BEFORE_EXPIRY) == "valid"


def test_one_changed_number_is_invalid():
    v = copy.deepcopy(FX["verdict"])
    v["entities"][0]["score"] = 0.95
    assert verify_offline(v, FX["signature"], FX["jwks"], now=BEFORE_EXPIRY) == "invalid_signature"


def test_changed_reason_is_invalid():
    v = copy.deepcopy(FX["verdict"])
    v["entities"][0]["drivers"][0]["likelihood_direction"] = "lower"
    assert verify_offline(v, FX["signature"], FX["jwks"], now=BEFORE_EXPIRY) == "invalid_signature"


def test_expired_after_expires_at():
    later = dt.datetime(2027, 1, 1, tzinfo=dt.timezone.utc)
    assert verify_offline(FX["verdict"], FX["signature"], FX["jwks"], now=later) == "expired"


def test_unknown_key():
    assert verify_offline(FX["verdict"], FX["signature"], {"keys": []}, now=BEFORE_EXPIRY) == "unknown_key"


def test_missing_or_broken_signature():
    assert verify_offline(FX["verdict"], None, FX["jwks"]) == "invalid_signature"
    bad = dict(FX["signature"], protected="bm90LWpzb24")
    assert verify_offline(FX["verdict"], bad, FX["jwks"]) == "invalid_signature"
