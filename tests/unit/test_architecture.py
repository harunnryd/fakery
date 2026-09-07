import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "twin"


def _module_of(path: Path) -> str:
    return "twin." + ".".join(path.relative_to(SRC).with_suffix("").parts)


def _imports_of(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                package = _module_of(path).rsplit(".", node.level)[0]
                base = f"{package}.{node.module}" if node.module else package
            else:
                base = node.module or ""
            if base:
                found.add(base)
    return found


def _files_of(package: str) -> list[Path]:
    root = SRC / package.split(".", 1)[1]
    return sorted(p for p in root.rglob("*.py") if p.name != "__init__.py")


def _twin_imports(path: Path) -> set[str]:
    return {name for name in _imports_of(path) if name.startswith("twin.")}


@pytest.mark.parametrize(
    "package", ["twin.meet", "twin.storage"], ids=["meet-layer", "storage-layer"]
)
def test_inner_layers_import_no_inner_layer(package: str) -> None:
    banned = {"twin.bots", "twin.api", "twin.meet", "twin.storage"} - {package}
    offenders = {}
    for path in _files_of(package):
        hits = sorted(
            name
            for name in _twin_imports(path)
            if any(name == ban or name.startswith(f"{ban}.") for ban in banned)
        )
        if hits:
            offenders[str(path.relative_to(SRC))] = hits
    assert offenders == {}


def _importers_of(sdk: str) -> set[str]:
    return {
        str(path.relative_to(SRC)) for path in sorted(SRC.rglob("*.py")) if sdk in _imports_of(path)
    }


@pytest.mark.parametrize(
    ("sdk", "allowed"),
    [
        ("cloakbrowser", "meet/launcher.py"),
        ("kubernetes_asyncio", "bots/jobs.py"),
        ("minio", "storage/blob.py"),
        ("patchright", ""),
    ],
    ids=["browser-engine", "job-client", "blob-client", "removed-engine"],
)
def test_provider_sdk_stays_in_its_layer(sdk: str, allowed: str) -> None:
    expected = {allowed} if allowed else set()
    assert _importers_of(sdk) == expected
