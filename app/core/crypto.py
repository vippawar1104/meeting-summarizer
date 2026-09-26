"""Encryption for secrets we must store and later use (users' own LLM API keys).

Fernet (AES-128-CBC + HMAC, authenticated). REVIEWLY_ENCRYPTION_KEY may hold several comma-separated
keys: the first encrypts, all of them decrypt, so a key can be rotated without losing stored secrets.
"""

import base64

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

# Only ever used when REVIEWLY_ENV=dev; production refuses to start without a real key.
DEV_KEY = base64.urlsafe_b64encode(b"reviewly-dev-key-not-a-secret-32").decode()


class SecretBox:
    def __init__(self, keys: str) -> None:
        parts = [k.strip() for k in keys.split(",") if k.strip()]
        if not parts:
            raise ValueError("no encryption key configured")
        self._box = MultiFernet([Fernet(k.encode()) for k in parts])

    def encrypt(self, plaintext: str) -> str:
        return self._box.encrypt(plaintext.encode()).decode()

    def decrypt(self, token: str) -> str:
        try:
            return self._box.decrypt(token.encode()).decode()
        except InvalidToken:
            raise ValueError(
                "stored secret cannot be decrypted (wrong or rotated-out key)"
            ) from None


def generate_key() -> str:
    return Fernet.generate_key().decode()


def valid_key(key: str) -> bool:
    try:
        SecretBox(key)
    except (ValueError, TypeError):
        return False
    return True


if __name__ == "__main__":
    print(generate_key())
