from cryptography.fernet import Fernet

from app.core.config import settings

_fernet = Fernet(settings.credentials_encryption_key.encode())


def encrypt(value: str) -> str:
    return _fernet.encrypt(value.encode()).decode()


def decrypt(token: str) -> str:
    return _fernet.decrypt(token.encode()).decode()
