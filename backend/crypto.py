"""
Device password encryption and decryption helpers.

Uses Fernet symmetric encryption from the cryptography package.
SECRET_KEY is read from the SECRET_KEY environment variable or backend/.env.
"""
import base64
import os

from cryptography.fernet import Fernet
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))

_fernet: "Fernet | None" = None


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is not None:
        return _fernet

    secret_key = os.environ.get("SECRET_KEY", "").strip()
    if not secret_key:
        raise RuntimeError(
            "SECRET_KEY environment variable is not set. "
            "Create backend/.env with: SECRET_KEY=<key>\n"
            "Generate a key with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )

    try:
        if len(secret_key) == 44 and secret_key.endswith("="):
            key_bytes = secret_key.encode()
        else:
            import hashlib

            derived = hashlib.sha256(secret_key.encode()).digest()
            key_bytes = base64.urlsafe_b64encode(derived)
    except Exception:
        key_bytes = secret_key.encode()

    _fernet = Fernet(key_bytes)
    return _fernet


def encrypt_password(plain: str) -> str:
    """Encrypt a plaintext password and return ciphertext."""
    if not plain:
        return ""
    f = _get_fernet()
    return f.encrypt(plain.encode()).decode()


def decrypt_password(cipher: str) -> str:
    """Decrypt ciphertext and return the plaintext password."""
    if not cipher:
        return ""
    f = _get_fernet()
    return f.decrypt(cipher.encode()).decode()
