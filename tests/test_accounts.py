"""Accounts, roles and per-customer isolation. Synthetic accounts and scripts only."""

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from studio.accounts import RateLimiter, check_password, hash_password, normalize_email, token_hash, verify_password
from studio.store import Store, StudioError

PASSWORD = "correct horse battery"


def later(days=1):
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat(timespec="seconds")


class PasswordTests(unittest.TestCase):
    def test_hash_and_verify(self):
        stored = hash_password(PASSWORD)
        self.assertTrue(stored.startswith("scrypt$"))
        self.assertTrue(verify_password(PASSWORD, stored))
        self.assertFalse(verify_password("wrong password!", stored))
        self.assertFalse(verify_password(PASSWORD, "not-a-hash"))
        self.assertNotEqual(hash_password(PASSWORD), stored)  # Salted.

    def test_validation(self):
        self.assertEqual(normalize_email("  Ana@Example.COM "), "ana@example.com")
        for bad in ("", "ana", "ana@", "a b@example.com", "ana@example"):
            with self.assertRaises(ValueError):
                normalize_email(bad)
        with self.assertRaises(ValueError):
            check_password("short")
        check_password(PASSWORD)

    def test_rate_limiter(self):
        limiter = RateLimiter(limit=2, window=60)
        self.assertFalse(limiter.blocked("a"))
        limiter.fail("a", "ip")
        limiter.fail("a")
        self.assertTrue(limiter.blocked("a"))
        self.assertFalse(limiter.blocked("ip"))
        limiter.reset("a")
        self.assertFalse(limiter.blocked("a"))


class AccountStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(dir=".tools")
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name))
        self.store.initialize()
        self.store.add_voice("voice", "Test", 7.0)
        self.admin = self.store.create_user("admin@example.com", "hash", "admin", "Admin", terms=True)
        self.ana = self.store.create_user("ana@example.com", "hash", "customer", "Ana", terms=True)
        self.ben = self.store.create_user("ben@example.com", "hash", "customer")

    def test_users_and_sessions(self):
        with self.assertRaises(StudioError) as duplicate:
            self.store.create_user("ANA@example.com", "hash")
        self.assertEqual(duplicate.exception.status, 409)
        self.assertEqual(self.store.admin_count(), 1)
        self.store.create_session(self.ana["id"], token_hash("valid"), later())
        self.store.create_session(self.ana["id"], token_hash("expired"), later(-1))
        self.assertEqual(self.store.session_user(token_hash("valid"))["email"], "ana@example.com")
        self.assertIsNone(self.store.session_user(token_hash("expired")))
        self.assertIsNone(self.store.session_user(token_hash("unknown")))
        self.assertIsNotNone(self.store.user(self.ana["id"])["last_login_at"])
        self.store.delete_session(token_hash("valid"))
        self.assertIsNone(self.store.session_user(token_hash("valid")))

    def test_disable_signs_out_and_stops_queued_work(self):
        self.store.create_session(self.ana["id"], token_hash("t"), later())
        job_id = self.store.create_job("T", "Hello there.", user_id=self.ana["id"])
        self.store.update_user(self.ana["id"], disabled=True)
        self.assertIsNone(self.store.session_user(token_hash("t")))
        self.assertEqual(self.store.job(job_id)["status"], "failed")
        self.store.update_user(self.ana["id"], disabled=False)
        self.assertEqual(self.store.user(self.ana["id"])["disabled"], 0)

    def test_last_admin_is_protected(self):
        with self.assertRaises(StudioError):
            self.store.update_user(self.admin["id"], disabled=True)
        with self.assertRaises(StudioError):
            self.store.update_user(self.admin["id"], role="customer")
        self.store.update_user(self.ben["id"], role="admin")
        self.store.update_user(self.admin["id"], role="customer")
        self.assertEqual(self.store.admin_count(), 1)

    def test_customers_see_only_their_own_recordings(self):
        ana_job = self.store.create_job("Ana's", "Hello.", user_id=self.ana["id"])
        ben_job = self.store.create_job("Ben's", "Hello.", user_id=self.ben["id"])
        admin_job = self.store.create_job("Admin's", "Hello.", user_id=self.admin["id"])
        owner_job = self.store.create_job("Owner's", "Hello.")
        self.assertEqual([job["id"] for job in self.store.state(self.ana["id"])["jobs"]], [ana_job])
        self.assertEqual({job["id"] for job in self.store.state()["jobs"]}, {admin_job, owner_job})
        self.assertEqual(self.store.job_owner(ben_job), self.ben["id"])
        with self.assertRaises(StudioError) as missing:
            self.store.job_owner("0" * 32)
        self.assertEqual(missing.exception.status, 404)
        self.assertEqual(self.store.active_jobs(self.ana["id"]), 1)
        reviewed = {item["id"] for item in self.store.customer_recordings()}
        self.assertEqual(reviewed, {ana_job, ben_job})

    def test_usage_log_survives_deletion(self):
        job_id = self.store.create_job("T", "Something to review.", user_id=self.ana["id"], voice_label="Studio voice")
        self.store.fail_job(job_id, "stopped")
        self.store.delete(job_id)
        [entry] = self.store.usage(self.ana["id"])
        self.assertEqual((entry["kind"], entry["text"], entry["voice"], entry["still_exists"]),
                         ("create", "Something to review.", "Studio voice", 0))
        self.assertEqual(self.store.usage(self.ben["id"]), [])

    def test_pronunciations_are_per_customer(self):
        self.store.save_pronunciation("Nuvexa", "noovexuh")
        self.store.save_pronunciation("Nuvexa", "nuhvexa", self.ana["id"])
        own = self.store.save_pronunciation("Kubo", "koobo", self.ana["id"])
        self.assertEqual(sorted(self.store.pronunciation_pairs(self.ana["id"])), [("Kubo", "koobo"), ("Nuvexa", "nuhvexa")])
        self.assertEqual(self.store.pronunciation_pairs(self.ben["id"]), [("Nuvexa", "noovexuh")])
        self.assertEqual(self.store.pronunciation_pairs(), [("Nuvexa", "noovexuh")])
        self.assertEqual([word["written"] for word in self.store.state(self.ana["id"])["pronunciations"]], ["Kubo", "Nuvexa"])
        with self.assertRaises(StudioError):
            self.store.delete_pronunciation(own["id"], self.ben["id"])
        self.store.delete_pronunciation(own["id"], self.ana["id"])


class AccountApiTests(unittest.TestCase):
    """Needs the studio environment (FastAPI, ffmpeg)."""

    def test_roles_isolation_and_review(self):
        from fastapi.testclient import TestClient

        from studio.app import create_app
        try:
            from test_takes import FakeEngine, wait_for, write_wav
        except ImportError:
            from tests.test_takes import FakeEngine, wait_for, write_wav

        headers = {"X-Studio-Request": "1"}
        with TemporaryDirectory(dir=".tools") as temporary:
            root = Path(temporary)
            write_wav(root / "reference.wav", seconds=7)
            app = create_app(root, FakeEngine(), auth=True)
            # Only the admin client starts the studio (worker, file lock); the others share the running app
            # and keep their own cookies.
            ana = TestClient(app, base_url="http://localhost")
            ben = TestClient(app, base_url="http://localhost")
            with TestClient(app, base_url="http://localhost") as admin:
                # First run: sign-in pages offer only setup, guarded by the terminal's code.
                self.assertEqual(admin.get("/", follow_redirects=False).headers["location"], "/login")
                self.assertEqual(admin.get("/api/state").status_code, 401)
                self.assertEqual(ana.post("/api/auth/signup", headers=headers, json={
                    "email": "ana@example.com", "password": PASSWORD, "terms": True}).status_code, 409)
                bad = admin.post("/api/auth/setup", headers=headers,
                                 json={"email": "admin@example.com", "password": PASSWORD, "code": "WRONG"})
                self.assertEqual(bad.status_code, 401)
                code = app.state.setup_code
                self.assertEqual(admin.post("/api/auth/setup", headers=headers, json={
                    "email": "admin@example.com", "password": PASSWORD, "code": code}).status_code, 201)
                self.assertEqual(ben.post("/api/auth/setup", headers=headers, json={
                    "email": "x@example.com", "password": PASSWORD, "code": code}).status_code, 409)

                # Open sign-up for customers, with the terms accepted.
                self.assertEqual(ana.post("/api/auth/signup", headers=headers, json={
                    "email": "ana@example.com", "password": PASSWORD}).status_code, 400)
                for client, email in ((ana, "ana@example.com"), (ben, "ben@example.com")):
                    response = client.post("/api/auth/signup", headers=headers,
                                           json={"email": email, "password": PASSWORD, "terms": True})
                    self.assertEqual(response.status_code, 201)
                    self.assertEqual(response.json()["account"]["role"], "customer")
                self.assertEqual(ana.post("/api/auth/signup", headers=headers, json={
                    "email": "ANA@example.com", "password": PASSWORD, "terms": True}).status_code, 409)

                # Customers cannot reach admin features.
                for method, url in (("get", "/api/dataset"), ("get", "/api/admin/accounts"), ("get", "/api/comparisons"),
                                    ("get", "/api/voice/current/audio"), ("post", "/api/dataset/export")):
                    self.assertEqual(getattr(ana, method)(url, headers=headers).status_code, 403, url)

                # A customer's recording uses the studio voice, whatever engine is requested.
                created = ana.post("/api/jobs", headers=headers,
                                   json={"script": "Private customer script.", "voice_match": "maximum"})
                self.assertEqual(created.status_code, 202)
                route = f"/api/jobs/{created.json()['id']}"
                done = wait_for(ana, route, "ready")
                self.assertNotIn("engine", done)
                self.assertNotIn("engine", done["sections"][0]["takes"][0])
                self.assertEqual([job["title"] for job in ana.get("/api/state").json()["jobs"]], ["Untitled recording"])
                self.assertEqual(ben.get("/api/state").json()["jobs"], [])
                self.assertEqual(ana.get(route + "/export/wav").status_code, 200)

                # Other customers get 404 for every route of that recording.
                for method, url in (("get", route), ("get", route + "/export/wav"), ("get", route + "/sections/1/audio"),
                                    ("post", route + "/retry"), ("delete", route)):
                    self.assertEqual(getattr(ben, method)(url, headers=headers).status_code, 404, url)

                # The admin can open it, sees whose it is, and reviews it read-only.
                opened = admin.get(route).json()
                self.assertIn("engine", opened)
                self.assertEqual((opened["owner"]["email"], opened["read_only"]), ("ana@example.com", True))
                for method, url, body in (("post", route + "/sections/1", {"text": "Changed by admin."}),
                                          ("put", route + "/loudness", {"target": -16.0}),
                                          ("post", route + "/approve", None)):
                    self.assertEqual(getattr(admin, method)(url, headers=headers, json=body).status_code, 403, url)
                self.assertEqual(admin.get(route + "/export/wav").status_code, 200)
                review = admin.get("/api/admin/recordings").json()["recordings"]
                self.assertEqual([item["script"] for item in review], ["Private customer script."])
                accounts = {account["email"]: account for account in admin.get("/api/admin/accounts").json()["accounts"]}
                me = accounts["admin@example.com"]["id"]
                self.assertEqual(admin.put(f"/api/admin/accounts/{me}", headers=headers,
                                           json={"disabled": True}).status_code, 409)
                ana_id = accounts["ana@example.com"]["id"]
                self.assertEqual(admin.put(f"/api/admin/accounts/{ana_id}", headers=headers,
                                           json={"disabled": True}).status_code, 200)
                self.assertEqual(ana.get("/api/state").status_code, 401)
                self.assertEqual(ana.post("/api/auth/login", headers=headers, json={
                    "email": "ana@example.com", "password": PASSWORD}).status_code, 401)

                # Sign-in, wrong passwords and rate limiting.
                self.assertEqual(ben.post("/api/auth/logout", headers=headers).status_code, 204)
                self.assertEqual(ben.get("/api/state").status_code, 401)
                self.assertEqual(ben.post("/api/auth/login", headers=headers, json={
                    "email": "ben@example.com", "password": PASSWORD}).status_code, 200)
                for _ in range(8):
                    ben.post("/api/auth/login", headers=headers, json={"email": "nobody@example.com", "password": "wrong password"})
                self.assertEqual(ben.post("/api/auth/login", headers=headers, json={
                    "email": "nobody@example.com", "password": "wrong password"}).status_code, 429)
                # Changes still require the studio header.
                self.assertEqual(ben.post("/api/auth/logout").status_code, 403)


if __name__ == "__main__":
    unittest.main()
