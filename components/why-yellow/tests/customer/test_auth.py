import time, unittest, uuid
import jwt
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
from .harness import JWT_SECRET, SUPA_URL, token, verifier
from customer.auth import AuthError, Verifier, bearer_token


class TestJwtVerification(unittest.TestCase):
    def setUp(self):
        self.uid = str(uuid.uuid4())
        self.v = verifier()

    def test_valid_hs256_token_yields_identity_from_sub_only(self):
        ident = self.v.verify(token(self.uid, "a@example.test"))
        self.assertEqual(ident.user_id, self.uid)
        self.assertEqual(ident.email, "a@example.test")
        self.assertEqual(ident.role, "authenticated")
        self.assertNotIn(self.uid[:4], "")  # identity repr never includes the token
        self.assertEqual(repr(ident), f"Identity(user_id={self.uid!r})")

    def test_missing_or_malformed(self):
        for bad in (None, "", "abc", "a.b", "a.b.c.d"):
            with self.assertRaises(AuthError) as cm:
                self.v.verify(bad)
            self.assertEqual(cm.exception.status, 401)

    def test_expired(self):
        with self.assertRaises(AuthError) as cm:
            self.v.verify(token(self.uid, ttl=-120))
        self.assertEqual(cm.exception.code, "token_expired")

    def test_wrong_secret(self):
        with self.assertRaises(AuthError) as cm:
            self.v.verify(token(self.uid, secret="another-secret-that-is-long-enough-1234"))
        self.assertEqual(cm.exception.code, "invalid_token")

    def test_wrong_audience_issuer_role(self):
        with self.assertRaises(AuthError) as cm:
            self.v.verify(token(self.uid, aud="anon"))
        self.assertEqual(cm.exception.code, "bad_audience")
        with self.assertRaises(AuthError) as cm:
            self.v.verify(token(self.uid, iss="https://evil.example/auth/v1"))
        self.assertEqual(cm.exception.code, "bad_issuer")
        with self.assertRaises(AuthError) as cm:
            self.v.verify(token(self.uid, role="service_role"))
        self.assertEqual((cm.exception.code, cm.exception.status), ("role_not_authenticated", 403))

    def test_alg_none_and_disallowed_alg_rejected(self):
        t = jwt.encode({"sub": self.uid, "aud": "authenticated", "iss": f"{SUPA_URL}/auth/v1", "exp": int(time.time()) + 60, "iat": int(time.time()), "role": "authenticated"},
                       key=None, algorithm="none")
        with self.assertRaises(AuthError) as cm:
            self.v.verify(t)
        self.assertEqual(cm.exception.code, "alg_not_allowed")

    def test_sub_must_be_uuid(self):
        with self.assertRaises(AuthError) as cm:
            self.v.verify(token("not-a-uuid"))
        self.assertEqual(cm.exception.code, "bad_subject")

    def test_es256_via_jwks_with_kid_rotation(self):
        k1 = ec.generate_private_key(ec.SECP256R1()); k2 = ec.generate_private_key(ec.SECP256R1())
        def jwk(k, kid):
            from jwt.algorithms import ECAlgorithm
            import json
            d = json.loads(ECAlgorithm.to_jwk(k.public_key()))
            d.update(kid=kid, use="sig", alg="ES256")
            return d
        sets = [{"keys": [jwk(k1, "kid-1")]}, {"keys": [jwk(k1, "kid-1"), jwk(k2, "kid-2")]}]
        calls = {"n": 0}
        def fetch():
            i = min(calls["n"], len(sets) - 1); calls["n"] += 1
            return sets[i]
        v = Verifier(SUPA_URL, None, jwks_fetch=fetch)
        pem1 = k1.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        pem2 = k2.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        self.assertEqual(v.verify(token(self.uid, alg="ES256", key=pem1, headers={"kid": "kid-1"})).user_id, self.uid)
        # unknown kid -> one refresh -> found
        self.assertEqual(v.verify(token(self.uid, alg="ES256", key=pem2, headers={"kid": "kid-2"})).user_id, self.uid)
        with self.assertRaises(AuthError):
            v.verify(token(self.uid, alg="ES256", key=pem2, headers={"kid": "kid-9"}))
        with self.assertRaises(AuthError) as cm:   # HS256 token against a JWKS-only project
            v.verify(token(self.uid))
        self.assertEqual(cm.exception.code, "hs256_not_configured")

    def test_bearer_extraction(self):
        self.assertIsNone(bearer_token({}))
        self.assertIsNone(bearer_token({"Authorization": "Basic abc"}))
        self.assertEqual(bearer_token({"Authorization": "Bearer xyz"}), "xyz")
