import json

import falcon
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone
from marshmallow import ValidationError

from mcod.aed import opening_hours
from mcod.aed.models import Aed
from mcod.aed.schemas import AedSchema



class AedView:
    csrf_exempt = True

    def on_get(self, request, response, *args, **kwargs):
        now = timezone.localtime()  # Europe/Warsaw

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
        today = now.date()
        aeds = Aed.objects.filter(
            id_aed__in=latest_ids,
            data_waznosci_elektrod__gt=today,
            data_waznosci_baterii__gt=today,
        ).order_by("numer_seryjny")
        # Only devices available right now. Valid OSM opening_hours text (e.g. "Mo-Fr 08:00-16:00")
        # is applied; empty or invalid text means no restriction (always open).
        aeds = [aed for aed in aeds if opening_hours.is_open(aed.godziny_dostepnosci, now)]
        self._json(response, falcon.HTTP_200, AedSchema(many=True).dump(aeds))

    def on_post(self, request, response, *args, **kwargs):
        schema = AedSchema()
        payload = _read_json(request)

        try:
            data = schema.load(payload)
        except ValidationError as err:
            self._json(response, falcon.HTTP_422, {"errors": err.messages})
            return

        # data_aktualizacji is managed by the server: always the moment of this write.
        data["data_aktualizacji"] = timezone.now()
        data.setdefault("revision", 0)

        if Aed.objects.filter(numer_seryjny=data["numer_seryjny"]).exists():
            self._json(response, falcon.HTTP_409, {"errors": {"numer_seryjny": ["AED with this serial number already exists."]}})
            return
        try:
            aed = Aed.objects.create(**data)
        except IntegrityError:
            self._json(response, falcon.HTTP_409, {"errors": {"numer_seryjny": ["AED with this serial number already exists."]}})
            return

        self._json(response, falcon.HTTP_201, schema.dump(aed))

    @staticmethod
    def _json(response, status, body):
        _json(response, status, body)


class AedDetailView:
    csrf_exempt = True

    def on_put(self, request, response, id_aed, *args, **kwargs):
        schema = AedSchema()
        payload = _read_json(request)

        # The record to edit is identified solely by id_aed from the URL.
        # id_aed in the body is optional, but if present it must match the URL.
        body_id = payload.pop("id_aed", None)
        if body_id is not None and str(body_id).lower() != str(id_aed):
            _json(response, falcon.HTTP_422, {"errors": {"id_aed": ["Does not match id_aed in the URL."]}})
            return

        try:
            data = schema.load(payload)
        except ValidationError as err:
            _json(response, falcon.HTTP_422, {"errors": err.messages})
            return

        # revision is managed by the server: ignore any client-supplied value.
        data.pop("revision", None)
        # data_aktualizacji is managed by the server: always the moment of this write.
        data["data_aktualizacji"] = timezone.now()

        if Aed.objects.filter(numer_seryjny=data["numer_seryjny"]).exclude(id_aed=id_aed).exists():
            _json(response, falcon.HTTP_409, {"errors": {"numer_seryjny": ["AED with this serial number already exists."]}})
            return

        try:
            with transaction.atomic():
                # Lock the row so concurrent PUTs can't both read the same revision.
                aed = Aed.objects.select_for_update().filter(id_aed=id_aed).first()
                if aed is None:
                    raise falcon.HTTPNotFound(description="AED with id {} does not exist.".format(id_aed))

                # PUT replaces the resource: optional fields missing from the payload are cleared.
                for name, field in schema.fields.items():
                    if field.dump_only or name == "revision":
                        continue
                    setattr(aed, name, data.get(name))
                aed.revision = (aed.revision or 0) + 1
                aed.save()
        except IntegrityError:
            _json(response, falcon.HTTP_409, {"errors": {"numer_seryjny": ["AED with this serial number already exists."]}})
            return

        _json(response, falcon.HTTP_200, schema.dump(aed))


def _read_json(request):
    try:
        payload = json.loads(request.bounded_stream.read() or b"{}")
    except ValueError:
        raise falcon.HTTPBadRequest(title="Invalid JSON", description="Request body must be valid JSON.")
    if not isinstance(payload, dict):
        raise falcon.HTTPBadRequest(title="Invalid JSON", description="Request body must be a JSON object.")
    return payload


def _json(response, status, body):
    response.status = status
    response.content_type = falcon.MEDIA_JSON
    response.text = json.dumps(body, ensure_ascii=False)
