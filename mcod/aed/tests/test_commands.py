from unittest import mock

import pytest
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from openpyxl import load_workbook

from mcod.aed.export import (
    AED_EXPORT_COLUMNS,
    create_aed_export_df,
    create_aed_export_file,
    create_aed_export_resource,
    publish_aed_export_resource,
)
from mcod.aed.tests.test_export import TODAY, aed_export_owner, aeds  # noqa: F401

COMMAND = "aed_export"
TASK = "mcod.aed.management.commands.aed_export.create_aed_export_resource_task"


@pytest.fixture
def frozen_today():
    with mock.patch("mcod.aed.management.commands.aed_export.timezone.localdate", return_value=TODAY):
        yield


@pytest.mark.django_db
def test_file_only_writes_xlsx_without_creating_resource(tmp_path, frozen_today, aeds):  # noqa: F811
    output = tmp_path / "aed.xlsx"

    with mock.patch(TASK) as task:
        call_command(COMMAND, "--file-only", "--output", str(output))

    task.delay.assert_not_called()
    rows = list(load_workbook(output)[settings.AED_EXPORT_XLSX_WORKSHEET_NAME].values)
    assert list(rows[0]) == AED_EXPORT_COLUMNS
    assert [row[1] for row in rows[1:]] == ["AED-001"]


@pytest.mark.django_db
def test_file_only_into_directory_uses_export_file_name(tmp_path, frozen_today):
    call_command(COMMAND, "--file-only", "--output", str(tmp_path))

    assert (tmp_path / f"{settings.AED_EXPORT_XLSX_FILE_NAME_PREFIX} 20261122.xlsx").exists()


def test_output_requires_file_only(tmp_path):
    with pytest.raises(CommandError):
        call_command(COMMAND, "--output", str(tmp_path))


@pytest.mark.django_db
def test_publish_queues_task(aed_export_owner, capsys):  # noqa: F811
    with mock.patch(TASK) as task:
        task.delay.return_value.id = "task-id"
        call_command(COMMAND)

    task.delay.assert_called_once_with()
    assert "task-id" in capsys.readouterr().out


@pytest.mark.django_db
def test_publish_without_owner_organization_fails():
    with mock.patch(TASK) as task, pytest.raises(CommandError):
        call_command(COMMAND)

    task.delay.assert_not_called()


@pytest.mark.django_db
def test_status_before_first_export(capsys):
    call_command(COMMAND, "--status")

    out = capsys.readouterr().out
    assert "dataset: not created yet" in out
    assert "resource: not published yet" in out


@pytest.mark.django_db
def test_status_shows_published_export(aed_export_owner, aeds, capsys):  # noqa: F811
    resource = create_aed_export_resource(create_aed_export_file(create_aed_export_df(TODAY), TODAY), TODAY)
    publish_aed_export_resource(resource)

    call_command(COMMAND, "--status")

    out = capsys.readouterr().out
    assert resource.dataset.frontend_absolute_url in out
    assert resource.frontend_absolute_url in out
