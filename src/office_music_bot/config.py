from __future__ import annotations

import base64
import ctypes
import json
import os
import tempfile
from dataclasses import dataclass, field
from getpass import getpass
from pathlib import Path


class ConfigurationError(Exception):
    pass


def data_directory() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    if os.name != "nt" or not local:
        raise ConfigurationError("This application requires Windows and LOCALAPPDATA.")
    return Path(local) / "OfficeMusicBot"


class _Blob(ctypes.Structure):
    _fields_ = [("size", ctypes.c_uint32), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def _protect(data: bytes, *, decrypt: bool = False) -> bytes:
    if os.name != "nt":
        raise ConfigurationError("Saved bot tokens require Windows DPAPI.")
    source_buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source = _Blob(len(data), source_buffer)
    result = _Blob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    operation = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    operation.argtypes = [
        ctypes.POINTER(_Blob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.POINTER(_Blob),
    ]
    operation.restype = ctypes.c_int
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    if not operation(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise ConfigurationError(
            "Windows could not protect/read the token. Run --setup as the owning Windows user."
        )
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        kernel32.LocalFree(result.data)


def _snowflake(value: object, name: str) -> int:
    text = str(value)
    if len(text) > 20 or not text.isascii() or not text.isdigit() or not 0 < int(text) < 2**64:
        raise ConfigurationError(f"{name} must be a positive Discord ID (Developer Mode: Copy ID).")
    return int(text)


@dataclass(frozen=True)
class Settings:
    token: str = field(repr=False)
    guild_id: str = ""
    channel_id: str = ""


@dataclass(frozen=True)
class Config:
    token: str = field(repr=False)
    guild_id: int
    channel_id: int
    directory: Path


def load_settings(directory: Path) -> Settings:
    path = directory / "config.json"
    saved: dict[str, object] = {}
    if path.exists():
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ConfigurationError("Cannot read config.json. Run --setup to replace it.") from exc
        if not isinstance(saved, dict):
            raise ConfigurationError("Invalid config.json object. Run --setup.")
    token = os.environ.get("OFFICE_MUSIC_TOKEN", "").strip()
    if not token and saved.get("protected_token"):
        try:
            token = _protect(
                base64.b64decode(str(saved["protected_token"]), validate=True), decrypt=True
            ).decode("utf-8")
        except (ValueError, UnicodeError) as exc:
            raise ConfigurationError("Invalid saved token. Run --setup.") from exc
    return Settings(
        token=token,
        guild_id=str(os.environ.get("OFFICE_MUSIC_GUILD_ID", saved.get("guild_id", ""))),
        channel_id=str(os.environ.get("OFFICE_MUSIC_CHANNEL_ID", saved.get("channel_id", ""))),
    )


def validate_settings(settings: Settings, directory: Path) -> Config:
    token = settings.token.strip()
    if not token or any(char.isspace() for char in token):
        raise ConfigurationError(
            "No valid bot token. Run --setup or set OFFICE_MUSIC_TOKEN locally."
        )
    return Config(
        token=token,
        guild_id=_snowflake(settings.guild_id.strip(), "Server ID"),
        channel_id=_snowflake(settings.channel_id.strip(), "Channel ID"),
        directory=directory,
    )


def load_config(directory: Path) -> Config:
    return validate_settings(load_settings(directory), directory)


def save_settings(directory: Path, settings: Settings) -> None:
    encoded = (
        base64.b64encode(_protect(settings.token.encode("utf-8"))).decode("ascii")
        if settings.token
        else ""
    )
    directory.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=directory, delete=False, suffix=".tmp"
        ) as stream:
            temporary = Path(stream.name)
            json.dump(
                {
                    "protected_token": encoded,
                    "guild_id": settings.guild_id,
                    "channel_id": settings.channel_id,
                },
                stream,
                indent=2,
            )
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, directory / "config.json")
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def setup(directory: Path) -> None:
    print("Discord bot setup. Never share the token or paste it into a chat.")
    settings = Settings(
        getpass("Bot token (hidden): ").strip(),
        input("Discord server ID: ").strip(),
        input("Song request text channel ID: ").strip(),
    )
    validate_settings(settings, directory)
    save_settings(directory, settings)
    print(f"Saved user-protected configuration in {directory}.")


class InstanceLock:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "instance.lock"
        self.stream = None

    def __enter__(self) -> InstanceLock:
        import msvcrt

        self.stream = self.path.open("a+b")
        if self.path.stat().st_size == 0:
            self.stream.write(b"\0")
            self.stream.flush()
        self.stream.seek(0)
        try:
            msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            self.stream.close()
            raise ConfigurationError("Another OfficeMusicBot instance is already running.") from exc
        return self

    def __exit__(self, *args: object) -> None:
        import msvcrt

        if self.stream:
            self.stream.seek(0)
            msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
            self.stream.close()
