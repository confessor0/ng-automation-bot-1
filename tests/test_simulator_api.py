import unittest
from unittest.mock import patch

from src.web_server import app


class SimulatorApiTests(unittest.TestCase):
    def setUp(self):
        app.config.update(TESTING=True)
        self.client = app.test_client()
        with self.client.session_transaction() as session:
            session["user_token"] = "simulator-test-token"

    def basket(self):
        return {
            "engine_version": 2, "symbol": "NATGASMINI", "underlying_price": 300,
            "atm_strike": 300, "lot_size": 1250, "as_of": "2026-10-25T10:00:00+05:30",
            "expiry": "2026-11-25T23:30:00+05:30", "mode": "TARGET_DATE",
            "implied_volatility": 35, "interest_rate": 6.5,
            "legs": [{"side": "BUY", "instrument": "OPT", "option_type": "CE",
                      "strike": 300, "entry": 10, "qty": 1, "price_source": "MANUAL"}],
        }

    def test_context_and_canonical_preset(self):
        context = self.client.get("/api/simulator/context")
        self.assertEqual(context.status_code, 200)
        self.assertEqual(context.get_json()["lot_size"], 1250)
        preset = self.client.post("/api/simulator/preset", json={
            "trend": "BULLISH", "underlying_price": 293, "atm_strike": 295,
            "expiry": "26NOV", "atm_ce_price": 20, "atm_pe_price": 21,
        })
        self.assertEqual(preset.status_code, 200)
        legs = preset.get_json()["recommendation"]["legs"]
        self.assertEqual((legs[0]["strike"], legs[0]["option_type"], legs[0]["qty"]), (300, "PE", 12))

    def test_simulator_page_contains_all_result_views(self):
        response = self.client.get("/simulator")
        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        for label in ("Payoff graph", "Positions", "Greeks", "Scenario matrix",
                      "Historical replay", "Assumptions &amp; data"):
            self.assertIn(label, page)

    @patch("src.web_server.ensure_broker_connected")
    def test_quote_route_uses_existing_read_only_broker(self, connect):
        class Broker:
            def get_quotes(self, symbols):
                return {symbol: 12.5 for symbol in symbols}
        connect.return_value = Broker()
        response = self.client.post("/api/simulator/quotes", json={"symbols": ["MCX:TEST"]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["quotes"]["MCX:TEST"]["ltp"], 12.5)

    def test_calculate_and_scenario_routes(self):
        calculated = self.client.post("/api/simulator/calculate", json=self.basket())
        self.assertEqual(calculated.status_code, 200)
        self.assertIn("greeks", calculated.get_json()["result"])
        scenario = self.client.post("/api/simulator/scenario", json=self.basket())
        self.assertEqual(scenario.status_code, 200)
        self.assertEqual(len(scenario.get_json()["result"]["matrix"]), 7)

    def test_invalid_basket_is_rejected(self):
        invalid = self.basket(); invalid["legs"][0]["qty"] = 0
        response = self.client.post("/api/simulator/calculate", json=invalid)
        self.assertEqual(response.status_code, 400)
        self.assertIn("quantity", response.get_json()["message"])


if __name__ == "__main__":
    unittest.main()
