"""Keep the gate offline: apps built in tests parse PDF/DOCX with the legacy pypdf /
python-docx double instead of the Docling worker. Mark a test `real_parsers` to opt out.

The patch is session-scoped so module- and session-scoped fixtures that build apps (e.g. the
evaluation corpus) are covered too; a function-scoped switch handles the marker."""

import pytest

from app.parsers import registry as _registry

_real_build_registry = _registry.build_registry
_use_real = {"value": False}


def _build_registry(settings):
    if _use_real["value"]:
        return _real_build_registry(settings)
    from tests.fakes.parsers import offline_registry

    return offline_registry()


@pytest.fixture(scope="session", autouse=True)
def _offline_parsers():
    patch = pytest.MonkeyPatch()
    patch.setattr(_registry, "build_registry", _build_registry)
    yield
    patch.undo()


@pytest.fixture(autouse=True)
def _real_parsers_marker(request):
    _use_real["value"] = request.node.get_closest_marker("real_parsers") is not None
    yield
    _use_real["value"] = False
