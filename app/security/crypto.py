import base64
import hashlib
import secrets

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

_SALT = b"sofia_agent_v1_kdf_salt_2025"
_ITERATIONS = 480_000  # OWASP 2024 minimum for PBKDF2-SHA256


class EncryptionManager:
    """
    AES-128-CBC + HMAC-SHA256 via Fernet for encrypting PII at rest.
    Key is derived from master secret using PBKDF2-SHA256.
    """

    def __init__(self, master_key: str) -> None:
        raw = master_key.encode() if isinstance(master_key, str) else master_key
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=_SALT,
            iterations=_ITERATIONS,
        )
        fernet_key = base64.urlsafe_b64encode(kdf.derive(raw))
        self._fernet = Fernet(fernet_key)

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, ciphertext: str) -> str:
        return self._fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")

    def encrypt_optional(self, value: str | None) -> str | None:
        return self.encrypt(value) if value else None

    def decrypt_optional(self, value: str | None) -> str | None:
        return self.decrypt(value) if value else None


def generate_secure_token(nbytes: int = 32) -> str:
    """Cryptographically secure URL-safe token."""
    return secrets.token_urlsafe(nbytes)


def hash_identifier(value: str) -> str:
    """Deterministic SHA-256 hex digest for indexed lookups without storing plaintext."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
