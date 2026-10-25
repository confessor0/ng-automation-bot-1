import tempfile
import unittest
from pathlib import Path

from src.database import UnifiedOrderManager
from src.web_server import app


class SimulatorPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.original_path = UnifiedOrderManager.DB_PATH
        self.original_instance = UnifiedOrderManager._instance
        self.tempdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        UnifiedOrderManager.DB_PATH = str(Path(self.tempdir.name) / "simulator.db")
        UnifiedOrderManager._instance = None
        self.db = UnifiedOrderManager.get_instance()
        app.config.update(TESTING=True)
        self.client = app.test_client()
        with self.client.session_transaction() as session:
            session["user_token"] = "persistence-test-token"

    def tearDown(self):
        UnifiedOrderManager._instance = self.original_instance
        UnifiedOrderManager.DB_PATH = self.original_path
        self.tempdir.cleanup()

    def payload(self):
        return {
            "engine_version": 2, "name": "Bullish test", "symbol": "NATGASMINI",
            "underlying_price": 300, "atm_strike": 300, "lot_size": 1250,
            "as_of": "2026-10-25T10:00:00+05:30", "expiry": "2026-11-25T23:30:00+05:30",
            "mode": "TARGET_DATE", "implied_volatility": 35, "interest_rate": 6.5,
            "legs": [{"side": "BUY", "instrument": "FUT", "entry": 300, "qty": 1,
                      "symbol": "MCX:NATGASMINI26NOVFUT", "price_source": "MANUAL"}],
        }

    def test_save_duplicate_lifecycle_archive_and_delete(self):
        created = self.client.post("/api/simulations", json=self.payload())
        self.assertEqual(created.status_code, 200)
        simulation_id = created.get_json()["simulation"]["simulation_id"]
        listed = self.client.get("/api/simulations").get_json()["simulations"]
        self.assertEqual(listed[0]["name"], "Bullish test")
        duplicate = self.client.post(f"/api/simulations/{simulation_id}/duplicate", json={"name": "Copy"})
        self.assertEqual(duplicate.status_code, 200)
        reviewed = self.client.patch(f"/api/simulations/{simulation_id}", json={"status": "REVIEWED"})
        self.assertEqual(reviewed.get_json()["simulation"]["status"], "REVIEWED")
        archived = self.client.patch(f"/api/simulations/{simulation_id}", json={"status": "ARCHIVED", "archived": True})
        self.assertEqual(archived.status_code, 200)
        self.assertFalse(any(row["simulation_id"] == simulation_id for row in self.client.get("/api/simulations").get_json()["simulations"]))
        self.assertEqual(self.client.delete(f"/api/simulations/{simulation_id}").status_code, 200)

    def test_replay_uses_saved_events_and_labels_estimates(self):
        payload = {"details": {"futures_close": 300, "ce_close": 12, "pe_close": 11},
                   "symbols": {"fut": "MCX:NATGASMINI26NOVFUT",
                               "ce": "MCX:NATGASMINI26NOV300CE",
                               "pe": "MCX:NATGASMINI26NOV300PE"}, "atm_strike": 300}
        source_id = self.db.record_strategy_event(
            "HISTORICAL_SIGNAL", "NATGASMINI", "BULLISH", "TREND_CONTINUES_BULLISH",
            "BULLISH", "26NOV", 300, 300, payload,
            occurred_at="2026-10-20T18:00:00+05:30", candle_at="2026-10-20T05:30:00+05:30")
        self.db.record_strategy_event(
            "HISTORICAL_SIGNAL", "NATGASMINI", "BULLISH", "TREND_CONTINUES_BULLISH",
            "BULLISH", "26NOV", 305, 310, {"details": {"futures_close": 310}},
            occurred_at="2026-10-21T18:00:00+05:30", candle_at="2026-10-21T05:30:00+05:30")
        response = self.client.post(f"/api/simulator/replay/{source_id}", json={"implied_volatility": 35})
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertGreaterEqual(data["count"], 2)
        self.assertIn("THEORETICAL_OPTIONS", data["data_quality"])

    def test_scan_history_listing_is_capped_at_twenty(self):
        for index in range(25):
            self.db.save_scan_run(
                f"SCAN_{index:02d}", "NATGASMINI", 20,
                "2026-10-01", "2026-10-24", "COMPLETED", {"records": 20}
            )
        self.assertEqual(len(self.db.list_scan_runs(limit=200)), 20)


if __name__ == "__main__":
    unittest.main()
