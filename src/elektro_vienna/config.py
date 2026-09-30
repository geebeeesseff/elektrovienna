"""Centralized local-only configuration. Overrides stay within LocalAppData."""

import ctypes
import os
from dataclasses import dataclass
from pathlib import Path

from .models import historical_cutoff_ms


class ConfigurationError(Exception):
    pass


def local_app_data() -> Path:
    if os.name == "nt":
        # Query Windows itself so an overridden environment variable cannot redirect secrets.
        buffer = ctypes.create_unicode_buffer(32768)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 28, None, 0, buffer):
            raise ConfigurationError("Cannot resolve Windows LocalAppData.")
        return Path(buffer.value)
    value = os.environ.get("LOCALAPPDATA")
    if not value:
        raise ConfigurationError("Set LOCALAPPDATA to a private local directory on non-Windows hosts.")
    return Path(value)


def synchronized_roots() -> list[Path]:
    roots = [Path(os.environ[key]) for key in ("OneDrive", "OneDriveCommercial", "OneDriveConsumer")
             if os.environ.get(key)]
    if os.name == "nt":
        import winreg

        def visit(key_path):
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
                    for index in range(winreg.QueryInfoKey(key)[1]):
                        name, value, _ = winreg.EnumValue(key, index)
                        if name.casefold() in {"mountpoint", "userfolder"} and isinstance(value, str):
                            roots.append(Path(value))
                    for index in range(winreg.QueryInfoKey(key)[0]):
                        visit(key_path + "\\" + winreg.EnumKey(key, index))
            except FileNotFoundError:
                pass
            except OSError:
                raise ConfigurationError("Cannot verify Windows synchronization roots; refusing runtime storage.") from None

        visit(r"Software\SyncEngines\Providers\OneDrive")
        visit(r"Software\Microsoft\OneDrive\Accounts")
    return roots


def validate_path(path: Path, local_root: Path) -> Path:
    raw = path.expanduser().absolute()
    resolved = raw.resolve()
    root = local_root.expanduser().absolute()
    # Disallow redirected roots, junctions, symlinks, traversal and alternate data streams.
    if root.resolve() != root or resolved != raw or not resolved.is_relative_to(root):
        raise ConfigurationError("Credentials and state must be unredirected paths inside LocalAppData.")
    if resolved == root or any(":" in part for part in resolved.parts[1:]):
        raise ConfigurationError("Invalid local runtime path.")
    if any((parent / ".git").exists() for parent in (resolved, *resolved.parents)):
        raise ConfigurationError("Credentials and state must remain outside every Git repository.")
    if any(part.casefold() in {"sharepoint", "knowledgebase"} or
           part.casefold().startswith("onedrive") for part in resolved.parts):
        raise ConfigurationError("Credentials and state cannot use synchronized storage.")
    for sync_root in synchronized_roots():
        if resolved.is_relative_to(sync_root.resolve()):
            raise ConfigurationError("Credentials and state cannot use synchronized storage.")
    return resolved


@dataclass(frozen=True)
class Config:
    credentials_dir: Path
    state_path: Path
    mailbox: str = "office@elektrovienna.at"
    cutoff_ms: int = historical_cutoff_ms()

    @property
    def client_file(self) -> Path:
        return self.credentials_dir / "client_secret.json"

    @property
    def token_file(self) -> Path:
        return self.credentials_dir / "token.json"

    @classmethod
    def from_environment(cls) -> "Config":
        root = local_app_data()
        base = root / "ElektroViennaKnowledge"
        credentials = validate_path(Path(os.environ.get("EV_CREDENTIALS_DIR", base / "credentials")), root)
        state = validate_path(Path(os.environ.get("EV_STATE_PATH", base / "state" / "pipeline.sqlite3")), root)
        for name in ("client_secret.json", "token.json", "token.json.tmp"):
            validate_path(credentials / name, root)
        for suffix in ("", "-journal", "-wal", "-shm"):
            validate_path(Path(str(state) + suffix), root)
        if state == credentials or state.is_relative_to(credentials):
            raise ConfigurationError("State and credential directories must be separate.")
        return cls(credentials, state)
