from app.crypto import decrypt_value, encrypt_value


def test_encrypt_decrypt_roundtrip():
    ciphertext = encrypt_value("my-secret-cookie-value")
    assert ciphertext != "my-secret-cookie-value"
    assert decrypt_value(ciphertext) == "my-secret-cookie-value"
