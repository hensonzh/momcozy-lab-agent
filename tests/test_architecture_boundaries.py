import ast
from pathlib import Path
import re


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_IMPORT_ROOTS = ("backend", "app.modules")
PRODUCT_REPOSITORY_PATH = re.compile(r"(?i)(?:^|[./\\])backend(?:[/\\]|$)")
CAPABILITY_IMPLEMENTATION_PACKAGES = (
    "conversation_history_image",
    "device_guidance",
    "ibclc",
    "pump_models",
    "support_ticket",
)
RUNTIME_FORBIDDEN_IMPORT_ROOTS = (
    "app.agent",
    "app.bootstrap",
    "app.capabilities",
)
RUNTIME_DOMAIN_ACTION_MODULES = (
    "diary.py",
    "hospital_bag.py",
    "lactation.py",
    "plans.py",
    "profile.py",
)
RETIRED_FACT_MEMORY_PATHS = (
    "app/agent_runtime/facts",
    "app/agent_runtime/memory",
    "scripts/run_fact_extraction_worker.py",
    "scripts/run_memory_consolidation.py",
)
RETIRED_FACT_MEMORY_ENTRYPOINT_MARKERS = {
    "app/agent_runtime/runs/service.py": ("fact_enqueuer",),
    "app/api/agent_runtime/router.py": (
        '"/facts',
        '"/memories',
        "FactService",
        "MemoryService",
    ),
    "app/core/settings.py": ("fact_worker_",),
    "app/infrastructure/redis/worker_heartbeat.py": ("fact-worker",),
    "docker-compose.local.yml": ("fact-worker:", "memory-consolidation:"),
    "docker-compose.test.yml": ("fact-worker:", "memory-consolidation:"),
}


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


def test_agent_definitions_do_not_import_capability_implementations() -> None:
    violations: list[str] = []
    for path in (REPOSITORY_ROOT / "app" / "agent").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Import)
                and any(
                    alias.name == "app.capabilities"
                    or alias.name.startswith("app.capabilities.")
                    for alias in node.names
                )
            ) or (
                isinstance(node, ast.ImportFrom)
                and node.module is not None
                and (
                    node.module == "app.capabilities"
                    or node.module.startswith("app.capabilities.")
                )
            ):
                violations.append(
                    f"{path.relative_to(REPOSITORY_ROOT)}:{node.lineno}"
                )

    assert violations == []


def test_capabilities_do_not_import_agent_package() -> None:
    violations: list[str] = []
    for path in (REPOSITORY_ROOT / "app" / "capabilities").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Import)
                and any(
                    alias.name == "app.agent"
                    or alias.name.startswith("app.agent.")
                    for alias in node.names
                )
            ) or (
                isinstance(node, ast.ImportFrom)
                and node.module is not None
                and (
                    node.module == "app.agent"
                    or node.module.startswith("app.agent.")
                )
            ):
                violations.append(
                    f"{path.relative_to(REPOSITORY_ROOT)}:{node.lineno}"
                )

    assert violations == []


def test_agent_runtime_does_not_depend_on_application_definitions() -> None:
    runtime_root = REPOSITORY_ROOT / "app" / "agent_runtime"
    violations: list[str] = []
    for path in runtime_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imported_modules = (node.module,)
            else:
                continue
            for imported_module in imported_modules:
                if any(
                    imported_module == root
                    or imported_module.startswith(f"{root}.")
                    for root in RUNTIME_FORBIDDEN_IMPORT_ROOTS
                ):
                    violations.append(
                        f"{path.relative_to(REPOSITORY_ROOT)}:"
                        f"{node.lineno}:{imported_module}"
                    )

    assert violations == []


def test_durable_agent_loop_depends_on_execution_contract_not_sdk_adapter() -> None:
    path = (
        REPOSITORY_ROOT
        / "app"
        / "agent_runtime"
        / "orchestration"
        / "loop.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported_modules = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        str(node.module or "")
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }

    assert "openai_agents" not in imported_modules
    assert not any(
        module == "agents" or module.startswith("agents.")
        for module in imported_modules
    )


def test_application_delivery_and_composition_are_outside_runtime() -> None:
    runtime_root = REPOSITORY_ROOT / "app" / "agent_runtime"

    assert not (runtime_root / "composition.py").exists()
    assert not (runtime_root / "api").exists()
    assert all(
        not (runtime_root / "actions" / filename).exists()
        for filename in RUNTIME_DOMAIN_ACTION_MODULES
    )


def test_provider_runtime_does_not_read_application_settings() -> None:
    provider_source = (
        REPOSITORY_ROOT
        / "app"
        / "agent_runtime"
        / "providers"
        / "runtime.py"
    ).read_text(encoding="utf-8")

    assert "app.core.settings" not in provider_source


def test_fact_and_memory_background_features_are_removed() -> None:
    assert [
        relative_path
        for relative_path in RETIRED_FACT_MEMORY_PATHS
        if (REPOSITORY_ROOT / relative_path).exists()
    ] == []

    violations: list[str] = []
    for relative_path, markers in (
        RETIRED_FACT_MEMORY_ENTRYPOINT_MARKERS.items()
    ):
        source = (
            REPOSITORY_ROOT / relative_path
        ).read_text(encoding="utf-8")
        violations.extend(
            f"{relative_path}:{marker}"
            for marker in markers
            if marker in source
        )

    assert violations == []


def test_capability_implementations_are_owned_by_domain_packages() -> None:
    capabilities_root = REPOSITORY_ROOT / "app" / "capabilities"
    internal_root = capabilities_root / "_internal"

    assert {
        path.name for path in internal_root.iterdir() if path.is_file()
    } == {
        "__init__.py",
        "action_proposals.py",
        "execution.py",
        "model_schemas.py",
        "plans_actions.py",
        "schemas.py",
    }

    violations: list[str] = []
    for package_name in CAPABILITY_IMPLEMENTATION_PACKAGES:
        package_root = capabilities_root / package_name
        for filename in ("contracts.py", "handlers.py", "registry.py"):
            path = package_root / filename
            assert path.is_file()
            source = path.read_text(encoding="utf-8")
            if "app.capabilities._internal.contracts" in source:
                violations.append(str(path.relative_to(REPOSITORY_ROOT)))
            if "app.capabilities._internal.handlers" in source:
                violations.append(str(path.relative_to(REPOSITORY_ROOT)))

    assert violations == []


def test_model_visible_schemas_are_owned_by_domain_capabilities() -> None:
    capabilities_root = REPOSITORY_ROOT / "app" / "capabilities"

    assert not (capabilities_root / "model_input_schemas.py").exists()
    for package_name in (
        "conversation_history_image",
        "device_guidance",
        "ibclc",
        "lactation_analysis",
        "plans",
        "profile",
        "pump_models",
        "support_ticket",
        "timeline",
    ):
        package_root = capabilities_root / package_name
        assert (package_root / "model_schemas.py").is_file()
        assert (package_root / "module.py").is_file()


def test_plans_and_timeline_capabilities_have_distinct_ownership() -> None:
    plans_root = REPOSITORY_ROOT / "app" / "capabilities" / "plans"
    timeline_root = REPOSITORY_ROOT / "app" / "capabilities" / "timeline"

    assert timeline_root.is_dir()
    for path in plans_root.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "ScheduleTimeline" not in source
        assert "schedule_timeline" not in source
