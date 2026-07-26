# SPDX-FileCopyrightText: 2026 The Tether Authors <bioedca@u.northwestern.edu>
# SPDX-License-Identifier: GPL-3.0-or-later

"""Contract tests for groomable GitHub issue-form intake."""

from pathlib import Path

import pytest
import yaml

ISSUE_TEMPLATE_DIR = Path(__file__).resolve().parents[1] / ".github" / "ISSUE_TEMPLATE"
FORM_PATHS = tuple(
    path
    for path in sorted((*ISSUE_TEMPLATE_DIR.glob("*.yml"), *ISSUE_TEMPLATE_DIR.glob("*.yaml")))
    if path.name != "config.yml"
)
EXPECTED_FORMS = {
    "bug.yml",
    "docs.yml",
    "feature.yml",
    "maintenance.yml",
    "question.yml",
    "validation-oracle-failure.yml",
}
EXPECTED_TYPE_LABELS = {
    "bug.yml": "type:bug",
    "docs.yml": "type:docs",
    "feature.yml": "type:feature",
    "maintenance.yml": "type:chore",
    "question.yml": "type:question",
    "validation-oracle-failure.yml": "type:validation-oracle-failure",
}
WORK_FORM_NAMES = EXPECTED_FORMS - {"question.yml"}
ROUTING_FIELD_IDS = {"acceptance", "dependencies", "autonomy", "overlap", "scope"}
AUTONOMY_OPTIONS = {
    "agent-can-do-alone",
    "maintainer decision required",
    "external/human action required",
}
SAFETY_ATTESTATIONS = {
    "I searched existing issues and this is not a duplicate.",
    "I am not reporting a vulnerability publicly; security reports use the private advisory flow.",
    "I included no secrets or private, raw, unlicensed, user, or lab data.",
}


def _load(path: Path) -> dict[str, object]:
    form = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(form, dict), f"{path.name} must contain one YAML mapping"
    return form


def _controls(form: dict[str, object]) -> dict[str, dict[str, object]]:
    body = form.get("body")
    assert isinstance(body, list)
    controls: dict[str, dict[str, object]] = {}
    for item in body:
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            controls[item["id"]] = item
    return controls


def _labels(form: dict[str, object]) -> list[str]:
    labels = form.get("labels")
    assert isinstance(labels, list)
    assert all(isinstance(label, str) for label in labels)
    return labels


def _is_required(control: dict[str, object]) -> bool:
    validations = control.get("validations")
    return isinstance(validations, dict) and validations.get("required") is True


def _option_labels(control: dict[str, object]) -> set[str]:
    attributes = control.get("attributes")
    assert isinstance(attributes, dict)
    options = attributes.get("options")
    assert isinstance(options, list)
    labels: set[str] = set()
    for option in options:
        if isinstance(option, str):
            labels.add(option)
        else:
            assert isinstance(option, dict)
            label = option.get("label")
            assert isinstance(label, str)
            labels.add(label)
    return labels


def test_public_issue_form_taxonomy_is_complete() -> None:
    assert {path.name for path in FORM_PATHS} == EXPECTED_FORMS
    assert EXPECTED_TYPE_LABELS.keys() == EXPECTED_FORMS
    for path in FORM_PATHS:
        type_labels = [label for label in _labels(_load(path)) if label.startswith("type:")]
        assert type_labels == [EXPECTED_TYPE_LABELS[path.name]], path.name


@pytest.mark.parametrize("path", FORM_PATHS, ids=lambda path: path.stem)
def test_every_public_form_starts_in_backlog_only(path: Path) -> None:
    labels = _labels(_load(path))
    status_labels = [label for label in labels if label.startswith("status:")]

    assert len(labels) == len(set(labels)), f"{path.name} repeats a static label"
    assert status_labels == ["status:backlog"]
    assert "status:ready" not in labels


@pytest.mark.parametrize(
    "path",
    tuple(path for path in FORM_PATHS if path.name in WORK_FORM_NAMES),
    ids=lambda path: path.stem,
)
def test_work_forms_require_grooming_and_overlap_fields(path: Path) -> None:
    controls = _controls(_load(path))

    assert controls.keys() >= ROUTING_FIELD_IDS
    for field_id in ROUTING_FIELD_IDS:
        assert _is_required(controls[field_id]), f"{path.name}:{field_id} must be required"
    assert _option_labels(controls["autonomy"]) == AUTONOMY_OPTIONS
    for field_id in ("dependencies", "overlap"):
        attributes = controls[field_id].get("attributes")
        assert isinstance(attributes, dict)
        assert attributes.get("placeholder") == "none"


@pytest.mark.parametrize("path", FORM_PATHS, ids=lambda path: path.stem)
def test_every_public_form_requires_standard_safety_attestations(path: Path) -> None:
    safety = _controls(_load(path))["safety"]

    assert safety.get("type") == "checkboxes"
    attributes = safety.get("attributes")
    assert isinstance(attributes, dict)
    options = attributes.get("options")
    assert isinstance(options, list)
    assert _option_labels(safety) == SAFETY_ATTESTATIONS
    assert all(isinstance(option, dict) and option.get("required") is True for option in options)


def test_only_validation_failures_receive_an_initial_priority() -> None:
    for path in FORM_PATHS:
        priority_labels = [label for label in _labels(_load(path)) if label.startswith("priority:")]
        expected = ["priority:P0"] if path.name == "validation-oracle-failure.yml" else []
        assert priority_labels == expected, path.name


def test_every_milestone_selector_includes_m10() -> None:
    selectors: list[tuple[str, dict[str, object]]] = []
    for path in FORM_PATHS:
        milestone = _controls(_load(path)).get("milestone")
        if milestone is not None:
            selectors.append((path.name, milestone))

    assert {name for name, _ in selectors} == {"bug.yml", "feature.yml", "maintenance.yml"}
    for name, selector in selectors:
        assert "M10" in _option_labels(selector), name


def test_question_form_is_explicitly_not_worker_intake() -> None:
    form = _load(ISSUE_TEMPLATE_DIR / "question.yml")
    markdown = " ".join(
        str(item.get("attributes", {}).get("value", ""))
        for item in form["body"]
        if isinstance(item, dict) and item.get("type") == "markdown"
    )
    markdown = " ".join(markdown.split())

    assert "non-worker intake" in markdown
    assert "`status:ready`" in markdown
    assert "separate work issue" in markdown


def test_blank_issues_remain_disabled() -> None:
    config = _load(ISSUE_TEMPLATE_DIR / "config.yml")
    assert config.get("blank_issues_enabled") is False
