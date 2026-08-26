"""Tests for the login layer (app.security) - password hashing and session
tokens. Pure/fast, no DB or network, same reasoning as test_permissions.py:
this only exercises the crypto helpers, not the users-table repository code
(which needs a real Postgres connection, exactly like every other
*_repository module in this project - see tests/test_tools.py).
"""

from __future__ import annotations

import unittest

from app import security


class PasswordHashingTests(unittest.TestCase):
    def test_correct_password_verifies(self) -> None:
        stored = security.hash_password("correct horse battery staple")
        self.assertTrue(security.verify_password("correct horse battery staple", stored))

    def test_wrong_password_does_not_verify(self) -> None:
        stored = security.hash_password("correct horse battery staple")
        self.assertFalse(security.verify_password("wrong password", stored))

    def test_hash_is_salted_differently_each_time(self) -> None:
        first = security.hash_password("same password")
        second = security.hash_password("same password")
        self.assertNotEqual(first, second)
        self.assertTrue(security.verify_password("same password", first))
        self.assertTrue(security.verify_password("same password", second))

    def test_malformed_stored_hash_denies_rather_than_raises(self) -> None:
        self.assertFalse(security.verify_password("anything", "not-a-real-hash"))
        self.assertFalse(security.verify_password("anything", ""))

    def test_hash_records_the_pbkdf2_algorithm_and_iteration_count(self) -> None:
        stored = security.hash_password("x")
        algo, iterations, _salt, _digest = stored.split("$")
        self.assertEqual(algo, "pbkdf2_sha256")
        self.assertEqual(int(iterations), security.PBKDF2_ITERATIONS)


class SessionTokenTests(unittest.TestCase):
    def test_round_trip_preserves_username_and_role(self) -> None:
        token = security.create_session_token("admin", "admin")
        decoded = security.decode_session_token(token)
        self.assertEqual(decoded, {"sub": "admin", "role": "admin"})

    def test_garbage_token_is_rejected(self) -> None:
        self.assertIsNone(security.decode_session_token("not.a.jwt"))

    def test_empty_token_is_rejected(self) -> None:
        self.assertIsNone(security.decode_session_token(""))

    def test_token_signed_with_a_different_secret_is_rejected(self) -> None:
        import jwt as pyjwt

        forged = pyjwt.encode({"sub": "admin", "role": "admin"}, "a-different-secret", algorithm="HS256")
        self.assertIsNone(security.decode_session_token(forged))

    def test_expired_token_is_rejected(self) -> None:
        import jwt as pyjwt
        from datetime import datetime, timedelta, timezone

        expired_payload = {
            "sub": "admin",
            "role": "admin",
            "iat": datetime.now(timezone.utc) - timedelta(hours=13),
            "exp": datetime.now(timezone.utc) - timedelta(hours=1),
        }
        expired = pyjwt.encode(expired_payload, security._SESSION_SECRET, algorithm=security.SESSION_ALGORITHM)
        self.assertIsNone(security.decode_session_token(expired))


if __name__ == "__main__":
    unittest.main()
