import json
import tempfile
import unittest
from pathlib import Path

from src.state_manager import UserManager
from src.web_server import app, state_mgr


class UserManagementTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.manager = UserManager(str(Path(self.tempdir.name) / "users.json"))
        self.original_manager = state_mgr.user_manager
        state_mgr.user_manager = self.manager
        app.config.update(TESTING=True)
        self.client = app.test_client()
        token, user = self.manager.authenticate("admin", "password123")
        self.assertIsNotNone(token)
        with self.client.session_transaction() as session:
            session["user_token"] = token
            session["username"] = "admin"
            session["role"] = user["role"]

    def tearDown(self):
        state_mgr.user_manager = self.original_manager
        self.tempdir.cleanup()

    def test_legacy_password_migrates_and_new_password_is_hashed(self):
        self.manager.users["legacy"] = {
            "id": "u_legacy", "password": "Legacy123", "name": "Legacy",
            "role": "VIEWER", "email": "legacy@example.com", "active": True,
        }
        token, _ = self.manager.authenticate("legacy", "Legacy123")
        self.assertIsNotNone(token)
        self.assertIn("password_hash", self.manager.users["legacy"])
        self.assertNotIn("password", self.manager.users["legacy"])
        self.assertNotEqual(self.manager.users["legacy"]["password_hash"], "Legacy123")

    def test_admin_can_create_reset_disable_and_delete_user(self):
        created = self.client.post("/api/admin/users", json={
            "username": "alice", "name": "Alice", "email": "alice@example.com",
            "role": "VIEWER", "password": "Alice123", "active": True,
        })
        self.assertEqual(created.status_code, 200)
        listed = self.client.get("/api/admin/users").get_json()["users"]
        alice = next(item for item in listed if item["username"] == "alice")
        self.assertNotIn("password", alice)
        self.assertNotIn("password_hash", alice)

        reset = self.client.post("/api/admin/users/alice/reset-password", json={})
        self.assertEqual(reset.status_code, 200)
        temporary = reset.get_json()["temporary_password"]
        self.assertTrue(temporary)
        token, _ = self.manager.authenticate("alice", temporary)
        self.assertIsNotNone(token)
        self.assertTrue(self.manager.users["alice"]["must_change_password"])

        disabled = self.client.post("/api/admin/users/alice/status", json={"active": False})
        self.assertEqual(disabled.status_code, 200)
        self.assertIsNone(self.manager.authenticate("alice", temporary)[0])
        deleted = self.client.delete("/api/admin/users/alice")
        self.assertEqual(deleted.status_code, 200)
        self.assertNotIn("alice", self.manager.users)

    def test_user_can_change_password_and_primary_admin_cannot_be_deleted(self):
        changed = self.client.post("/api/user/change_password", json={
            "current_password": "password123", "new_password": "NewAdmin123",
        })
        self.assertEqual(changed.status_code, 200)
        self.assertIsNotNone(self.manager.authenticate("admin", "NewAdmin123")[0])
        weak = self.client.post("/api/user/change_password", json={
            "current_password": "NewAdmin123", "new_password": "short",
        })
        self.assertEqual(weak.status_code, 400)
        deleted = self.client.delete("/api/admin/users/admin")
        self.assertEqual(deleted.status_code, 400)

    def test_user_profile_never_returns_password_material(self):
        profile = self.client.get("/api/user/profile")
        self.assertEqual(profile.status_code, 200)
        body = json.dumps(profile.get_json())
        self.assertNotIn("password_hash", body)
        self.assertNotIn("password123", body)


if __name__ == "__main__":
    unittest.main()
