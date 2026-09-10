import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


class TokenCipher:
    """Encrypt delivery-bot tokens with a local Fernet key."""

    def __init__(self, key_path: Path | str) -> None:
        self.key_path = Path(key_path)
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        key = self._load_or_create_key()
        self._fernet = Fernet(key)

    def _load_or_create_key(self) -> bytes:
        try:
            with self.key_path.open("xb") as file:
                key = Fernet.generate_key()
                file.write(key)
            try:
                os.chmod(self.key_path, 0o600)
            except OSError:
                pass
            return key
        except FileExistsError:
            key = self.key_path.read_bytes().strip()
            Fernet(key)
            return key

    def encrypt(self, token: str) -> str:
        return self._fernet.encrypt(token.encode("utf-8")).decode("ascii")

    def decrypt(self, encrypted_token: str) -> str:
        try:
            return self._fernet.decrypt(encrypted_token.encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeError, ValueError) as exc:
            raise ValueError("Could not decrypt delivery bot token") from exc
