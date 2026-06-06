"""
crypto.py — enkripsi / dekripsi password device F5
Menggunakan Fernet symmetric encryption dari library cryptography.
SECRET_KEY dibaca dari environment variable SECRET_KEY (atau file .env).
"""
import os
import base64
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
            "SECRET_KEY environment variable belum diset! "
            "Buat file backend/.env dengan isi: SECRET_KEY=<key>\n"
            "Generate key baru: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )

    # Jika key belum dalam format Fernet base64 (44 char), derive dari string biasa
    try:
        if len(secret_key) == 44 and secret_key.endswith("="):
            key_bytes = secret_key.encode()
        else:
            # Derive 32 bytes dari string apapun, lalu encode ke base64 url-safe
            import hashlib
            derived = hashlib.sha256(secret_key.encode()).digest()
            key_bytes = base64.urlsafe_b64encode(derived)
    except Exception:
        key_bytes = secret_key.encode()

    _fernet = Fernet(key_bytes)
    return _fernet


def encrypt_password(plain: str) -> str:
    """Enkripsi password plaintext, return string ciphertext."""
    if not plain:
        return ""
    f = _get_fernet()
    return f.encrypt(plain.encode()).decode()


def decrypt_password(cipher: str) -> str:
    """Dekripsi ciphertext, return password plaintext."""
    if not cipher:
        return ""
    f = _get_fernet()
    return f.decrypt(cipher.encode()).decode()
