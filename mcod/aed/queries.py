import datetime

from django.db.models import F, QuerySet

from mcod.aed.models import Aed


def get_valid_aeds(today: datetime.date) -> QuerySet:
    """
    Devices published by GET /aed/devices, before the "open right now" filter.

    Shared by the API endpoint and the open data export, so both always expose the same set of devices.
    """
    # One record per numer_seryjny: PostgreSQL DISTINCT ON keeps the first row of each
    # group, so order by most recent data_aktualizacji (NULLs last, revision as tie-breaker).
    latest_ids = (
        Aed.objects.order_by(
            "numer_seryjny",
            F("data_aktualizacji").desc(nulls_last=True),
            F("revision").desc(nulls_last=True),
        )
        .distinct("numer_seryjny")
        .values("id_aed")
    )
    # Only devices with valid electrodes and battery (R07). Applied after deduplication,
    # so an older record never replaces a newest one that has expired.
    # Missing (NULL) dates do not pass the check.
    return Aed.objects.filter(
        id_aed__in=latest_ids,
        data_waznosci_elektrod__gt=today,
        data_waznosci_baterii__gt=today,
    ).order_by("numer_seryjny")
