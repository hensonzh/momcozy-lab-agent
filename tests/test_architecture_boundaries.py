import ast
from pathlib import Path
import re


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_IMPORT_ROOTS = ("backend", "app.modules")
PRODUCT_REPOSITORY_PATH = re.compile(r"(?i)(?:^|[./\\])backend(?:[/\\]|$)")


def test_runtime_source_never_imports_product_backend_implementation() -> None:
    violations: list[str] = []
    for path in (REPOSITORY_ROOT / "app").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            imported_modules: tuple[str, ...]
            if isinstance(node, ast.Import):
                imported_modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imported_modules = (node.module,)
            else:
                continue
            for imported_module in imported_modules:
                if any(
                    imported_module == root or imported_module.startswith(f"{root}.")
                    for root in FORBIDDEN_IMPORT_ROOTS
                ):
                    violations.append(
                        f"{path.relative_to(REPOSITORY_ROOT)}:{node.lineno}:{imported_module}"
                    )

    assert violations == []


def test_dependency_manifests_never_reference_product_backend_repository_path() -> None:
    manifests = [
        *sorted(REPOSITORY_ROOT.glob("requirements*.txt")),
        REPOSITORY_ROOT / "pyproject.toml",
    ]
    violations: list[str] = []
    for path in manifests:
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            candidate = line.strip()
            if not candidate or candidate.startswith("#"):
                continue
            if PRODUCT_REPOSITORY_PATH.search(candidate):
                violations.append(f"{path.name}:{line_number}:{candidate}")

    assert violations == []


def test_product_backend_http_paths_are_owned_by_single_adapter() -> None:
    owners: list[str] = []
    for path in (REPOSITORY_ROOT / "app").rglob("*.py"):
        if "/v1/internal/agent/" in path.read_text(encoding="utf-8"):
            owners.append(str(path.relative_to(REPOSITORY_ROOT)))

    assert owners == ["app/infrastructure/product_backend/client.py"]
