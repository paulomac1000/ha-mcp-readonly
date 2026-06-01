"""E2E tests: Context Generator v1.0 full pipeline."""

import os

import pytest

from .conftest import _server_running

_HA_URL = os.getenv("HA_URL") or "http://192.168.0.101:8123"
_HA_TOKEN = os.getenv("HA_TOKEN") or ""
_HA_CONFIG_PATH = os.getenv("HA_CONFIG_PATH") or "/var/apps/hassio/data/hassio"

pytestmark = pytest.mark.skipif(not _HA_TOKEN or not _server_running(), reason="HA_TOKEN and running server required for e2e tests")


def _run_generation(output_path):
    """Run context generation with real HA credentials."""
    os.environ["HA_URL"] = _HA_URL
    os.environ["HA_TOKEN"] = _HA_TOKEN
    os.environ["HA_CONFIG_PATH"] = _HA_CONFIG_PATH

    import context_generator.constants as c

    c.HA_URL = _HA_URL
    c.HA_TOKEN = _HA_TOKEN
    c.HA_CONFIG_PATH = _HA_CONFIG_PATH
    c.OUTPUT_FILE = output_path

    from context_generator.core import main

    main()


class TestContextGeneratorE2E:
    """Full context generator pipeline on real HA."""

    def test_generates_valid_markdown_file(self, tmp_output_path):
        """Context generation should produce a markdown file > 50KB."""
        _run_generation(tmp_output_path)

        assert os.path.exists(tmp_output_path)
        size = os.path.getsize(tmp_output_path)
        assert size > 50000, f"File too small: {size} bytes"

        with open(tmp_output_path) as f:
            content = f.read()

        assert "Home Assistant Context for AI" in content
        assert "Persons & Presence Tracking" in content
        assert "Zones & Geofencing" in content
        assert "Energy & Consumption" in content
        assert "Helper Inventory" in content
        assert "Services Catalog" in content
        assert "HACS & Custom Components" in content

    def test_generates_executive_summary(self, tmp_output_path):
        """Executive summary should contain key metrics."""
        _run_generation(tmp_output_path)

        with open(tmp_output_path) as f:
            content = f.read()

        assert "Executive Summary" in content
        assert "Total Entities" in content
        assert "Devices" in content
        assert "Automations" in content
        assert "Scripts" in content

    def test_sections_are_non_empty(self, tmp_output_path):
        """Key sections should contain actual data, not just headers."""
        _run_generation(tmp_output_path)

        with open(tmp_output_path) as f:
            content = f.read()

        sections = [
            "Automation Logic",
            "Entity Dependency Graph",
            "Log Analysis",
            "Quick Reference",
        ]
        for section in sections:
            found_section = False
            for header in content.split("\n"):
                if section in header and header.startswith("##"):
                    found_section = True
                    break
            assert found_section, f"Section '{section}' not found"

    def test_generate_online_mode(self, tmp_output_path):
        """Online mode should fetch data from HA API."""
        os.environ["HA_URL"] = _HA_URL
        os.environ["HA_TOKEN"] = _HA_TOKEN
        import context_generator.constants as c

        c.HA_URL = _HA_URL
        c.HA_TOKEN = _HA_TOKEN
        c.HA_CONFIG_PATH = _HA_CONFIG_PATH
        c.OUTPUT_FILE = tmp_output_path
        from context_generator.core import main

        main()

        assert os.path.exists(tmp_output_path)
        with open(tmp_output_path) as f:
            content = f.read()
        assert "Executive Summary" in content

    def test_generate_offline_no_api(self, tmp_output_path):
        """Offline mode should work without HA_TOKEN."""
        import context_generator.constants as c

        c.HA_URL = "http://nonexistent:8123"
        c.HA_TOKEN = ""
        c.HA_CONFIG_PATH = _HA_CONFIG_PATH
        c.OUTPUT_FILE = tmp_output_path
        from context_generator.core import main

        main()

        assert os.path.exists(tmp_output_path)
        with open(tmp_output_path) as f:
            content = f.read()
        assert "Home Assistant Context for AI" in content

    def test_generate_output_overwrites(self, tmp_output_path):
        """Second generation should overwrite, not append."""
        import context_generator.constants as c

        c.HA_URL = _HA_URL
        c.HA_TOKEN = _HA_TOKEN
        c.HA_CONFIG_PATH = _HA_CONFIG_PATH
        c.OUTPUT_FILE = tmp_output_path
        from context_generator.core import main

        main()
        size1 = os.path.getsize(tmp_output_path)

        main()
        size2 = os.path.getsize(tmp_output_path)

        assert abs(size1 - size2) < 500

    def test_empty_config_path_does_not_crash(self, tmp_output_path):
        """Empty config path should still generate without crashing."""
        import context_generator.constants as c

        c.HA_URL = "http://nonexistent:8123"
        c.HA_TOKEN = ""
        c.HA_CONFIG_PATH = "/nonexistent/path"
        c.OUTPUT_FILE = tmp_output_path
        from context_generator.core import main

        main()

        assert os.path.exists(tmp_output_path)
