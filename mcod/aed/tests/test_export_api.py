import pytest
from falcon import HTTP_NOT_FOUND, HTTP_OK
from falcon.testing import TestClient

from mcod.aed.models import AggregatedAEDInfo
from mcod.resources.factories import ResourceFactory

pytestmark = [pytest.mark.depends_on_component, pytest.mark.component_api]


@pytest.mark.django_db
def test_aed_export_not_available(client: TestClient):
    resp = client.simulate_get("/aed/export")
    assert resp.status == HTTP_NOT_FOUND


@pytest.mark.django_db
def test_aed_export_without_resource(client: TestClient):
    resource = ResourceFactory.create()
    AggregatedAEDInfo.objects.create(dataset=resource.dataset)

    resp = client.simulate_get("/aed/export")
    assert resp.status == HTTP_NOT_FOUND


@pytest.mark.django_db
@pytest.mark.parametrize(
    "resource_params, status_code",
    [
        (dict(status="published"), HTTP_OK),
        (dict(status="draft"), HTTP_NOT_FOUND),
        (dict(is_removed=True), HTTP_NOT_FOUND),
    ],
)
def test_aed_export(client: TestClient, resource_params, status_code):
    resource = ResourceFactory.create(**resource_params)
    AggregatedAEDInfo.objects.create(dataset=resource.dataset, resource=resource)

    resp = client.simulate_get("/aed/export")

    assert resp.status == status_code
    if status_code == HTTP_OK:
        assert resp.json["dataset_id"] == resource.dataset.id
        assert resp.json["dataset_slug"] == resource.dataset.slug
        assert resp.json["resource_id"] == resource.id
        assert resp.json["resource_slug"] == resource.slug
        assert resp.json["download_url"] == resource.download_url
