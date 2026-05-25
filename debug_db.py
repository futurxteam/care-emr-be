import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
django.setup()

from care.emr.models.organization import Organization
from care.facility.models.facility import Facility

print("--- ORGANIZATIONS ---")
for org in Organization.objects.all().order_by('level_cache', 'id'):
    print(f"ID: {org.id} | ExtID: {org.external_id} | Name: {org.name} | Level: {org.level_cache} | ParentID: {org.parent_id} | ParentCache: {org.parent_cache}")

print("\n--- FACILITIES ---")
for fac in Facility.objects.all():
    print(f"ID: {fac.id} | Name: {fac.name} | IsPublic: {fac.is_public} | GeoOrgID: {fac.geo_organization_id} | GeoOrgCache: {fac.geo_organization_cache}")
