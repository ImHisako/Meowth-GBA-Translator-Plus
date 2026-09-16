"""Local, provider-specific API key storage shared by the GUI and CLI."""

import json
import os
from pathlib import Path
import sys
import tempfile


class CredentialsError(ValueError):
    """A credential file could not be used; never include its contents."""


def credentials_path() -> Path:
    override = os.environ.get("MEOWTH_API_KEYS_FILE")
    if override:
        return Path(override).expanduser().resolve()
    project = Path(__file__).resolve().parents[2]
    if not getattr(sys, "frozen", False) and (project / "pyproject.toml").is_file():
        return project / "api_keys.json"
    if sys.platform == "win32":
        folder = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "Meowth"
    elif sys.platform == "darwin":
        folder = Path.home() / "Library/Application Support/Meowth"
    else:
        folder = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "meowth"
    return folder / "api_keys.json"


class CredentialStore:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path is not None else credentials_path()

    def load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"version": 1, "providers": {}, "last_provider": "deepseek"}
        except (OSError, ValueError):
            raise CredentialsError("Impossibile leggere il file locale delle chiavi API.") from None
        if (not isinstance(data, dict) or data.get("version") != 1
                or not isinstance(data.get("providers"), dict)
                or not all(isinstance(k, str) and isinstance(v, str) for k, v in data["providers"].items())
                or not isinstance(data.get("last_provider", "deepseek"), str)):
            raise CredentialsError("Formato del file locale delle chiavi API non valido.")
        return data

    def get(self, provider: str) -> str:
        return self.load()["providers"].get(provider, "")

    def save(self, provider: str, api_key: str) -> None:
        data = self.load()  # Refuse to overwrite an unreadable existing file.
        api_key = api_key.strip()
        if api_key:
            data["providers"][provider] = api_key
        else:
            data["providers"].pop(provider, None)
        data["last_provider"] = provider
        temporary = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # mkstemp grants owner-only access on POSIX. Replace atomically so
            # interruption cannot leave a partially written credential file.
            fd, temporary = tempfile.mkstemp(prefix=".api_keys-", suffix=".tmp", dir=self.path.parent)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            os.replace(temporary, self.path)
        except OSError:
            raise CredentialsError("Impossibile salvare il file locale delle chiavi API.") from None
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)
