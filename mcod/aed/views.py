import json

import falcon
from django.db import IntegrityError
from django.utils import timezone
from marshmallow import ValidationError

from mcod.aed.models import Aed
from mcod.aed.schemas import AedSchema


class AedView:
    csrf_exempt = True

    def on_get(self, request, response, *args, **kwargs):
        response.status = falcon.HTTP_200
        response.content_type = falcon.MEDIA_TEXT
        response.text = "aed endpoint"

    def on_post(self, request, response, *args, **kwargs):
        schema = AedSchema()
        try:
            payload = json.loads(request.bounded_stream.read() or b"{}")
        except ValueError:
            raise falcon.HTTPBadRequest(title="Invalid JSON", description="Request body must be valid JSON.")
        if not isinstance(payload, dict):
            raise falcon.HTTPBadRequest(title="Invalid JSON", description="Request body must be a JSON object.")

        try:
            data = schema.load(payload)
        except ValidationError as err:
            self._json(response, falcon.HTTP_422, {"errors": err.messages})
            return

        data.setdefault("data_aktualizacji", timezone.now())
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
        response.status = status
        response.content_type = falcon.MEDIA_JSON
        response.text = json.dumps(body, ensure_ascii=False)
