import datetime
import os
from unittest import mock

import pytest
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist

from mcod.aed.export import (
    AED_EXPORT_COLUMNS,
    create_aed_export_dataset,
    create_aed_export_df,
    create_aed_export_file,
    create_aed_export_resource,
    discard_aed_export_resource,
    get_aed_export_resource,
    publish_aed_export_resource,
)
from mcod.aed.models import Aed, AggregatedAEDInfo
from mcod.aed.schemas import AedSchema
from mcod.aed.tasks import create_aed_export_resource_task, finalize_aed_export_resource_task
from mcod.categories.factories import CategoryFactory
from mcod.categories.models import Category
from mcod.core.tests.helpers.tasks import run_on_commit_events
from mcod.organizations.factories import OrganizationFactory
from mcod.resources.dga_utils import (
    FailedValidationException,
    PendingValidationException,
    check_all_resource_validations_status,
)
from mcod.resources.models import Resource

TODAY = datetime.date(2026, 11, 22)
FUTURE = TODAY + datetime.timedelta(days=30)
PAST = TODAY - datetime.timedelta(days=1)


@pytest.fixture
def aed_export_owner():
    if not Category.objects.filter(title="Zdrowie").exists():
        CategoryFactory.create(title="Zdrowie")
    return OrganizationFactory.create(pk=settings.AED_EXPORT_DATASET_OWNER_ORGANIZATION_PK)


@pytest.fixture
def aeds():
    return {
        "valid": Aed.objects.create(
            numer_seryjny="AED-001",
            miejscowosc="Warszawa",
            szerokosc_geograficzna=52.23,
            dlugosc_geograficzna=21.01,
            # Closed at night: the export must still contain it.
            godziny_dostepnosci="Mo-Fr 08:00-16:00",
            data_waznosci_elektrod=FUTURE,
            data_waznosci_baterii=FUTURE,
            revision=3,
        ),
        "expired_electrodes": Aed.objects.create(
            numer_seryjny="AED-002", data_waznosci_elektrod=PAST, data_waznosci_baterii=FUTURE
        ),
        "unknown_battery": Aed.objects.create(numer_seryjny="AED-003", data_waznosci_elektrod=FUTURE),
    }


def _export_file():
    return create_aed_export_file(create_aed_export_df(TODAY), TODAY)


def test_export_columns_match_api_schema():
    assert AED_EXPORT_COLUMNS == list(AedSchema().fields)


@pytest.mark.django_db
def test_create_aed_export_df_contains_only_valid_devices(aeds):
    df = create_aed_export_df(TODAY)

    assert df.columns.to_list() == AED_EXPORT_COLUMNS
    assert df["numer_seryjny"].to_list() == ["AED-001"]
    row = df.iloc[0]
    assert row["id_aed"] == str(aeds["valid"].id_aed)
    assert row["data_waznosci_elektrod"] == FUTURE.isoformat()
    assert row["revision"] == 3


@pytest.mark.django_db
def test_create_aed_export_df_empty():
    df = create_aed_export_df(TODAY)
    assert df.empty
    assert df.columns.to_list() == AED_EXPORT_COLUMNS


@pytest.mark.django_db
def test_create_aed_export_file_is_utf8_csv(aeds):
    file = _export_file()

    assert file.name == f"{settings.AED_EXPORT_CSV_FILE_NAME_PREFIX} 20261122.csv"
    assert file.content_type == "text/csv"
    lines = file.read().decode("utf-8").splitlines()
    assert lines[0] == ",".join(AED_EXPORT_COLUMNS)
    assert len(lines) == 2
    # NULLs are empty cells and integers are not cast to floats.
    assert ",3" in lines[1] and "3.0" not in lines[1]
    assert ",," in lines[1]


@pytest.mark.django_db
def test_create_aed_export_dataset_without_owner_organization():
    with pytest.raises(ObjectDoesNotExist):
        create_aed_export_dataset()


@pytest.mark.django_db
def test_create_aed_export_resource_creates_dataset_and_singleton(aed_export_owner, aeds):
    resource = create_aed_export_resource(_export_file(), TODAY)

    info = AggregatedAEDInfo.objects.get()
    assert info.resource is None  # becomes current only after validation
    assert info.dataset == resource.dataset
    dataset = resource.dataset
    assert dataset.organization == aed_export_owner
    assert dataset.title == settings.AED_EXPORT_DATASET_DEFAULT_TITLE
    assert dataset.update_frequency == "daily"
    assert set(dataset.categories.values_list("title", flat=True)) == {"Zdrowie"}
    assert set(dataset.tags.values_list("name", flat=True)) == set(settings.AED_EXPORT_DATASET_TAGS_NAMES)
    assert resource.title == settings.AED_EXPORT_RESOURCE_DEFAULT_TITLE
    assert resource.status == "published"
    assert resource.data_date == TODAY
    assert resource.main_file.name.endswith(".csv")


@pytest.mark.django_db
def test_create_aed_export_resource_reuses_dataset_and_metadata(aed_export_owner, aeds):
    first = create_aed_export_resource(_export_file(), TODAY)
    publish_aed_export_resource(first)
    Resource.objects.filter(pk=first.pk).update(title="Edited title", description="Edited description")

    second = create_aed_export_resource(_export_file(), TODAY)

    assert second.dataset_id == first.dataset_id
    assert second.title == "Edited title"
    assert second.description == "Edited description"
    assert AggregatedAEDInfo.objects.count() == 1


@pytest.mark.django_db
def test_publish_aed_export_resource_replaces_previous(aed_export_owner, aeds):
    first = create_aed_export_resource(_export_file(), TODAY)
    publish_aed_export_resource(first)
    assert get_aed_export_resource() == first

    second = create_aed_export_resource(_export_file(), TODAY)
    publish_aed_export_resource(second)

    assert get_aed_export_resource() == second
    assert Resource.raw.get(pk=first.pk).is_removed


@pytest.mark.django_db
def test_discard_aed_export_resource_keeps_current(aed_export_owner, aeds):
    current = create_aed_export_resource(_export_file(), TODAY)
    publish_aed_export_resource(current)
    pending = create_aed_export_resource(_export_file(), TODAY)

    discard_aed_export_resource(current.pk)
    discard_aed_export_resource(pending.pk)

    assert Resource.raw.filter(pk=current.pk).exists()
    assert not Resource.raw.filter(pk=pending.pk).exists()


@pytest.mark.django_db
def test_create_aed_export_resource_task_schedules_finalization(aed_export_owner, aeds):
    with mock.patch.object(finalize_aed_export_resource_task, "apply_async") as finalize:
        resource_pk = create_aed_export_resource_task()

    assert Resource.objects.filter(pk=resource_pk).exists()
    finalize.assert_called_once_with(args=(resource_pk,), countdown=settings.AED_EXPORT_VALIDATION_CHECK_COUNTDOWN)


@pytest.mark.django_db
class TestFinalizeAedExportResourceTask:
    CHECK = "mcod.aed.tasks.check_all_resource_validations_status"

    @pytest.fixture
    def resource(self, aed_export_owner, aeds):
        return create_aed_export_resource(_export_file(), TODAY)

    def test_publishes_validated_resource(self, resource):
        with mock.patch(self.CHECK):
            finalize_aed_export_resource_task.apply(args=(resource.pk,), throw=True)

        assert get_aed_export_resource() == resource

    def test_discards_resource_with_failed_validation(self, resource):
        with mock.patch(self.CHECK, side_effect=FailedValidationException):
            result = finalize_aed_export_resource_task.apply(args=(resource.pk,))

        assert result.failed()
        assert not Resource.raw.filter(pk=resource.pk).exists()
        assert get_aed_export_resource() is None

    def test_discards_resource_still_pending_after_last_retry(self, resource):
        task = finalize_aed_export_resource_task
        with mock.patch(self.CHECK, side_effect=PendingValidationException):
            result = task.apply(args=(resource.pk,), retries=task.max_retries)

        assert result.failed()
        assert not Resource.raw.filter(pk=resource.pk).exists()

    def test_missing_resource_is_noop(self):
        finalize_aed_export_resource_task.apply(args=(0,), throw=True)
        assert not AggregatedAEDInfo.objects.exists()


@pytest.mark.django_db
def test_aed_export_resource_passes_resource_validation(aed_export_owner, aeds):
    """The generated CSV goes through the real file/data/link validation pipeline."""
    resource = create_aed_export_resource(_export_file(), TODAY)
    run_on_commit_events()

    check_all_resource_validations_status(resource)  # raises if pending or failed
    assert resource.format == "csv"
    assert resource.has_table


@pytest.mark.django_db
def test_aed_export_end_to_end_generates_csv_file(aed_export_owner, aeds):
    """
    Full nightly flow: task -> CSV stored as ResourceFile -> real validation -> finalize -> published.
    The CSV lands in RESOURCES_MEDIA_ROOT/<YYYYMMDD>/ (in tests: test/media/resources/...).
    """
    with mock.patch("mcod.aed.tasks.timezone.localdate", return_value=TODAY), mock.patch.object(
        finalize_aed_export_resource_task, "apply_async"
    ) as finalize:
        resource_pk = create_aed_export_resource_task()

    run_on_commit_events()  # resource file/data/link validation tasks
    finalize_aed_export_resource_task.apply(args=finalize.call_args.kwargs["args"], throw=True)

    resource = get_aed_export_resource()
    assert resource.pk == resource_pk

    path = resource.main_file.path
    assert path.startswith(settings.RESOURCES_MEDIA_ROOT)
    # The resources storage replaces spaces with underscores (and adds a suffix on name collisions).
    file_name = os.path.basename(path)
    assert file_name.startswith(f"{settings.AED_EXPORT_CSV_FILE_NAME_PREFIX} 20261122".replace(" ", "_"))
    assert file_name.endswith(".csv")
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    assert lines[0] == ",".join(AED_EXPORT_COLUMNS)
    assert [line.split(",")[1] for line in lines[1:]] == ["AED-001"]
    print(f"\nAED export CSV: {path}")
