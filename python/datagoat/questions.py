"""Question and shape builders for `Client.ask`, as plain dicts.

Each builder sends only what you state. `outcome_is_desirable` is never filled in for you: it says
whether the outcome is one you want, and a default would decide that for you. It is optional
everywhere except `choice`, where "the best option" means nothing without it.

    yesno   the chance the outcome happens for each case
    score   that chance as a level: unlikely | possible | likely | very_likely by default
    choice  the best option: the likeliest to give an outcome you want, the least likely to give
            one you avoid; each answer also carries most_likely and least_likely
    rank    cases in order of the chance
"""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Sequence


def _stated(**kw: Any) -> Dict[str, Any]:
    return {k: (list(v) if isinstance(v, tuple) else v) for k, v in kw.items() if v is not None}


def yesno(outcome_column: str, *, outcome_is_desirable: Optional[bool] = None,
          positive_values: Optional[Sequence[str]] = None, refit_of: Optional[str] = None) -> Dict[str, Any]:
    """The chance `outcome_column` comes out yes for each case."""
    return {"type": "yesno", **_stated(outcome_column=outcome_column, outcome_is_desirable=outcome_is_desirable,
                                       positive_values=positive_values, refit_of=refit_of)}


def score(outcome_column: str, *, levels: Optional[Sequence[str]] = None, cuts: Optional[Sequence[float]] = None,
          outcome_is_desirable: Optional[bool] = None, positive_values: Optional[Sequence[str]] = None,
          refit_of: Optional[str] = None) -> Dict[str, Any]:
    """The chance as a named level. `levels` lowest first; `cuts` one fewer, ascending, in (0, 1).
    Omit both for unlikely < 0.25 <= possible < 0.50 <= likely < 0.75 <= very_likely."""
    return {"type": "score", **_stated(outcome_column=outcome_column, levels=levels, cuts=cuts,
                                       outcome_is_desirable=outcome_is_desirable,
                                       positive_values=positive_values, refit_of=refit_of)}


def choice(*, outcome_is_desirable: bool, option_column: Optional[str] = None, options: Optional[Sequence[str]] = None,
           outcome_column: Optional[str] = None, option_outcomes: Optional[Mapping[str, str]] = None,
           positive_values: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """The best option for each case. Two forms:

    - `option_column` + `options` + `outcome_column`: which value of a column you control (a
      contract, a team, a channel) is best for this case. Refused as `option_not_in_pattern` when
      the record shows the column makes no difference.
    - `option_outcomes`: {option: outcome_column}, one learned outcome per option.

    `outcome_is_desirable` is required: the best option is the likeliest to give an outcome you
    want and the least likely to give one you avoid."""
    if not isinstance(outcome_is_desirable, bool):
        raise TypeError("choice needs outcome_is_desirable=True or False")
    if (option_outcomes is None) == (option_column is None):
        raise ValueError("choice takes EITHER option_column + options + outcome_column, OR option_outcomes")
    return {"type": "choice", **_stated(option_column=option_column, options=options,
                                        outcome_column=outcome_column,
                                        option_outcomes=dict(option_outcomes) if option_outcomes else None,
                                        outcome_is_desirable=outcome_is_desirable,
                                        positive_values=positive_values)}


def rank(outcome_column: str, *, top_k: Optional[int] = None, outcome_is_desirable: Optional[bool] = None,
         positive_values: Optional[Sequence[str]] = None, refit_of: Optional[str] = None) -> Dict[str, Any]:
    """The cases (or, with no cases, the whole record) in order of the chance, highest first."""
    return {"type": "rank", **_stated(outcome_column=outcome_column, top_k=top_k,
                                      outcome_is_desirable=outcome_is_desirable,
                                      positive_values=positive_values, refit_of=refit_of)}


def from_model(model_ref: str, *, type: str = "yesno", levels: Optional[Sequence[str]] = None,
               cuts: Optional[Sequence[float]] = None, top_k: Optional[int] = None) -> Dict[str, Any]:
    """Answer from an existing model instead of fitting: its outcome, yes values and
    outcome_is_desirable are the model's. type is yesno, score or rank. Pass the cases as rows (no
    record needed), or send a newer record and name cases by id."""
    if type not in ("yesno", "score", "rank"):
        raise ValueError("a model answers yesno, score or rank; choice is answered from its record")
    return {"type": type, "model_ref": model_ref, **_stated(levels=levels, cuts=cuts, top_k=top_k)}


# -- record shapes ------------------------------------------------------------------ #
# Pass one of these as `shape=` (with `time_column=`) when the record is not one row per case.

def _shape(kind: str, **fields: Any) -> Dict[str, Any]:
    return {"kind": kind, **{k: v for k, v in fields.items() if v is not None}}


UNITS = ("minutes", "hours", "days", "weeks", "steps")


def events(*, label: Mapping[str, Any], event_column: Optional[str] = None, value_columns: Optional[Sequence[str]] = None,
           horizon: Optional[int] = None, unit: Optional[str] = None, lookback: Optional[Sequence[int]] = None,
           horizon_days: Optional[int] = None, lookback_days: Optional[Sequence[int]] = None, as_of: Optional[str] = None) -> Dict[str, Any]:
    """An event log. label is {"lapsed": True} or {"name": ..., "when": predicate}.

    horizon + unit (minutes, hours, days, weeks, steps) is the outcome window at the end of the log, e.g.
    horizon=24, unit="hours" names the outcome lapsed_24h (or {name}_next_24h); steps is a whole-number
    time column. lookback is the two activity windows before it, in the same unit. horizon_days /
    lookback_days are the older days-only form; use one or the other."""
    if (horizon is None) != (unit is None):
        raise ValueError("horizon and unit go together, e.g. horizon=24, unit='hours'")
    if unit is not None and unit not in UNITS:
        raise ValueError(f"unit must be one of {', '.join(UNITS)} (months vary in length: use 30, 90 or 365 days)")
    return _shape("events", label=dict(label), event_column=event_column, value_columns=list(value_columns) if value_columns else None,
                  horizon={"value": horizon, "unit": unit} if horizon is not None else None,
                  lookback=list(lookback) if lookback else None,
                  horizon_days=horizon_days, lookback_days=list(lookback_days) if lookback_days else None, as_of=as_of)


def series(*, value_columns: Sequence[str], windows: Sequence[int], time_unit: Optional[str] = None) -> Dict[str, Any]:
    """A time series with an outcome on each row. windows: [3, 6], [7, 30] or [4, 12] periods. time_unit="steps"
    when the time column is a whole number rather than a time."""
    return _shape("series", value_columns=list(value_columns), windows=list(windows), time_unit=time_unit)


def panel(*, trend_of: Optional[str] = None, window_of: Optional[str] = None, direction: Optional[str] = None,
          alpha: Optional[float] = None, periods: Optional[int] = None, agg: Optional[str] = None,
          min_history: Optional[int] = None) -> Dict[str, Any]:
    """Periods per case. The outcome is a significant trend in `trend_of`, or `window_of` over the final periods."""
    if (trend_of is None) == (window_of is None):
        raise ValueError("a panel reads its outcome from trend_of OR window_of")
    label = ({"trend_of": trend_of, "direction": direction, "alpha": alpha} if trend_of
             else {"window_of": window_of, "periods": periods, "agg": agg})
    return _shape("panel", label={k: v for k, v in label.items() if v is not None}, min_history=min_history)


def signals(*, signal_columns: Sequence[str], windows: Sequence[int], snapshot_every: str, horizon: int,
            event_column: Optional[str] = None, event_start_column: Optional[str] = None, event_end_column: Optional[str] = None,
            min_history: Optional[int] = None, as_of: Optional[str] = None) -> Dict[str, Any]:
    """Sensor readings with event intervals in the same table. snapshot_every: 15min, 1h, 6h, 1d or 1w. The outcome: an
    event starts within `horizon` snapshots (3 at 6h is 18 hours)."""
    return _shape("signals", signal_columns=list(signal_columns), windows=list(windows), snapshot_every=snapshot_every, horizon=horizon,
                  event_column=event_column, event_start_column=event_start_column, event_end_column=event_end_column,
                  min_history=min_history, as_of=as_of)


def traces(*, agent_column: Optional[str] = None, task_column: Optional[str] = None, tool_column: Optional[str] = None) -> Dict[str, Any]:
    """A log of runs, one row per run, with a yes/no outcome column."""
    return _shape("traces", agent_column=agent_column, task_column=task_column, tool_column=tool_column)


def snapshots(*, snapshot_id_column: str, snapshot_time_column: str, dataset_id: Optional[str] = None,
              rows: Optional[Sequence[Mapping[str, Any]]] = None, csv: Optional[str] = None,
              fetch_url: Optional[str] = None, outcome_time_column: Optional[str] = None,
              event_column: Optional[str] = None, value_columns: Optional[Sequence[str]] = None,
              lookback_days: Optional[Sequence[int]] = None) -> Dict[str, Any]:
    """An event log read as of moments you choose. The record (`data`) is the log; the snapshot table
    (exactly one of dataset_id, rows, csv, fetch_url) has one row per case per moment, with the same
    entity column, a snapshot id, the snapshot's time and the outcome. lookback_days: [30, 90],
    [7, 30] or [90, 365]. Whole cases are held out."""
    table = {k: v for k, v in {"dataset_id": dataset_id, "rows": list(rows) if rows is not None else None,
                               "csv": csv, "fetch_url": fetch_url}.items() if v is not None}
    if len(table) != 1:
        raise ValueError("the snapshot table is exactly one of dataset_id, rows, csv, fetch_url")
    return _shape("snapshots", snapshots=table, snapshot_id_column=snapshot_id_column,
                  snapshot_time_column=snapshot_time_column, outcome_time_column=outcome_time_column,
                  event_column=event_column, value_columns=list(value_columns) if value_columns else None,
                  lookback_days=list(lookback_days) if lookback_days else None)
