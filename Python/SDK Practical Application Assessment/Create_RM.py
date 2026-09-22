from albert import Albert
import successHelpers.credentials as credentials

from albert.resources.inventory import InventoryItem, InventoryCategory, InventoryUnitCategory
from albert.resources.companies import Company
from albert.resources.lots import Lot

#%% Client Setup
# define tenant
tenant = "Albert Sandbox"

# define client
#url, token = credentials.get_base_url_and_bearer_token_for(tenant)
#client = Albert(base_url=url, token=token)

client = Albert(
    base_url=credentials.get_base_url_and_bearer_token_for("Albert Sandbox")[0],
    token=credentials.get_base_url_and_bearer_token_for("Albert Sandbox")[1]
)

#%% Dry Run
# Safety mode: True = preview only, False = actually writes
DRY_RUN = True

#%% Create Raw Material
inv = InventoryItem(
    name= "Feenstaub",
    company=Company(name="Christian Inc."),
    category=InventoryCategory.RAW_MATERIALS,
    unit_category=InventoryUnitCategory.MASS,
    tags=["SDK Training"]
    )

#%% Debug
if DRY_RUN:
    print("🔍 DRY RUN – not writing")
    print("Would create:", inv.name)
else:
    inv = client.inventory.create(inventory_item=inv, avoid_duplicates=True)
    print("✅ Done:", inv.id)
