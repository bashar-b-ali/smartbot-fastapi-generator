from app.core.security import hash_password, needs_rehash, verify_password


def test_bcrypt_password_verifies() -> None:
    hashed = hash_password("secret")
    assert verify_password("secret", hashed) is True
    assert verify_password("wrong", hashed) is False


def test_unknown_legacy_hash_fails_closed() -> None:
    legacy_hash = "pbkdf2_sha256$260000$salt$hash"
    assert verify_password("secret", legacy_hash) is False
    assert needs_rehash(legacy_hash) is False
