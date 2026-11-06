from albert import Albert
import successHelpers.credentials as credentials

from albert.resources.locations import Location
from albert.resources.storage_locations import StorageLocation

#%% Client Setup
tenant = "Albert Sandbox"
# Safety mode: True = preview only, False = actually writes
DRY_RUN = True

base_url, token = credentials.get_base_url_and_bearer_token_for(tenant)
client = Albert.from_token(base_url=base_url, token=token)

#%% Chose Location
parent = client.locations.get_by_id(id="LOC137")

#%% CreateStorage Location
storage_location = StorageLocation(
    name = "Christian's Bench",
    location=parent
    )

if DRY_RUN:
    print(f"🔍 DRY RUN: This would create {storage_location.name} in {parent.name}")
else:
    created = client.storage_locations.create(storage_location=storage_location)
    print(f"✅ Created: {created.name} | ID: {created.id}")
