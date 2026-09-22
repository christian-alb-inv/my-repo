from albert import Albert
import successHelpers.credentials as credentials
import pandas as pd

from albert.resources.inventory import InventoryItem, InventoryCategory, InventoryUnitCategory
#from albert.resources.companies import Company
#from albert.resources.lots import Lot

#%% Client Setup
# define tenant
tenant = "Albert Sandbox"

# define client
#url, token = credentials.get_base_url_and_bearer_token_for(tenant)
#client = Albert(base_url=url, token=token)

client = Albert.from_token(
    base_url=credentials.get_base_url_and_bearer_token_for(tenant)[0],
    token=credentials.get_base_url_and_bearer_token_for(tenant)[1]
)
#%% Dry Run
# Safety mode: True = preview only, False = actually writes
DRY_RUN = False

#%% Read Excel
df = pd.read_excel("/Users/christian/Documents/GitHub/my-repo/Python/SDK Practical Application Assessment/SDK Application file.xlsx", sheet_name="RMs to create")
print(df.head())
print(df.columns.tolist())

#%% Create RM
# for _, row in df.iterrows(): #Der _ ist eine Python-Konvention für "diese Variable brauche ich nicht". iterrows() gibt immer zwei Werte zurück — den Zeilenindex und die Zeile selbst. Da wir den Index nicht brauchen, schreiben wir _ statt z.B. index.
#     name = row["RM Name"]
#     manufacturer = row["Manufacturer"]
#     alias = row["Alias"]
#     description = row["Description"]
#    # tags = row["Tags (semicolon-separated)"]
#     tags = [t.strip() for t in row["Tags (semicolon-separated)"].split(";")]
    
#     inv = InventoryItem(
#         name = name,
#         company=manufacturer,
#         category=InventoryCategory.RAW_MATERIALS,
#         unit_category=InventoryUnitCategory.MASS,
#         alias=alias,
#         description=description,
#         tags=tags        
#         )
#     print(inv.name)

# #%% Test Run
#     if DRY_RUN:
#         print(f"🔍 DRY RUN – would create: {name}")
#     else:
#         created = client.inventory.create(inventory_item=inv, avoid_duplicates=True)
#         print(f"✅ Created: {created.name} | ID: {created.id}")

inv = InventoryItem(
    name="Liquitint Blue HP",
    category=InventoryCategory.RAW_MATERIALS,
    unit_category=InventoryUnitCategory.MASS,
)
created = client.inventory.create(inventory_item=inv, avoid_duplicates=False)
print(f"✅ Created: {created.name} | ID: {created.id}")
