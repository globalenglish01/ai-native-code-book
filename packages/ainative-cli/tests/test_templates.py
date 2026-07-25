from __future__ import annotations

import ast

import pytest

from ainative_cli.templates import TEMPLATES, get_template


def test_get_template_returns_known_template():
    template = get_template("minimal")
    assert template.name == "minimal"


def test_get_template_raises_key_error_with_available_types_listed():
    with pytest.raises(KeyError) as exc_info:
        get_template("nonexistent-type")
    message = str(exc_info.value)
    assert "minimal" in message
    assert "customer-service" in message


@pytest.mark.parametrize("template_name", sorted(TEMPLATES))
def test_every_template_main_py_is_syntactically_valid_python(template_name):
    template = TEMPLATES[template_name]
    # This will raise SyntaxError if the generated code is malformed.
    ast.parse(template.main_py)


@pytest.mark.parametrize("template_name", sorted(TEMPLATES))
def test_every_template_declares_at_least_ainative_core(template_name):
    template = TEMPLATES[template_name]
    assert "ainative-core" in template.packages


@pytest.mark.parametrize("template_name", sorted(TEMPLATES))
def test_every_template_has_non_empty_description(template_name):
    template = TEMPLATES[template_name]
    assert template.description.strip() != ""
