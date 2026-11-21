from marshmallow import Schema, fields, validate

from mcod.aed.models import Dostepnosc, StanUrzadzenia, Status, ZrodloDanych


def _str(max_length=255, **kwargs):
    return fields.Str(allow_none=True, validate=validate.Length(max=max_length), **kwargs)


def _enum(enum_cls):
    return fields.Str(allow_none=True, validate=validate.OneOf([e.value for e in enum_cls]))


class AedSchema(Schema):
    class Meta:
        ordered = True

    id_aed = fields.UUID(dump_only=True)
    numer_seryjny = fields.Str(required=True, validate=validate.Length(min=3, max=64))
    id_zrodlowe = _str()
    regon_dysponenta = _str()
    nazwa_dysponenta = _str()
    nazwa_miejsca = _str()
    teryt_gminy = _str(7)
    miejscowosc = _str()
    ulica = _str()
    numer_budynku = _str()
    kod_pocztowy = _str(6)
    szerokosc_geograficzna = fields.Float(allow_none=True)
    dlugosc_geograficzna = fields.Float(allow_none=True)
    opis_lokalizacji = _str()
    dostepnosc = _enum(Dostepnosc)
    godziny_dostepnosci = _str()
    uwagi_dostepu = _str()
    stan_urzadzenia = _enum(StanUrzadzenia)
    data_waznosci_baterii = fields.Date(allow_none=True)
    data_waznosci_elektrod = fields.Date(allow_none=True)
    data_przegladu = fields.Date(allow_none=True)
    data_weryfikacji = fields.Date(allow_none=True)
    zrodlo_danych = _enum(ZrodloDanych)
    data_aktualizacji = fields.AwareDateTime(allow_none=True)
    status = _enum(Status)
    revision = fields.Int(allow_none=True)
