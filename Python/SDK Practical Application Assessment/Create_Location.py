from albert import Albert
import successHelpers.credentials as credentials

from albert.resources.locations import Location

#%% Client Setup
tenant = "Albert Sandbox"
# Safety mode: True = preview only, False = actually writes
DRY_RUN = False

base_url, token = credentials.get_base_url_and_bearer_token_for(tenant)
client = Albert.from_token(base_url=base_url, token=token)

#%% Create Location
location = Location(
    name="Christian's Laboratory",
    address="Hermann-Bahlsen-Str. 15, Barsinghausen",
    latitude=52.3132,   # 52° 18' 47" N
    longitude=9.4711,   # 9° 28' 16" E
    country="DE",
)

if DRY_RUN:
    print(f"🔍 DRY RUN: would create location: {location.name}")
else:
    created = client.locations.create(location=location)
    print(f"✅ Created: {created.name} | ID: {created.id}")
