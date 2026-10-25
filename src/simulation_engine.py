"""Deterministic decision-support calculations for futures and options baskets.

The engine is intentionally broker independent. Market data is supplied by the
caller together with a source and timestamp, making every result reproducible.
Commodity options are valued with Black-76 because their underlying is a
futures contract. Nothing in this module can submit an order.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Dict, Iterable, List, Tuple


SECONDS_PER_YEAR = 365.0 * 24 * 60 * 60


def _number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def _cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def _pdf(value: float) -> float:
    return math.exp(-0.5 * value * value) / math.sqrt(2.0 * math.pi)


def _years_between(at: datetime, expiry: datetime) -> float:
    return max(0.0, (expiry - at).total_seconds() / SECONDS_PER_YEAR)


def black76(futures: float, strike: float, years: float, rate: float,
            volatility: float, option_type: str) -> Dict[str, float]:
    """Return Black-76 price and unit Greeks.

    ``volatility`` and ``rate`` are decimals (0.35 and 0.065). Vega is returned
    per one percentage-point volatility change and theta per calendar day.
    """
    futures, strike = max(_number(futures), 0.0), max(_number(strike), 0.0)
    years, volatility = max(_number(years), 0.0), max(_number(volatility), 0.0)
    rate = _number(rate)
    kind = str(option_type or "").upper()
    intrinsic = max(futures - strike, 0.0) if kind == "CE" else max(strike - futures, 0.0)
    if not futures or not strike or years <= 0 or volatility <= 0:
        delta = (1.0 if futures > strike else 0.0) if kind == "CE" else (-1.0 if futures < strike else 0.0)
        return {"price": intrinsic, "intrinsic": intrinsic, "time_value": 0.0,
                "delta": delta, "gamma": 0.0, "theta": 0.0, "vega": 0.0}
    root_t = math.sqrt(years)
    discount = math.exp(-rate * years)
    d1 = (math.log(futures / strike) + 0.5 * volatility * volatility * years) / (volatility * root_t)
    d2 = d1 - volatility * root_t
    if kind == "CE":
        price = discount * (futures * _cdf(d1) - strike * _cdf(d2))
        delta = discount * _cdf(d1)
    else:
        price = discount * (strike * _cdf(-d2) - futures * _cdf(-d1))
        delta = -discount * _cdf(-d1)
    gamma = discount * _pdf(d1) / (futures * volatility * root_t)
    vega = discount * futures * _pdf(d1) * root_t / 100.0
    shorter = max(0.0, years - (1.0 / 365.0))
    if shorter <= 0:
        next_price = intrinsic
    else:
        short_root = math.sqrt(shorter)
        short_discount = math.exp(-rate * shorter)
        short_d1 = (math.log(futures / strike) + 0.5 * volatility * volatility * shorter) / (volatility * short_root)
        short_d2 = short_d1 - volatility * short_root
        if kind == "CE":
            next_price = short_discount * (futures * _cdf(short_d1) - strike * _cdf(short_d2))
        else:
            next_price = short_discount * (strike * _cdf(-short_d2) - futures * _cdf(-short_d1))
    theta = next_price - price
    return {"price": max(0.0, price), "intrinsic": intrinsic,
            "time_value": max(0.0, price - discount * intrinsic), "delta": delta,
            "gamma": gamma, "theta": theta, "vega": vega}


def implied_volatility(market_price: float, futures: float, strike: float,
                       years: float, rate: float, option_type: str) -> float | None:
    """Solve Black-76 IV with a stable bounded bisection search."""
    target = _number(market_price, -1)
    if target < 0 or futures <= 0 or strike <= 0 or years <= 0:
        return None
    intrinsic = black76(futures, strike, 0, rate, 0, option_type)["price"]
    if target < intrinsic:
        return None
    low, high = 0.0001, 5.0
    for _ in range(80):
        mid = (low + high) / 2.0
        value = black76(futures, strike, years, rate, mid, option_type)["price"]
        if abs(value - target) < 0.0001:
            return mid
        if value < target:
            low = mid
        else:
            high = mid
    return (low + high) / 2.0


def normalize_legs(raw_legs: Iterable[dict]) -> Tuple[List[dict], List[str]]:
    legs, errors = [], []
    for index, source in enumerate(raw_legs or [], 1):
        side = str(source.get("side") or source.get("action") or "").upper()
        instrument = str(source.get("instrument") or source.get("type") or source.get("leg_type") or "OPT").upper()
        option_type = str(source.get("option_type") or source.get("opt") or "").upper()
        qty = _number(source.get("qty", source.get("quantity")))
        strike = _number(source.get("strike")) if instrument == "OPT" else None
        entry = _number(source.get("entry", source.get("entry_price", source.get("premium"))), -1)
        if side not in ("BUY", "SELL"):
            errors.append(f"Leg {index}: side must be BUY or SELL.")
        if instrument not in ("OPT", "FUT"):
            errors.append(f"Leg {index}: instrument must be OPT or FUT.")
        if qty <= 0:
            errors.append(f"Leg {index}: quantity must be greater than zero.")
        if entry < 0:
            errors.append(f"Leg {index}: entry price is required and cannot be negative.")
        if instrument == "OPT" and (option_type not in ("CE", "PE") or not strike):
            errors.append(f"Leg {index}: option needs a valid strike and CE/PE type.")
        legs.append({
            "seq": source.get("seq", index), "role": source.get("role") or "Custom leg",
            "symbol": source.get("symbol") or source.get("trading_symbol") or f"LEG_{index}",
            "side": side, "instrument": instrument, "option_type": option_type or None,
            "strike": strike, "qty": qty, "entry_price": entry,
            "bid": source.get("bid"), "ask": source.get("ask"), "ltp": source.get("ltp"),
            "price_source": source.get("price_source") or source.get("data_quality") or "MANUAL",
            "quote_timestamp": source.get("quote_timestamp"), "expiry": source.get("expiry"),
            "iv": source.get("iv", source.get("implied_volatility")),
        })
    return legs, errors


def value_leg(leg: dict, futures_price: float, at: datetime, expiry: datetime,
              default_iv: float, rate: float, lot_size: float,
              manual_exit: float | None = None) -> dict:
    side_sign = 1.0 if leg["side"] == "BUY" else -1.0
    years = _years_between(at, expiry)
    if leg["instrument"] == "FUT":
        mark = futures_price if manual_exit is None else manual_exit
        unit = {"price": mark, "intrinsic": mark, "time_value": 0.0,
                "delta": 1.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0}
    elif manual_exit is not None:
        intrinsic = max(futures_price - leg["strike"], 0.0) if leg["option_type"] == "CE" else max(leg["strike"] - futures_price, 0.0)
        unit = {"price": manual_exit, "intrinsic": intrinsic,
                "time_value": max(0.0, manual_exit - intrinsic),
                "delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0}
    else:
        iv = _number(leg.get("iv"), default_iv)
        unit = black76(futures_price, leg["strike"], years, rate, iv, leg["option_type"])
    quantity_factor = side_sign * leg["qty"] * lot_size
    pnl = (unit["price"] - leg["entry_price"]) * quantity_factor
    result = dict(leg)
    result.update({
        "mark_price": round(unit["price"], 4), "intrinsic_value": round(unit["intrinsic"], 4),
        "time_value": round(unit["time_value"], 4), "gross_pnl": round(pnl, 2),
        "delta": round(unit["delta"] * quantity_factor, 4),
        "gamma": round(unit["gamma"] * quantity_factor, 6),
        "theta": round(unit["theta"] * quantity_factor, 2),
        "vega": round(unit["vega"] * quantity_factor, 2),
    })
    return result


def _charges(valued_legs: List[dict], assumptions: dict) -> Dict[str, float]:
    charges = assumptions.get("charges") or {}
    turnover = sum((leg["entry_price"] + leg["mark_price"]) * leg["qty"] * assumptions["lot_size"] for leg in valued_legs)
    brokerage = max(0.0, _number(charges.get("brokerage_per_lot"))) * sum(leg["qty"] for leg in valued_legs) * 2
    exchange = turnover * max(0.0, _number(charges.get("exchange_percent"))) / 100
    ctt = turnover * max(0.0, _number(charges.get("ctt_percent"))) / 100
    sebi = turnover * max(0.0, _number(charges.get("sebi_percent"))) / 100
    stamp = turnover * max(0.0, _number(charges.get("stamp_percent"))) / 100
    gst = (brokerage + exchange + sebi) * max(0.0, _number(charges.get("gst_percent"))) / 100
    total = brokerage + exchange + ctt + sebi + stamp + gst
    return {"turnover": round(turnover, 2), "brokerage": round(brokerage, 2),
            "exchange": round(exchange, 2), "ctt": round(ctt, 2),
            "sebi": round(sebi, 2), "stamp": round(stamp, 2),
            "gst": round(gst, 2), "total": round(total, 2)}


def calculate(payload: dict, at: datetime, expiry: datetime) -> Dict[str, Any]:
    legs, errors = normalize_legs(payload.get("legs") or [])
    if errors:
        raise ValueError(" | ".join(errors))
    underlying = _number(payload.get("underlying_price"))
    if underlying <= 0:
        raise ValueError("A positive futures/underlying price is required.")
    lot_size = _number(payload.get("lot_size"), 1)
    if lot_size <= 0:
        raise ValueError("Lot size must be greater than zero.")
    mode = str(payload.get("mode") or "TARGET_DATE").upper()
    volatility = max(0.0001, _number(payload.get("implied_volatility"), 35.0) / 100.0)
    volatility += _number(payload.get("volatility_change")) / 100.0
    volatility = max(0.0001, volatility)
    rate = _number(payload.get("interest_rate"), 6.5) / 100.0
    manual_marks = payload.get("manual_exit_prices") or {}
    slippage = max(0.0, _number((payload.get("charges") or {}).get("slippage_percent"))) / 100.0
    valued = []
    for leg in legs:
        manual = None
        if mode == "MANUAL_EXIT":
            manual = _number(manual_marks.get(str(leg["seq"]), manual_marks.get(leg["symbol"])), -1)
            if manual < 0:
                raise ValueError(f"Manual exit price is required for leg {leg['seq']}.")
        effective_at = expiry if mode == "EXPIRY" else at
        raw_entry = leg["entry_price"]
        leg["entry_price"] = raw_entry * (1 + slippage if leg["side"] == "BUY" else 1 - slippage)
        valued_leg = value_leg(leg, underlying, effective_at, expiry, volatility, rate, lot_size, manual)
        raw_mark = valued_leg["mark_price"]
        adjusted_mark = raw_mark * (1 - slippage if leg["side"] == "BUY" else 1 + slippage)
        direction = 1 if leg["side"] == "BUY" else -1
        valued_leg["raw_entry_price"] = round(raw_entry, 4)
        valued_leg["adjusted_entry_price"] = round(leg["entry_price"], 4)
        valued_leg["adjusted_mark_price"] = round(adjusted_mark, 4)
        valued_leg["gross_pnl"] = round((adjusted_mark - leg["entry_price"]) * direction * leg["qty"] * lot_size, 2)
        valued.append(valued_leg)
    assumptions = dict(payload)
    assumptions["lot_size"] = lot_size
    charge_data = _charges(valued, assumptions)
    gross = sum(leg["gross_pnl"] for leg in valued)
    net = gross - charge_data["total"]
    if valued:
        for leg in valued:
            leg["contribution_percent"] = round(leg["gross_pnl"] / gross * 100, 2) if gross else 0.0
    greeks = {name: round(sum(leg[name] for leg in valued), 4) for name in ("delta", "gamma", "theta", "vega")}
    entry_cashflow = sum((1 if leg["side"] == "SELL" else -1) * leg["entry_price"] * leg["qty"] * lot_size for leg in valued)
    margin_rate = max(0.0, _number((payload.get("charges") or {}).get("margin_rate"), 12.0)) / 100.0
    margin = sum(abs(leg["entry_price"] * leg["qty"] * lot_size) for leg in valued) * margin_rate
    warnings = risk_warnings(valued, at, expiry, payload)
    stop_loss = max(0.0, _number(payload.get("stop_loss_amount")))
    target_profit = max(0.0, _number(payload.get("target_profit_amount")))
    if stop_loss and net <= -stop_loss:
        warnings.append("Selected scenario reaches the configured stop-loss amount.")
    if target_profit and net >= target_profit:
        warnings.append("Selected scenario reaches the configured profit target.")
    return {"mode": mode, "underlying_price": round(underlying, 4),
            "as_of": at.isoformat(), "expiry": expiry.isoformat(), "lot_size": lot_size,
            "legs": valued, "gross_pnl": round(gross, 2), "net_pnl": round(net, 2),
            "net_premium": round(entry_cashflow, 2), "charges": charge_data,
            "estimated_margin": round(margin, 2), "greeks": greeks,
            "warnings": warnings, "data_quality": data_quality(valued)}


def risk_warnings(legs: List[dict], at: datetime, expiry: datetime, payload: dict) -> List[str]:
    warnings = []
    missing_entries = [x for x in legs if _number(x.get("raw_entry_price", x.get("entry_price"))) <= 0]
    if missing_entries:
        warnings.append(
            f"{len(missing_entries)} leg(s) have no positive entry limit. "
            "The displayed P&L is theoretical until those prices are entered or refreshed."
        )
    for short in [x for x in legs if x["side"] == "SELL" and x["instrument"] == "OPT"]:
        protected = any(x["side"] == "BUY" and x["option_type"] == short["option_type"] and
                        ((short["option_type"] == "CE" and x["strike"] > short["strike"]) or
                         (short["option_type"] == "PE" and x["strike"] < short["strike"])) for x in legs)
        if not protected:
            warnings.append(f"Short {short['option_type']} {short['strike']:g} is not protected by a detected wing.")
    if at >= expiry:
        warnings.append("Evaluation time is at or after expiry; all option time value is zero.")
    if any(x.get("price_source") in ("MISSING", "MANUAL_PRICE_REQUIRED") for x in legs):
        warnings.append("One or more legs have missing market prices.")
    limits = payload.get("risk_limits") or {}
    max_spread = max(0.0, _number(limits.get("max_bid_ask_spread_percent"), 20.0))
    max_quote_age = max(0.0, _number(limits.get("max_quote_age_minutes"), 15.0))
    for leg in legs:
        bid, ask = _number(leg.get("bid")), _number(leg.get("ask"))
        midpoint = (bid + ask) / 2 if bid and ask else 0
        if midpoint and ((ask - bid) / midpoint * 100) > max_spread:
            warnings.append(f"{leg['symbol']} bid/ask spread exceeds {max_spread:g}%.")
        timestamp = leg.get("quote_timestamp")
        if timestamp:
            try:
                quoted_at = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
                if quoted_at.tzinfo is None:
                    quoted_at = quoted_at.replace(tzinfo=at.tzinfo)
                age_minutes = max(0.0, (at - quoted_at.astimezone(at.tzinfo)).total_seconds() / 60)
                if age_minutes > max_quote_age:
                    warnings.append(f"{leg['symbol']} quote is {age_minutes:.0f} minutes old.")
            except (TypeError, ValueError):
                warnings.append(f"{leg['symbol']} has an invalid quote timestamp.")
    capital = _number(payload.get("capital_available"))
    max_capital = max(0.0, _number(limits.get("max_capital_usage_percent"), 80.0))
    margin_rate = max(0.0, _number((payload.get("charges") or {}).get("margin_rate"), 12.0)) / 100
    estimated_margin = sum(abs(x["entry_price"] * x["qty"] * _number(payload.get("lot_size"), 1)) for x in legs) * margin_rate
    if capital and estimated_margin / capital * 100 > max_capital:
        warnings.append(f"Estimated margin exceeds the {max_capital:g}% capital-usage limit.")
    return warnings


def data_quality(legs: List[dict]) -> Dict[str, Any]:
    counts: Dict[str, int] = {}
    for leg in legs:
        source = str(leg.get("price_source") or "UNKNOWN")
        counts[source] = counts.get(source, 0) + 1
    missing_entry_count = sum(
        1 for leg in legs
        if _number(leg.get("raw_entry_price", leg.get("entry_price"))) <= 0
    )
    missing_symbol_count = sum(
        1 for leg in legs if not str(leg.get("symbol") or "").strip()
        or str(leg.get("symbol") or "").startswith("LEG_")
    )
    incomplete_source = any(key in counts for key in ("MISSING", "MANUAL_PRICE_REQUIRED"))
    return {"price_sources": counts, "mixed_sources": len(counts) > 1,
            "missing_entry_count": missing_entry_count,
            "missing_symbol_count": missing_symbol_count,
            "complete": not incomplete_source and missing_entry_count == 0 and missing_symbol_count == 0}


def scenario_surface(payload: dict, at: datetime, expiry: datetime) -> Dict[str, Any]:
    reference = _number(payload.get("underlying_price"))
    range_percent = min(80.0, max(5.0, _number(payload.get("range_percent"), 30.0)))
    points = min(241, max(21, int(_number(payload.get("points"), 81))))
    low, high = reference * (1 - range_percent / 100), reference * (1 + range_percent / 100)
    price_points = [low + i * (high - low) / (points - 1) for i in range(points)]
    offsets = payload.get("day_offsets") or [0, 1, 3, 7]
    dates = []
    from datetime import timedelta
    for offset in offsets:
        value = min(expiry, at + timedelta(days=max(0, int(offset))))
        if value not in dates:
            dates.append(value)
    if expiry not in dates:
        dates.append(expiry)
    curves, matrix = [], []
    for date_value in dates:
        curve = []
        for price in price_points:
            scenario_payload = dict(payload)
            scenario_payload["underlying_price"] = price
            scenario_payload["mode"] = "EXPIRY" if date_value >= expiry else "TARGET_DATE"
            result = calculate(scenario_payload, date_value, expiry)
            curve.append({"price": round(price, 2), "pnl": result["net_pnl"]})
        curves.append({"at": date_value.isoformat(), "points": curve})
    selected_moves = sorted(set([-.20, -.10, -.05, 0, .05, .10, .20]))
    for move in selected_moves:
        row = {"move_percent": round(move * 100, 2), "price": round(reference * (1 + move), 2), "values": []}
        for date_value in dates:
            item = dict(payload); item["underlying_price"] = row["price"]
            item["mode"] = "EXPIRY" if date_value >= expiry else "TARGET_DATE"
            row["values"].append(calculate(item, date_value, expiry)["net_pnl"])
        matrix.append(row)
    expiry_curve = curves[-1]["points"]
    break_evens = []
    for left, right in zip(expiry_curve, expiry_curve[1:]):
        if left["pnl"] == 0:
            break_evens.append(left["price"])
        elif left["pnl"] * right["pnl"] < 0:
            ratio = -left["pnl"] / (right["pnl"] - left["pnl"])
            break_evens.append(round(left["price"] + ratio * (right["price"] - left["price"]), 2))
    years = _years_between(at, expiry)
    sigma = max(0.0001, (_number(payload.get("implied_volatility"), 35.0) + _number(payload.get("volatility_change"))) / 100)
    def terminal_cdf(price: float) -> float:
        if price <= 0 or years <= 0:
            return 0.0 if price < reference else 1.0
        z = (math.log(price / reference) + 0.5 * sigma * sigma * years) / (sigma * math.sqrt(years))
        return _cdf(z)
    probability_profit = 0.0
    for index, point in enumerate(expiry_curve):
        if point["pnl"] < 0:
            continue
        left_edge = 0.0 if index == 0 else (expiry_curve[index - 1]["price"] + point["price"]) / 2
        right_edge = float("inf") if index == len(expiry_curve) - 1 else (point["price"] + expiry_curve[index + 1]["price"]) / 2
        probability_profit += (1.0 if math.isinf(right_edge) else terminal_cdf(right_edge)) - terminal_cdf(left_edge)
    max_profit = max(point["pnl"] for point in expiry_curve)
    max_loss = min(point["pnl"] for point in expiry_curve)
    return {"curves": curves, "matrix": matrix, "dates": [x.isoformat() for x in dates],
            "break_evens": sorted(set(break_evens)), "range": [round(low, 2), round(high, 2)],
            "probability_of_profit_pct": round(min(100.0, max(0.0, probability_profit * 100)), 2),
            "risk_reward_in_range": round(max_profit / abs(max_loss), 3) if max_loss < 0 else None,
            "scope_label": f"Estimated within {range_percent:g}% displayed range"}
