import uuid
from enum import Enum

from django.core.exceptions import ValidationError
from django.core.validators import MinLengthValidator
from django.db import models


def choices(enum_cls):
    return [(e.value, e.value) for e in enum_cls]


class Dostepnosc(str, Enum):
    calodobowa = "calodobowa"
    w_godzinach = "w_godzinach"
    ograniczona = "ograniczona"
    nieznana = "nieznana"


class StanUrzadzenia(str, Enum):
    sprawne = "sprawne"
    niesprawne = "niesprawne"
    nieznany = "nieznany"


class ZrodloDanych(str, Enum):
    formularz = "formularz"
    import_csv = "import_csv"
    zbior_dostawcy = "zbior_dostawcy"


class Status(str, Enum):
    draft = "draft"
    published = "published"


class Aed(models.Model):
    id_aed = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    numer_seryjny = models.CharField(max_length=64, unique=True, validators=[MinLengthValidator(3)])
    id_zrodlowe = models.CharField(max_length=255, null=True, blank=True)
    regon_dysponenta = models.CharField(max_length=255, null=True, blank=True)
    nazwa_dysponenta = models.CharField(max_length=255, null=True, blank=True)
    nazwa_miejsca = models.CharField(max_length=255, null=True, blank=True)
    teryt_gminy = models.CharField(max_length=7, null=True, blank=True)
    miejscowosc = models.CharField(max_length=255, null=True, blank=True)
    ulica = models.CharField(max_length=255, null=True, blank=True)
    numer_budynku = models.CharField(max_length=255, null=True, blank=True)
    kod_pocztowy = models.CharField(max_length=6, null=True, blank=True)
    szerokosc_geograficzna = models.FloatField(null=True, blank=True)
    dlugosc_geograficzna = models.FloatField(null=True, blank=True)
    opis_lokalizacji = models.CharField(max_length=255, null=True, blank=True)
    dostepnosc = models.CharField(max_length=32, choices=choices(Dostepnosc), null=True, blank=True)
    godziny_dostepnosci = models.CharField(max_length=255, null=True, blank=True)
    uwagi_dostepu = models.CharField(max_length=255, null=True, blank=True)
    stan_urzadzenia = models.CharField(max_length=32, choices=choices(StanUrzadzenia), null=True, blank=True)
    data_waznosci_baterii = models.DateField(null=True, blank=True)
    data_waznosci_elektrod = models.DateField(null=True, blank=True)
    data_przegladu = models.DateField(null=True, blank=True)
    data_weryfikacji = models.DateField(null=True, blank=True)
    zrodlo_danych = models.CharField(max_length=32, choices=choices(ZrodloDanych), null=True, blank=True)
    data_aktualizacji = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=32, choices=choices(Status), null=True, blank=True)
    revision = models.IntegerField(null=True, blank=True)

    class Meta:
        db_table = "aed"
        verbose_name = "AED"
        verbose_name_plural = "AED"

    def __str__(self):
        return self.numer_seryjny


class AggregatedAEDInfo(models.Model):
    """
    Singleton pointing at the current open data export of AED devices
    (the dedicated Dataset and its newest validated Resource).
    """

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    dataset = models.ForeignKey("datasets.Dataset", on_delete=models.SET_NULL, null=True, related_name="+")
    resource = models.ForeignKey("resources.Resource", on_delete=models.SET_NULL, null=True, related_name="+")

    class Meta:
        db_table = "aed_aggregated_info"
        verbose_name = "AED - eksport do otwartych danych"
        verbose_name_plural = "AED - eksport do otwartych danych"

    def save(self, *args, **kwargs):
        if not self.pk and AggregatedAEDInfo.objects.exists():
            raise ValidationError("There can be only one AggregatedAEDInfo instance")
        return super().save(*args, **kwargs)

    @property
    def export_dataset(self):
        if self.dataset and self.dataset.is_published and not self.dataset.is_removed:
            return self.dataset
        return None

    @property
    def export_resource(self):
        if self.resource and self.resource.is_published and not self.resource.is_removed:
            return self.resource
        return None
