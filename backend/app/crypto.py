from cryptography.fernet import Fernet

from app.config import get_settings


def encrypt_value(plaintext: str) -> str:
    fernet = Fernet(get_settings().fernet_key.encode())
    return fernet.encrypt(plaintext.encode()).decode()


def decrypt_value(ciphertext: str) -> str:
    fernet = Fernet(get_settings().fernet_key.encode())
    return fernet.decrypt(ciphertext.encode()).decode()
