"""
Export of AED devices (GET /aed/devices) to open data ("otwarte dane").

Modelled on the main DGA resource (mcod.resources.dga_utils):
column constants -> nightly task aggregating into a DataFrame -> new Resource in a dedicated Dataset
-> AggregatedAEDInfo singleton pointing at the current one -> API endpoint (GET /aed/export).
"""

import datetime
import logging
from io import BytesIO
from typing import TYPE_CHECKING, List, Optional

import pandas as pd
from django.apps import apps
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction

from mcod.aed.models import AggregatedAEDInfo
from mcod.aed.queries import get_valid_aeds
from mcod.aed.schemas import AedSchema

if TYPE_CHECKING:
    from mcod.datasets.models import Dataset
    from mcod.resources.models import Resource

logger = logging.getLogger("mcod")

# Structure of the published file. Column names are the GET /aed/devices JSON keys, so the file
# and the API describe a device the same way. Kept explicit (not derived from AedSchema) because it is
# a public contract: adding a field to the schema must be a deliberate decision to publish it.
AED_EXPORT_COLUMNS: List[str] = [
    "id_aed",
    "numer_seryjny",
    "id_zrodlowe",
    "regon_dysponenta",
    "nazwa_dysponenta",
    "nazwa_miejsca",
    "teryt_gminy",
    "miejscowosc",
    "ulica",
    "numer_budynku",
    "kod_pocztowy",
    "szerokosc_geograficzna",
    "dlugosc_geograficzna",
    "opis_lokalizacji",
    "dostepnosc",
    "godziny_dostepnosci",
    "uwagi_dostepu",
    "stan_urzadzenia",
    "data_waznosci_baterii",
    "data_waznosci_elektrod",
    "data_przegladu",
    "data_weryfikacji",
    "zrodlo_danych",
    "data_aktualizacji",
    "status",
    "revision",
]


def get_aggregated_aed_info() -> Optional[AggregatedAEDInfo]:
    return AggregatedAEDInfo.objects.select_related("dataset", "resource").first()


def get_aed_export_resource() -> Optional["Resource"]:
    info = get_aggregated_aed_info()
    return info.export_resource if info else None


def get_aed_export_dataset() -> Optional["Dataset"]:
    info = get_aggregated_aed_info()
    return info.export_dataset if info else None


def create_aed_export_df(today: datetime.date) -> pd.DataFrame:
    """
    Devices exposed by GET /aed/devices, serialized exactly like the API does.

    The "open right now" filter of the endpoint is deliberately not applied: the file is a registry
    generated once a day, so it would only reflect the hour of the export. Consumers get
    godziny_dostepnosci to evaluate availability themselves.
    """
    records = AedSchema(many=True).dump(get_valid_aeds(today))
    # dtype=object keeps ints as ints and None as empty cells (no 1 -> 1.0 casting on NULL columns).
    df = pd.DataFrame(records, columns=AED_EXPORT_COLUMNS, dtype=object)
    if df.empty:
        logger.warning("AED export: no devices to export.")
    return df


def create_aed_export_file(df: pd.DataFrame, today: datetime.date) -> SimpleUploadedFile:
    output = BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter", options={"strings_to_formulas": False, "strings_to_urls": False}) as writer:
        df.to_excel(writer, index=False, sheet_name=settings.AED_EXPORT_XLSX_WORKSHEET_NAME, freeze_panes=(1, 0))
    file_name = f"{settings.AED_EXPORT_XLSX_FILE_NAME_PREFIX} {today:%Y%m%d}.xlsx"
    return SimpleUploadedFile(
        name=file_name,
        content=output.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


def create_aed_export_dataset() -> "Dataset":
    Organization = apps.get_model("organizations", "Organization")
    Dataset = apps.get_model("datasets", "Dataset")
    Category = apps.get_model("categories", "Category")
    Tag = apps.get_model("tags", "Tag")

    organization_pk = settings.AED_EXPORT_DATASET_OWNER_ORGANIZATION_PK
    try:
        organization = Organization.objects.get(pk=organization_pk)
    except Organization.DoesNotExist:
        logger.error(f"Can't create AED export dataset. Institution with pk {organization_pk} not found.")
        raise ObjectDoesNotExist("AED export dataset owner institution not found.")

    with transaction.atomic():
        dataset = Dataset.objects.create(
            title=settings.AED_EXPORT_DATASET_DEFAULT_TITLE,
            notes=settings.AED_EXPORT_DATASET_DEFAULT_DESC,
            organization=organization,
            update_notification_recipient_email=settings.AED_EXPORT_DATASET_UPDATE_NOTIFICATION_EMAIL,
            has_dynamic_data=False,
            has_high_value_data=False,
            has_high_value_data_from_ec_list=False,
            has_research_data=False,
            update_frequency="daily",
            status="published",
        )

        categories_titles = settings.AED_EXPORT_DATASET_CATEGORIES_TITLES
        categories = list(Category.objects.filter(title__in=categories_titles))
        for missing_title in set(categories_titles) - {c.title for c in categories}:
            logger.warning(f"No category with title {missing_title} found.")

        pl_lang = settings.LANGUAGES[0][0]
        tags = [Tag.objects.get_or_create(name=name, language=pl_lang)[0] for name in settings.AED_EXPORT_DATASET_TAGS_NAMES]

        dataset.categories.set(categories)
        dataset.tags.set(tags)
        dataset.save()

    logger.info(f"Created AED export dataset {dataset.pk}.")
    return dataset


def get_or_create_aed_export_dataset() -> "Dataset":
    """
    Returns the dedicated AED export Dataset, creating it (and the singleton) if it does not exist.
    The Dataset is registered in the singleton right away, so it is reused even if the first
    resource never passes validation.
    """
    with transaction.atomic():
        info = AggregatedAEDInfo.objects.select_for_update(of=("self",)).select_related("dataset").first()
        dataset = info.export_dataset if info else None
        if dataset is not None:
            return dataset

        dataset = create_aed_export_dataset()
        if info:
            info.dataset = dataset
            info.save()
        else:
            AggregatedAEDInfo.objects.create(dataset=dataset)
        return dataset


def create_aed_export_resource(file: SimpleUploadedFile, today: datetime.date) -> "Resource":
    """
    Creates a new export Resource (with its ResourceFile) in the dedicated Dataset.
    The ResourceFile post_save signal starts the standard file/data/link validation tasks.
    The Resource becomes the current one only after `publish_aed_export_resource`.
    """
    Resource = apps.get_model("resources", "Resource")
    ResourceFile = apps.get_model("resources", "ResourceFile")

    with transaction.atomic():
        dataset = get_or_create_aed_export_dataset()

        # Keep metadata edited by admins on the previous export.
        old_resource = get_aed_export_resource()
        if old_resource:
            title, description = old_resource.title, old_resource.description
        else:
            title, description = settings.AED_EXPORT_RESOURCE_DEFAULT_TITLE, settings.AED_EXPORT_RESOURCE_DEFAULT_DESC

        resource = Resource.objects.create(
            title=title,
            description=description,
            dataset=dataset,
            has_dynamic_data=False,
            has_high_value_data=False,
            has_high_value_data_from_ec_list=False,
            has_research_data=False,
            contains_protected_data=False,
            status="published",
            data_date=today,
        )
        resource_file = ResourceFile.objects.create(resource=resource, is_main=True, file=file)
        logger.debug(f"Created AED export resource {resource.pk} with file {resource_file.pk}.")

    return resource


def publish_aed_export_resource(new_resource: "Resource") -> None:
    """Points the singleton at the new validated Resource and removes the previous one."""
    with transaction.atomic():
        info = AggregatedAEDInfo.objects.select_for_update(of=("self",)).select_related("resource").first()
        old_resource = info.resource if info else None

        if info:
            info.resource = new_resource
            info.dataset = new_resource.dataset
            info.save()
        else:
            info = AggregatedAEDInfo.objects.create(resource=new_resource, dataset=new_resource.dataset)
        logger.info(f"AggregatedAEDInfo {info.pk} points at resource {new_resource.pk}.")

        if old_resource and old_resource.pk != new_resource.pk and not old_resource.is_removed:
            old_resource.delete()
            logger.info(f"Previous AED export resource {old_resource.pk} deleted.")


def discard_aed_export_resource(resource_pk: int) -> None:
    """Deletes an export Resource that did not pass validation (never the current one)."""
    Resource = apps.get_model("resources", "Resource")
    info = AggregatedAEDInfo.objects.first()
    if info and info.resource_id == resource_pk:
        logger.warning(f"Refusing to discard current AED export resource {resource_pk}.")
        return
    Resource.raw.filter(pk=resource_pk).delete()
    logger.info(f"Discarded AED export resource {resource_pk}.")
