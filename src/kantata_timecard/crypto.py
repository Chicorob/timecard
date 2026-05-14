from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from .config import get_settings


class TokenCipher:
    def __init__(self, key: str):
        self._fernet = Fernet(key.encode() if isinstance(key, str) else key)

    def encrypt(self, plaintext: str) -> bytes:
        return self._fernet.encrypt(plaintext.encode("utf-8"))

    def decrypt(self, ciphertext: bytes) -> str:
        try:
            return self._fernet.decrypt(ciphertext).decode("utf-8")
        except InvalidToken as e:
            raise ValueError("token decryption failed (wrong TOKEN_ENCRYPTION_KEY?)") from e


_cipher: TokenCipher | None = None


def get_cipher() -> TokenCipher:
    global _cipher
    if _cipher is None:
        _cipher = TokenCipher(get_settings().token_encryption_key)
    return _cipher
