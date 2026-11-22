from mcod.aed import views

PREFIX = "/aed"

# Like @RequestMapping("/aed") on a Spring controller: every path here is prefixed with PREFIX.
_routes = [
    ("/devices", views.AedView()),
    ("/devices/{id_aed:uuid}", views.AedDetailView()),
    ("/export", views.AedExportView()),
]

routes = [(PREFIX + path, resource) for path, resource in _routes]
