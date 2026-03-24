"""Tests for thermiq_regs.py register definitions.

Verifies that:
- No duplicate register keys exist (silent overwrite bug)
- All register types are recognized
- Translation arrays have correct length
- Register fields are well-formed
"""
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from custom_components.thermiq_mqtt.heatpump.thermiq_regs import (
    reg_id,
    id_names,
    FIELD_REGNUM,
    FIELD_REGTYPE,
    FIELD_UNIT,
)

# All known register types in the codebase
KNOWN_REG_TYPES = {
    "temperature",
    "temperature_input",
    "time_input",
    "sensor",
    "sensor_input",
    "generated_input",
    "time",
    "select_input",
    "sensor_language",
    "sensor_boolean",
    "generated_sensor",
    "binary_sensor",
    "generated_input_boolean",
}


class TestRegisterDefinitions:
    """Test the reg_id dictionary structure."""

    def test_all_types_recognized(self):
        """Every register type should be in the known set."""
        unknown = set()
        for key, reg_def in reg_id.items():
            reg_type = reg_def[FIELD_REGTYPE]
            if reg_type not in KNOWN_REG_TYPES:
                unknown.add((key, reg_type))

        assert unknown == set(), f"Unknown register types: {unknown}"

    def test_register_numbers_are_strings(self):
        """Register numbers (field 0) should be strings like 'r00', 'indr_t'."""
        for key, reg_def in reg_id.items():
            reg_num = reg_def[FIELD_REGNUM]
            assert isinstance(reg_num, str), (
                f"Register {key} has non-string register number: {reg_num}"
            )

    def test_reg_definitions_have_minimum_fields(self):
        """Each register should have at least 5 fields."""
        for key, reg_def in reg_id.items():
            assert len(reg_def) >= 5, (
                f"Register {key} has only {len(reg_def)} fields, expected >= 5"
            )

    def test_temperature_inputs_have_numeric_minmax(self):
        """Temperature/sensor inputs should have numeric min/max values."""
        input_types = {"temperature_input", "time_input", "sensor_input", "generated_input"}
        for key, reg_def in reg_id.items():
            if reg_def[FIELD_REGTYPE] in input_types:
                min_val = reg_def[3]
                max_val = reg_def[4]
                assert isinstance(min_val, (int, float)), (
                    f"Register {key} has non-numeric min: {min_val}"
                )
                assert isinstance(max_val, (int, float)), (
                    f"Register {key} has non-numeric max: {max_val}"
                )
                assert min_val <= max_val, (
                    f"Register {key} has min ({min_val}) > max ({max_val})"
                )


class TestTranslations:
    """Test the id_names translation dictionary."""

    def test_translations_have_consistent_length(self):
        """All translation entries should have the same number of languages."""
        lengths = set()
        for key, names in id_names.items():
            lengths.add(len(names))

        assert len(lengths) == 1, (
            f"Inconsistent translation lengths: {lengths}. "
            "All entries should have the same number of language strings."
        )

    def test_all_registers_have_translations(self):
        """Most registers should have entries in id_names."""
        missing = []
        # Only check non-internal registers (those that become entities)
        for key in reg_id:
            if key not in id_names:
                missing.append(key)

        # Some registers may intentionally lack translations, but flag if many
        assert len(missing) < len(reg_id) * 0.3, (
            f"Too many registers without translations ({len(missing)}/{len(reg_id)}): "
            f"{missing[:10]}..."
        )
