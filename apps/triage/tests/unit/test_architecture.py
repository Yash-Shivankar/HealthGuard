"""Architecture tests.

AUDIT 7.1 states the rule that keeps the layering honest:

    controllers never touch the ORM
    services never touch HTTP
    repositories never contain business logic

A rule stated in a document is a rule that decays. These tests fail the build
when a layer reaches somewhere it should not, which is the only version of
that rule that survives contact with a deadline.

They are deliberately crude - import and source inspection, not a type system.
Crude and enforced beats elegant and aspirational.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

APPS_DIR = Path(__file__).resolve().parents[4] / "apps"

# Layer -> import prefixes that layer must not use.
FORBIDDEN_IMPORTS = {
    "controllers": (
        # Controllers go through services; they do not query.
        "django.db",
    ),
    "services": (
        # Services must be callable from a view, a management command or a
        # Celery task without changing.
        "rest_framework",
        "django.http",
        "django.shortcuts",
    ),
    "repositories": (
        # Repositories are data access. Business rules belong one layer up,
        # and HTTP does not belong anywhere near them.
        "rest_framework",
        "django.http",
        "django.shortcuts",
    ),
    "serializers": ("django.http", "django.shortcuts"),
}


def _python_files(layer: str) -> list[Path]:
    return [
        path
        for app in sorted(APPS_DIR.iterdir())
        if app.is_dir()
        for path in sorted((app / layer).rglob("*.py"))
        if path.name != "__init__.py"
    ]


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module)
    return modules


def _cases(layer: str):
    return [
        pytest.param(path, id=str(path.relative_to(APPS_DIR)).replace("\\", "/"))
        for path in _python_files(layer)
    ]


class TestLayerImports:
    @pytest.mark.parametrize("path", _cases("controllers"))
    def test_controllers_do_not_import_the_orm(self, path):
        offenders = _forbidden(path, "controllers")
        assert not offenders, f"{path.name} imports {offenders}; call a service instead"

    @pytest.mark.parametrize("path", _cases("services"))
    def test_services_do_not_import_http(self, path):
        offenders = _forbidden(path, "services")
        assert not offenders, (
            f"{path.name} imports {offenders}; raise a utils.exceptions.domain "
            "error and let the controller map it"
        )

    @pytest.mark.parametrize("path", _cases("repositories"))
    def test_repositories_do_not_import_http(self, path):
        offenders = _forbidden(path, "repositories")
        assert not offenders, f"{path.name} imports {offenders}"

    @pytest.mark.parametrize("path", _cases("serializers"))
    def test_serializers_do_not_import_http(self, path):
        offenders = _forbidden(path, "serializers")
        assert not offenders, f"{path.name} imports {offenders}"


def _forbidden(path: Path, layer: str) -> list[str]:
    forbidden = FORBIDDEN_IMPORTS[layer]
    return sorted(
        module
        for module in _imported_modules(path)
        if any(module == f or module.startswith(f + ".") for f in forbidden)
    )


class TestQueryLocation:
    """Only repositories may build QuerySets."""

    @pytest.mark.parametrize("layer", ["controllers", "services"])
    def test_no_manager_access_outside_repositories(self, layer):
        offenders = []
        for path in _python_files(layer):
            source = path.read_text(encoding="utf-8")
            for lineno, line in enumerate(source.splitlines(), start=1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if ".objects." in stripped:
                    offenders.append(f"{path.name}:{lineno}")
        assert not offenders, (
            f"ORM manager access outside repositories: {offenders}. "
            "Add a repository method instead."
        )


class TestDependencyDirection:
    """utils/ is generic; it must not know about any particular app."""

    def test_utils_does_not_import_apps(self):
        utils_dir = APPS_DIR.parent / "utils"
        offenders = []
        for path in utils_dir.rglob("*.py"):
            if any(m.startswith("apps.") for m in _imported_modules(path)):
                offenders.append(path.name)
        assert not offenders, f"utils/ imports from apps/: {offenders}"

    def test_ml_package_is_not_imported_at_runtime(self):
        """Training and serving stay separate (AUDIT 7.2).

        The app loads a versioned artefact; it must never import the training
        pipeline, or a model change would mean a web-tier deploy.
        """
        offenders = []
        for app in sorted(APPS_DIR.iterdir()):
            if not app.is_dir():
                continue
            for path in app.rglob("*.py"):
                if "tests" in path.parts:
                    continue
                if any(
                    m == "ml" or m.startswith("ml.") for m in _imported_modules(path)
                ):
                    offenders.append(str(path.relative_to(APPS_DIR)))
        assert not offenders, f"apps/ imports the ml training package: {offenders}"


class TestThrottleScopesAreConfigured:
    """Every ``throttle_scope`` a view declares must have a configured rate.

    This has now bitten three times. A view that sets ``throttle_scope`` as a
    class attribute resolves it regardless of the default throttle classes, and
    a missing key raises ``ImproperlyConfigured`` **at request time** - so it
    looks like a broken endpoint rather than a misconfiguration, and only the
    tests that happen to hit that view fail.
    """

    @staticmethod
    def _declared_scopes() -> set[str]:
        scopes = set()
        for path in APPS_DIR.rglob("controllers/*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Assign)
                    and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str)
                    and any(
                        isinstance(t, ast.Name) and t.id == "throttle_scope"
                        for t in node.targets
                    )
                ):
                    scopes.add(node.value.value)
        return scopes

    def test_every_declared_scope_has_a_rate(self, settings):
        configured = set(settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"])
        missing = self._declared_scopes() - configured
        assert not missing, (
            f"throttle_scope(s) {sorted(missing)} have no entry in "
            "DEFAULT_THROTTLE_RATES. Add them to config/settings/base.py and to "
            "the test settings override."
        )

    def test_at_least_one_scope_is_declared(self):
        """Guards the guard: an AST walk that silently matches nothing would
        make the test above pass forever.
        """
        assert self._declared_scopes(), "expected some view to declare a throttle_scope"
