from alpaca_bot.web.auth import hash_password, verify_password


def test_hash_password_round_trips_with_verify() -> None:
    encoded = hash_password("secret-password", salt=bytes.fromhex("00" * 16))

    assert encoded.startswith("scrypt$16384$8$1$")
    assert verify_password(password="secret-password", encoded_hash=encoded) is True
    assert verify_password(password="wrong-password", encoded_hash=encoded) is False
