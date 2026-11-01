from django.core.management.base import BaseCommand
from django.db import transaction

from clinic.models import Appointment, Patient, Visit
from core.fields import EncryptedTextField


class Command(BaseCommand):
    help = "Re-encrypt every encrypted column with the NEWEST key in FIELD_ENCRYPTION_KEYS (run after adding a new key at the front)."

    def handle(self, *args, **opts):
        total = 0
        with transaction.atomic():
            for model in (Patient, Visit, Appointment):
                names = [f.name for f in model._meta.fields if isinstance(f, EncryptedTextField)]
                for obj in model.objects.all().iterator(chunk_size=200):
                    # force_insert/update_fields keep updated_at/updated_by untouched
                    model.objects.filter(pk=obj.pk).update(**{n: getattr(obj, n) for n in names})
                    total += 1
        self.stdout.write(self.style.SUCCESS(f"Re-encrypted {total} rows. You may now remove old keys."))
