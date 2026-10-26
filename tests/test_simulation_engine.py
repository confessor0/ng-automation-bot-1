import math
import unittest
from datetime import datetime, timedelta, timezone

from src.adaptive_strategy import build_recommendation
from src.simulation_engine import black76, calculate, implied_volatility, scenario_surface


class SimulationEngineTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 25, 10, 0, tzinfo=timezone.utc)
        self.expiry = self.now + timedelta(days=30)

    def test_black76_respects_put_call_parity(self):
        call = black76(300, 295, 30 / 365, 0.065, 0.35, "CE")
        put = black76(300, 295, 30 / 365, 0.065, 0.35, "PE")
        expected = math.exp(-0.065 * 30 / 365) * (300 - 295)
        self.assertAlmostEqual(call["price"] - put["price"], expected, places=4)
        self.assertGreater(call["time_value"], 0)
        self.assertLess(call["theta"], 0)

    def test_implied_volatility_round_trip(self):
        premium = black76(300, 300, 30 / 365, 0.065, 0.42, "CE")["price"]
        solved = implied_volatility(premium, 300, 300, 30 / 365, 0.065, "CE")
        self.assertAlmostEqual(solved, 0.42, places=3)

    def test_expiry_basket_pnl_and_greeks(self):
        payload = {
            "mode": "EXPIRY", "underlying_price": 320, "lot_size": 1250,
            "implied_volatility": 35, "interest_rate": 6.5,
            "legs": [
                {"side": "BUY", "instrument": "FUT", "entry": 300, "qty": 1},
                {"side": "BUY", "instrument": "OPT", "option_type": "PE", "strike": 310, "entry": 12, "qty": 1},
            ],
        }
        result = calculate(payload, self.expiry, self.expiry)
        self.assertEqual(result["gross_pnl"], 10000.0)
        self.assertEqual(result["legs"][1]["time_value"], 0)
        self.assertIn("delta", result["greeks"])

    def test_target_date_changes_option_value_with_time(self):
        payload = {"mode": "TARGET_DATE", "underlying_price": 300, "lot_size": 1250,
                   "implied_volatility": 35, "interest_rate": 6.5,
                   "legs": [{"side": "BUY", "instrument": "OPT", "option_type": "CE",
                              "strike": 300, "entry": 10, "qty": 1}]}
        today = calculate(payload, self.now, self.expiry)
        later = calculate(payload, self.now + timedelta(days=20), self.expiry)
        self.assertGreater(today["legs"][0]["mark_price"], later["legs"][0]["mark_price"])

    def test_slippage_reduces_pnl_and_capital_limit_warns(self):
        base = {"mode": "EXPIRY", "underlying_price": 310, "lot_size": 1250,
                "implied_volatility": 35, "interest_rate": 6.5, "capital_available": 100,
                "risk_limits": {"max_capital_usage_percent": 10},
                "legs": [{"side": "BUY", "instrument": "FUT", "entry": 300, "qty": 1}]}
        clean = calculate({**base, "charges": {"slippage_percent": 0, "margin_rate": 12}}, self.expiry, self.expiry)
        slipped = calculate({**base, "charges": {"slippage_percent": 1, "margin_rate": 12}}, self.expiry, self.expiry)
        self.assertLess(slipped["net_pnl"], clean["net_pnl"])
        self.assertTrue(any("capital-usage" in warning for warning in slipped["warnings"]))

    def test_scenario_surface_contains_expiry_and_breakevens(self):
        payload = {"mode": "TARGET_DATE", "underlying_price": 300, "lot_size": 1250,
                   "implied_volatility": 35, "interest_rate": 6.5, "points": 41,
                   "legs": [{"side": "BUY", "instrument": "OPT", "option_type": "CE",
                              "strike": 300, "entry": 10, "qty": 1}]}
        result = scenario_surface(payload, self.now, self.expiry)
        self.assertEqual(result["dates"][-1], self.expiry.isoformat())
        self.assertTrue(any(abs(value - 310) < 1 for value in result["break_evens"]))
        self.assertEqual(len(result["matrix"]), 7)

    def test_canonical_strategy_uses_itm_hedges(self):
        symbol = lambda _base, kind, expiry, strike, opt: f"{kind}-{expiry}-{strike}-{opt}"
        quote = lambda _symbol: 10.0
        bullish = build_recommendation(trend="BULLISH", signal_status="TEST", cmp=293,
                                       atm_strike=295, expiry="26NOV", quote=quote,
                                       symbol_builder=symbol)
        bearish = build_recommendation(trend="BEARISH", signal_status="TEST", cmp=293,
                                       atm_strike=295, expiry="26NOV", quote=quote,
                                       symbol_builder=symbol)
        self.assertEqual((bullish["legs"][0]["strike"], bullish["legs"][0]["option_type"]), (300, "PE"))
        self.assertEqual((bearish["legs"][0]["strike"], bearish["legs"][0]["option_type"]), (290, "CE"))

    def test_neutral_without_quotes_still_returns_reviewable_basket(self):
        recommendation = build_recommendation(trend="NEUTRAL", signal_status="TEST", cmp=300,
                                              atm_strike=300, expiry="26NOV",
                                              quote=lambda _symbol: 0,
                                              symbol_builder=lambda *_args: "SYMBOL")
        self.assertEqual(len(recommendation["legs"]), 4)
        self.assertFalse(recommendation["trade_approved"])
        self.assertEqual(recommendation["status"], "CONSIDER")

    def test_strategy_two_can_be_primary_for_a_directional_market(self):
        recommendation = build_recommendation(
            trend="BULLISH", signal_status="ADMIN_STRATEGY_2", cmp=300,
            atm_strike=300, expiry="26NOV", consolidation=True,
            quote=lambda _symbol: 8.0,
            symbol_builder=lambda _base, kind, expiry, strike, option_type: f"{kind}-{expiry}-{strike}-{option_type}",
        )
        self.assertEqual(recommendation["strategy"], "STRATEGY_2")
        self.assertEqual(recommendation["direction"], "NEUTRAL")
        self.assertEqual([(leg["action"], leg["strike"], leg["option_type"]) for leg in recommendation["legs"]], [
            ("SELL", 300, "PE"), ("SELL", 300, "CE"), ("BUY", 285, "PE"), ("BUY", 315, "CE")
        ])


if __name__ == "__main__":
    unittest.main()
