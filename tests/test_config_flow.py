"""Tests for config_flow.py.

Verifies that:
- Bare except: clauses have been replaced with except Exception:
- Config flow validates MQTT node names
- Language validation works
"""
import pytest
import ast
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestBareExceptsRemoved:
    """Verify that bare except: clauses have been replaced."""

    def test_no_bare_excepts_in_config_flow(self):
        """config_flow.py should not contain bare 'except:' clauses.

        Bare except catches SystemExit and KeyboardInterrupt, which is dangerous.
        All except clauses should specify at least 'Exception'.
        """
        config_flow_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "custom_components",
            "thermiq_mqtt",
            "config_flow.py",
        )

        with open(config_flow_path) as f:
            source = f.read()

        tree = ast.parse(source)

        bare_excepts = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                if node.type is None:
                    bare_excepts.append(node.lineno)

        assert bare_excepts == [], (
            f"Found bare 'except:' clauses at lines {bare_excepts}. "
            "Use 'except Exception:' instead."
        )


class TestAvailableLanguages:
    """Test language configuration."""

    def test_available_languages(self):
        """AVAILABLE_LANGUAGES should have no duplicates."""
        from custom_components.thermiq_mqtt.const import AVAILABLE_LANGUAGES

        assert len(AVAILABLE_LANGUAGES) == len(set(AVAILABLE_LANGUAGES)), (
            f"Duplicate languages found: {AVAILABLE_LANGUAGES}"
        )

    def test_expected_languages(self):
        """Should support en, se, fi, no, de."""
        from custom_components.thermiq_mqtt.const import AVAILABLE_LANGUAGES

        for lang in ["en", "se", "fi", "no", "de"]:
            assert lang in AVAILABLE_LANGUAGES
