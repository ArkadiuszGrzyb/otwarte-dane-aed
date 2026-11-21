from mcod.aed import views

PREFIX = "/aed"

# Like @RequestMapping("/aed") on a Spring controller: every path here is prefixed with PREFIX.
_routes = [
    ("/devices", views.AedView()),
]

routes = [(PREFIX + path, resource) for path, resource in _routes]
