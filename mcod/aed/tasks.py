import logging

from django.apps import apps
from django.conf import settings
from django.utils import timezone

from mcod.aed.export import (
    create_aed_export_df,
    create_aed_export_file,
    create_aed_export_resource,
    discard_aed_export_resource,
    publish_aed_export_resource,
)
from mcod.core.tasks import extended_shared_task
from mcod.resources.dga_utils import (
    PendingValidationException,
    check_all_resource_validations_status,
)

logger = logging.getLogger("mcod")


@extended_shared_task(name="mcod.aed.tasks.create_aed_export_resource_task")
def create_aed_export_resource_task() -> int:
    """
    Nightly export of AED devices to open data.

    1. Builds a DataFrame from the devices exposed by GET /aed/devices and saves it as CSV.
    2. Creates a new Resource with that file in the dedicated AED Dataset (created if needed).
       All database changes are made in a single transaction, so nothing is left behind on error.
    3. Schedules `finalize_aed_export_resource_task`, which waits for the resource validation and
       only then switches the AggregatedAEDInfo singleton to the new Resource.
    """
    logger.info("AED export: starting.")
    today = timezone.localdate()

    df = create_aed_export_df(today)
    logger.info(f"AED export: {len(df)} devices.")
    file = create_aed_export_file(df, today)
    resource = create_aed_export_resource(file, today)
    logger.info(f"AED export: created resource {resource.pk}, waiting for validation.")

    finalize_aed_export_resource_task.apply_async(
        args=(resource.pk,),
        countdown=settings.AED_EXPORT_VALIDATION_CHECK_COUNTDOWN,
    )
    return resource.pk


@extended_shared_task(
    max_retries=10,
    retry_on_errors=(PendingValidationException,),
    retry_countdown=60,
    bind=True,
    name="mcod.aed.tasks.finalize_aed_export_resource_task",
)
def finalize_aed_export_resource_task(self, resource_pk: int) -> None:
    """
    Publishes the export Resource as the current one once all its validations succeeded.
    Retries while validation is pending; discards the Resource if validation fails or
    is still pending after the last retry, keeping the previous export in place.
    """
    Resource = apps.get_model("resources", "Resource")
    resource = Resource.raw.filter(pk=resource_pk).first()
    if resource is None:
        logger.warning(f"AED export: resource {resource_pk} not found, nothing to finalize.")
        return

    try:
        check_all_resource_validations_status(resource)
    except PendingValidationException:
        if self.request.retries >= self.max_retries:
            logger.error(f"AED export: resource {resource_pk} still not validated after last retry.")
            discard_aed_export_resource(resource_pk)
        raise
    except Exception:
        logger.exception(f"AED export: resource {resource_pk} validation failed.")
        discard_aed_export_resource(resource_pk)
        raise

    publish_aed_export_resource(resource)
    logger.info(f"AED export: resource {resource_pk} published.")
