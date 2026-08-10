from __future__ import annotations

import subprocess
from pathlib import Path


PATCH_SCRIPT = Path("scripts/pr22_review_fix.py")


def replace_once(path: Path, old: str, new: str, *, allow_done: bool = False) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count == 0 and allow_done and new in text:
        return
    if count != 1:
        raise SystemExit(f"{path}: expected one target, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def prepare_patch_script() -> None:
    text = PATCH_SCRIPT.read_text(encoding="utf-8")

    old = '''def replace_once(path: str, old: str, new: str) -> None:\n    text = read(path)\n    count = text.count(old)\n    if count != 1:\n        raise SystemExit(\n            f"{path}: expected one replacement target, found {count}: {old[:100]!r}"\n        )\n    write(path, text.replace(old, new, 1))\n'''
    new = '''def replace_once(path: str, old: str, new: str) -> None:\n    text = read(path)\n    count = text.count(old)\n    if count == 0 and new in text:\n        return\n    if count != 1:\n        raise SystemExit(\n            f"{path}: expected one replacement target, found {count}: {old[:100]!r}"\n        )\n    write(path, text.replace(old, new, 1))\n'''
    if text.count(old) != 1:
        raise SystemExit("replace_once helper patch target missing")
    text = text.replace(old, new, 1)

    old = '''    updated, count = re.subn(r"(?m)^owners:", "owner:", text, count=1)\n    if count != 1:\n        raise SystemExit(f"{doc}: expected one owners frontmatter key, found {count}")\n    write(doc, updated)\n'''
    new = '''    updated, count = re.subn(r"(?m)^owners:", "owner:", text, count=1)\n    if count == 0:\n        if text.startswith("---\\n") and not re.search(r"(?m)^owner:", text):\n            raise SystemExit(f"{doc}: frontmatter has neither owner nor owners")\n        continue\n    write(doc, updated)\n'''
    if text.count(old) != 1:
        raise SystemExit("owner patch target missing")
    text = text.replace(old, new, 1)

    old = '''old_probe = \'\'\'          for _ in $(seq 1 100); do\n            if python - <<'PY'\n          import socket\n          try:\n              with socket.create_connection(('127.0.0.1', 9092), timeout=0.2):\n                  pass\n          except OSError:\n              raise SystemExit(1)\n          PY\n            then\n              break\n            fi\n            sleep 0.2\n          done\n          docker inspect --format '{{.State.Status}}' ha-mcp-official | grep -qx running\n\'\'\'\n'''
    new = '''old_probe = \'\'\'          for _ in $(seq 1 100); do\n            if python - <<'PY'\n          import socket\n          try:\n              with socket.create_connection(('127.0.0.1', 9092), timeout=.2):\n                  pass\n          except OSError:\n              raise SystemExit(1)\n          PY\n            then break; fi\n            sleep .2\n          done\n          docker inspect --format '{{.State.Status}} {{.State.Running}} {{.State.ExitCode}}' ha-mcp-official\n\'\'\'\n'''
    if text.count(old) != 1:
        raise SystemExit("official-client probe declaration patch target missing")
    text = text.replace(old, new, 1)

    old = r'context = content[match_start:match_end].replace("\n", " ").strip()'
    new = r'context = content[match_start:match_end].replace("\\n", " ").strip()'
    if text.count(old) != 1:
        raise SystemExit("filesystem generated newline patch target missing")
    PATCH_SCRIPT.write_text(text.replace(old, new, 1), encoding="utf-8")


def postprocess() -> None:
    replace_once(
        Path("context_generator/snapshot.py"),
        "from datetime import UTC, datetime, timedelta\n",
        "from datetime import UTC, datetime, timedelta\nfrom pathlib import Path\n",
    )

    conftest = Path("tests/unit/conftest.py")
    text = conftest.read_text(encoding="utf-8")
    if "import copy\n" not in text:
        text = text.replace("import asyncio\n", "import asyncio\nimport copy\n", 1)
    old = '''    snapshot = {\n        "_TOOL_MANIFESTS": dict(manifests_module._TOOL_MANIFESTS),\n        "_ACTIVE_TOOL_NAMES": manifests_module._ACTIVE_TOOL_NAMES,\n        "_INACTIVE_REASONS": dict(manifests_module._INACTIVE_REASONS),\n    }\n    yield\n    manifests_module._TOOL_MANIFESTS = snapshot["_TOOL_MANIFESTS"]\n    manifests_module._ACTIVE_TOOL_NAMES = snapshot["_ACTIVE_TOOL_NAMES"]\n    manifests_module._INACTIVE_REASONS = snapshot["_INACTIVE_REASONS"]\n'''
    new = '''    manifests = copy.deepcopy(manifests_module._TOOL_MANIFESTS)\n    active = manifests_module._ACTIVE_TOOL_NAMES\n    reasons = dict(manifests_module._INACTIVE_REASONS)\n    try:\n        yield\n    finally:\n        manifests_module._TOOL_MANIFESTS.clear()\n        manifests_module._TOOL_MANIFESTS.update(manifests)\n        manifests_module._ACTIVE_TOOL_NAMES = active\n        manifests_module._INACTIVE_REASONS.clear()\n        manifests_module._INACTIVE_REASONS.update(reasons)\n'''
    if text.count(old) != 1:
        raise SystemExit("tests/unit/conftest.py: manifest fixture target missing")
    conftest.write_text(text.replace(old, new, 1), encoding="utf-8")

    manifests_test = Path("tests/unit/test_manifests.py")
    text = manifests_test.read_text(encoding="utf-8")
    text = text.replace("import copy\n", "", 1)
    text = text.replace("import tools.manifests as manifests_module\n", "", 1)
    fixture = '''@pytest.fixture(autouse=True)\ndef _restore_manifest_state():\n    manifests = copy.deepcopy(manifests_module._TOOL_MANIFESTS)\n    active = manifests_module._ACTIVE_TOOL_NAMES\n    reasons = dict(manifests_module._INACTIVE_REASONS)\n    try:\n        yield\n    finally:\n        manifests_module._TOOL_MANIFESTS.clear()\n        manifests_module._TOOL_MANIFESTS.update(manifests)\n        manifests_module._ACTIVE_TOOL_NAMES = active\n        manifests_module._INACTIVE_REASONS.clear()\n        manifests_module._INACTIVE_REASONS.update(reasons)\n\n\n'''
    if text.count(fixture) != 1:
        raise SystemExit("tests/unit/test_manifests.py: redundant fixture target missing")
    manifests_test.write_text(text.replace(fixture, "", 1), encoding="utf-8")

    Path("tests/unit/manifest_helpers.py").write_text(
        '''"""Shared helpers for tests that register synthetic operation manifests."""\n\nfrom tools.manifests import (\n    get_all_manifests,\n    make_manifest,\n    register_manifest,\n    set_active_tools,\n)\n\n\ndef register_test_manifest(name: str, **updates: object) -> None:\n    manifest = make_manifest(name)\n    manifest.update(updates)\n    active = set(get_all_manifests(active_only=True))\n    register_manifest(name, manifest)\n    set_active_tools(active | {name})\n''',
        encoding="utf-8",
    )

    helper = '''def _register(name: str, **updates: object) -> None:\n    manifest = make_manifest(name)\n    manifest.update(updates)\n    active = set(get_all_manifests(active_only=True))\n    register_manifest(name, manifest)\n    set_active_tools(active | {name})\n\n\n'''
    for test_path in (Path("tests/unit/test_manifests.py"), Path("tests/unit/test_invocation_kernel.py")):
        text = test_path.read_text(encoding="utf-8")
        if text.count(helper) != 1:
            raise SystemExit(f"{test_path}: duplicate register helper target missing")
        import_line = "from tests.unit.manifest_helpers import register_test_manifest as _register\n"
        text = text.replace("import pytest\n", "import pytest\n\n" + import_line, 1)
        test_path.write_text(text.replace(helper, "", 1), encoding="utf-8")

    compliance = Path("tests/unit/test_compliance_hardening.py")
    replace_once(
        compliance,
        '''def test_empty_active_catalog_stays_empty() -> None:\n    set_active_tools(set())\n    try:\n        assert get_all_manifests(active_only=True) == {}\n    finally:\n        set_active_tools(set(get_all_manifests()))\n''',
        '''def test_empty_active_catalog_stays_empty() -> None:\n    set_active_tools(set())\n    assert get_all_manifests(active_only=True) == {}\n''',
    )
    replace_once(
        compliance,
        '''    try:\n        with principal_scope(principal), pytest.raises(InvocationError) as exc:\n            kernel.invoke_sync("get_all_states", lambda: {"ok": True})\n        assert exc.value.code == "UNAVAILABLE_DEPENDENCY"\n        assert "backend unavailable" in str(exc.value)\n    finally:\n        set_active_tools(set(get_all_manifests()))\n''',
        '''    with principal_scope(principal), pytest.raises(InvocationError) as exc:\n        kernel.invoke_sync("get_all_states", lambda: {"ok": True})\n    assert exc.value.code == "UNAVAILABLE_DEPENDENCY"\n    assert "backend unavailable" in str(exc.value)\n''',
    )


if __name__ == "__main__":
    prepare_patch_script()
    subprocess.run(["python", str(PATCH_SCRIPT)], check=True)
    postprocess()
