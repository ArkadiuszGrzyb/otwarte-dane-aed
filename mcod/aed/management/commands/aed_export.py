from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from mcod.aed.export import (
    create_aed_export_df,
    create_aed_export_file,
    get_aed_export_dataset,
    get_aed_export_resource,
)
from mcod.aed.tasks import create_aed_export_resource_task
from mcod.organizations.models import Organization


class Command(BaseCommand):
    help = (
        "Publishes the AED devices export (XLSX) on the open data portal right away, "
        "using the same flow as the nightly task. Use --file-only to generate the file "
        "without touching the database, or --status to show the currently published export."
    )

    def add_arguments(self, parser):
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument(
            "--file-only",
            action="store_true",
            help="Only generate the XLSX file locally (no Dataset/Resource is created).",
        )
        mode.add_argument(
            "--status",
            action="store_true",
            help="Show the currently published AED export dataset and resource.",
        )
        parser.add_argument(
            "--output",
            type=Path,
            help="Output path for --file-only (default: the export file name in the current directory).",
        )

    def handle(self, *args, **options):
        if options["output"] and not options["file_only"]:
            raise CommandError("--output can only be used with --file-only.")

        if options["status"]:
            self.show_status()
        elif options["file_only"]:
            self.generate_file(options["output"])
        else:
            self.publish()

    def generate_file(self, output: Path):
        today = timezone.localdate()
        df = create_aed_export_df(today)
        file = create_aed_export_file(df, today)
        path = output or Path(file.name)
        if path.is_dir():
            path = path / file.name
        path.write_bytes(file.read())
        self.stdout.write(self.style.SUCCESS(f"Created {path.resolve()} with {len(df)} devices."))

    def publish(self):
        organization_pk = settings.AED_EXPORT_DATASET_OWNER_ORGANIZATION_PK
        organization = Organization.objects.filter(pk=organization_pk).first()
        if organization is None:
            raise CommandError(
                f"Institution with pk {organization_pk} not found. "
                f"Set AED_EXPORT_DATASET_OWNER_ORGANIZATION_PK to an existing institution."
            )

        result = create_aed_export_resource_task.delay()
        self.stdout.write(
            self.style.SUCCESS(
                f"AED export queued (task {result.id}), owner institution: {organization.pk} {organization.title}."
            )
        )
        self.stdout.write(
            f"The new resource is published after validation (first check in "
            f"{settings.AED_EXPORT_VALIDATION_CHECK_COUNTDOWN}s). Run with --status to check the result."
        )

    def show_status(self):
        dataset = get_aed_export_dataset()
        resource = get_aed_export_resource()
        if dataset is None:
            self.stdout.write("AED export dataset: not created yet.")
        else:
            self.stdout.write(f"AED export dataset: {dataset.pk} {dataset.frontend_absolute_url}")
        if resource is None:
            self.stdout.write("AED export resource: not published yet.")
        else:
            self.stdout.write(
                f"AED export resource: {resource.pk} (data date {resource.data_date}) {resource.frontend_absolute_url}"
            )
