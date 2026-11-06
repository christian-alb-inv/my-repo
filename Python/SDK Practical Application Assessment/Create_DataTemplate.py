from albert import Albert
import successHelpers.credentials as credentials

from albert.resources.data_columns import DataColumn
from albert.resources.data_templates import DataTemplate, DataColumnValue
from albert.resources.parameter_groups import DataType, ValueValidation

#%% Client Setup
tenant = "Albert Sandbox"
# Safety mode: True = preview only, False = actually writes
DRY_RUN = False

base_url, token = credentials.get_base_url_and_bearer_token_for(tenant)
client = Albert.from_token(base_url=base_url, token=token)

#%% Settings
TEMPLATE_NAME = "Christian's Mass Spectrometry"
UNIT_NAME = "ppm"
TAGS = ["SDK Training"]

# First 50 elements (Z = 1 to 50)
ELEMENTS = [
    ("Hydrogen", "H"), ("Helium", "He"), ("Lithium", "Li"), ("Beryllium", "Be"),
    ("Boron", "B"), ("Carbon", "C"), ("Nitrogen", "N"), ("Oxygen", "O"),
    ("Fluorine", "F"), ("Neon", "Ne"), ("Sodium", "Na"), ("Magnesium", "Mg"),
    ("Aluminum", "Al"), ("Silicon", "Si"), ("Phosphorus", "P"), ("Sulfur", "S"),
    ("Chlorine", "Cl"), ("Argon", "Ar"), ("Potassium", "K"), ("Calcium", "Ca"),
    ("Scandium", "Sc"), ("Titanium", "Ti"), ("Vanadium", "V"), ("Chromium", "Cr"),
    ("Manganese", "Mn"), ("Iron", "Fe"), ("Cobalt", "Co"), ("Nickel", "Ni"),
    ("Copper", "Cu"), ("Zinc", "Zn"), ("Gallium", "Ga"), ("Germanium", "Ge"),
    ("Arsenic", "As"), ("Selenium", "Se"), ("Bromine", "Br"), ("Krypton", "Kr"),
    ("Rubidium", "Rb"), ("Strontium", "Sr"), ("Yttrium", "Y"), ("Zirconium", "Zr"),
    ("Niobium", "Nb"), ("Molybdenum", "Mo"), ("Technetium", "Tc"), ("Ruthenium", "Ru"),
    ("Rhodium", "Rh"), ("Palladium", "Pd"), ("Silver", "Ag"), ("Cadmium", "Cd"),
    ("Indium", "In"), ("Tin", "Sn"),
]

#%% Look up unit (must already exist in Albert)
unit = client.units.get_by_name(name=UNIT_NAME, exact_match=True)
if unit is None:
    raise ValueError(f"Unit '{UNIT_NAME}' not found in {tenant}")

#%% Check if template already exists
existing = client.data_templates.get_by_name(name=TEMPLATE_NAME)
if existing is not None:
    raise ValueError(f"Data template '{TEMPLATE_NAME}' already exists: {existing.id}")

#%% Create data columns and build the template
column_values = []
for element_name, symbol in ELEMENTS:
    column_name = f"{element_name} – {symbol}"   # "Hydrogen – H"

    if DRY_RUN:
        print(f"🔍 DRY RUN: would get or create data column: {column_name}")
        continue

    data_column = client.data_columns.get_or_create(data_column=DataColumn(name=column_name))
    column_values.append(
        DataColumnValue(
            data_column_id=data_column.id,
            unit=unit,
            validation=[ValueValidation(datatype=DataType.NUMBER)],
        )
    )
    print(f"✅ Data column: {data_column.name} | ID: {data_column.id}")

#%% Create data template
if DRY_RUN:
    print(f"🔍 DRY RUN: would create data template '{TEMPLATE_NAME}' with {len(ELEMENTS)} columns (unit: {UNIT_NAME}, tags: {TAGS})")
else:
    # Tags need an Albert ID: get_or_create returns the existing tag or creates it
    tag_objects = [client.tags.get_or_create(tag=tag) for tag in TAGS]

    template = DataTemplate(
        name=TEMPLATE_NAME,
        data_column_values=column_values,
        tags=tag_objects,
    )
    created = client.data_templates.create(data_template=template)
    print(f"✅ Created: {created.name} | ID: {created.id}")
