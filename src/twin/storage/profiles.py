import io
import tarfile
from pathlib import Path

from cryptography.fernet import Fernet

PROFILE_KEY_PREFIX = "profiles"
PROFILE_ARCHIVE_NAME = "profile.tar.gz"
PACK_EXCLUDE_DIRS = ("Cache", "Code Cache", "Crashpad", "Service Worker")
PACK_EXCLUDE_FILES = ("SingletonLock", "SingletonCookie", "SingletonSocket", "LOCK")
PACK_EXCLUDE_SUFFIXES = (".log",)


def profile_key(tenant: str, platform: str, tier: str) -> str:
    return f"{PROFILE_KEY_PREFIX}/{tenant}/{platform}/{tier}/{PROFILE_ARCHIVE_NAME}"


def validate_key(key: str) -> None:
    Fernet(key.encode())


def seal(data: bytes, key: str | None) -> bytes:
    if not key:
        return data
    return Fernet(key.encode()).encrypt(data)


def unseal(data: bytes, key: str | None) -> bytes:
    if not key:
        return data
    return Fernet(key.encode()).decrypt(data)


def _excluded(path: Path, relative: str) -> bool:
    if path.name in PACK_EXCLUDE_FILES or path.name.endswith(PACK_EXCLUDE_SUFFIXES):
        return True
    return any(part in PACK_EXCLUDE_DIRS for part in Path(relative).parts[:-1])


def pack_profile(source: Path) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for path in sorted(source.rglob("*")):
            relative = path.relative_to(source).as_posix()
            if _excluded(path, relative):
                continue
            archive.add(path, arcname=relative, recursive=False)
    return buffer.getvalue()


def unpack_profile(data: bytes, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        archive.extractall(dest, filter="data")
