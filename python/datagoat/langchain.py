"""LangChain tools: `from datagoat.langchain import datagoat_tools` then give them to an agent."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .client import Client


def datagoat_tools(dg: Client) -> List[Any]:
    from langchain_core.tools import tool

    @tool
    def dg_ask(questions: Dict[str, Any], entity_column: str, subject_kind: str, dataset_id: Optional[str] = None,
               rows: Optional[List[Dict[str, Any]]] = None, cases: Optional[Dict[str, Any]] = None,
               time_column: Optional[str] = None, shape: Optional[Dict[str, Any]] = None,
               acknowledge_decision_support: bool = False) -> Dict[str, Any]:
        """Answers yesno, score, choice and rank questions about cases from a record of past outcomes.
        The record is a table, or with shape (and time_column) an event log, series, panel, signals or traces.
        Each answer has a state: answered, refused (the record holds no reliable pattern; the same call
        returns the same refusal) or not_yet. choice needs outcome_is_desirable."""
        return dg.ask(questions, entity_column=entity_column, subject_kind=subject_kind, dataset_id=dataset_id,
                      rows=rows, cases=cases, time_column=time_column, shape=shape,
                      acknowledge_decision_support=acknowledge_decision_support)

    @tool
    def dg_verify(verdict: Dict[str, Any], signature: Dict[str, Any]) -> str:
        """Check a Verdict is genuine and unaltered: valid | invalid_signature | expired | unknown_key."""
        return dg.verify(verdict, signature)

    @tool
    def dg_report_outcomes(model_ref: str, outcomes: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Record what really happened for cases answered under model_ref: [{entity_id, outcome, observed_at, event_id}]."""
        return dg.report_outcomes(model_ref, outcomes)

    @tool
    def dg_attest(model_ref: str, entity_id: str, lever_token: str, post_value: Any, acted_at: str,
                  event_id: Optional[str] = None) -> Dict[str, Any]:
        """Records that a case was acted on through one of its levers (the lever_token from its levers) and the
        feature's value afterwards; the engine judges compliance. Returns compliant, dose_fraction, evaluated_feature."""
        return dg.attest(model_ref, entity_id, lever_token, post_value, acted_at, event_id=event_id)

    @tool
    def dg_evidence(model_ref: str) -> Dict[str, Any]:
        """Compares reported outcomes of cases acted on (compliant attestations) with cases not acted on;
        live is null until each group has 30 cases with an outcome."""
        return dg.evidence(model_ref)

    @tool
    def dg_describe() -> Dict[str, Any]:
        """Question types, record shapes, prices, limits and the free sample records with ready-to-run asks."""
        return dg.describe()

    return [dg_ask, dg_verify, dg_report_outcomes, dg_attest, dg_evidence, dg_describe]
