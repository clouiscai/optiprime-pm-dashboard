import unittest
from unittest.mock import patch

from fastapi import HTTPException

from api.routes import check_login_rate_limit, login_attempts, record_login_result
from services.auth import account_for_credentials, issue_session_token, role_for_token, session_for_token


class AuthTests(unittest.TestCase):
    def setUp(self):
        login_attempts.clear()
        self.environment = {
            "OPTIPRIME_ENV": "production",
            "OPTIPRIME_USERNAME": "admin-user",
            "OPTIPRIME_PASSWORD": "correct horse battery staple",
            "OPTIPRIME_VIEWER_USERNAME": "read-only-user",
            "OPTIPRIME_VIEWER_PASSWORD": "a separate viewer password",
            "OPTIPRIME_SESSION_SECRET": "s" * 64,
            "OPTIPRIME_ALLOW_STATIC_TOKENS": "false",
            "ROBOTX_TOKEN": "legacy-admin-token-that-is-not-a-session",
            "OPTIPRIME_VIEWER_TOKEN": "legacy-viewer-token-that-is-not-a-session",
        }

    def test_login_issues_signed_session_without_accepting_static_token(self):
        with patch.dict("os.environ", self.environment, clear=True):
            account = account_for_credentials("admin-user", "correct horse battery staple")
            self.assertIsNotNone(account)
            self.assertEqual(role_for_token(account["token"]), "admin")
            self.assertIsNone(role_for_token(self.environment["ROBOTX_TOKEN"]))

    def test_tampered_and_expired_sessions_are_rejected(self):
        with patch.dict("os.environ", self.environment, clear=True):
            with patch("services.auth.time.time", return_value=1_000_000):
                token = issue_session_token("admin-user", "admin")
            self.assertIsNone(session_for_token(token[:-1] + ("a" if token[-1] != "a" else "b")))
            with patch("services.auth.time.time", return_value=2_000_000):
                self.assertIsNone(session_for_token(token))

    def test_production_rejects_default_admin_password(self):
        environment = dict(self.environment)
        environment["OPTIPRIME_PASSWORD"] = "change-this-admin-password"
        with patch.dict("os.environ", environment, clear=True):
            self.assertIsNone(account_for_credentials("admin-user", "change-this-admin-password"))

    def test_login_attempts_are_throttled(self):
        client_key = "test-client"
        for _ in range(5):
            check_login_rate_limit(client_key)
            record_login_result(client_key, False)
        with self.assertRaises(HTTPException) as context:
            check_login_rate_limit(client_key)
        self.assertEqual(context.exception.status_code, 429)
        record_login_result(client_key, True)
        check_login_rate_limit(client_key)


if __name__ == "__main__":
    unittest.main()
