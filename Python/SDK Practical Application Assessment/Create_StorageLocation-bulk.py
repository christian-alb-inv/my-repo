from albert import Albert
import pandas as pd
import successHelpers.credentials as credentials

from albert.resources.storage_locations import StorageLocation

#%% Client Setup
tenant = "Albert Sandbox"
# Safety mode: True = preview only, False = actually writes
DRY_RUN = False

base_url, token = credentials.get_base_url_and_bearer_token_for(tenant)
client = Albert.from_token(base_url=base_url, token=token)

#%% Choose Location
parent = client.locations.get_by_id(id="LOC137")
print(f"Parent location: {parent.name}")

#%% Read Excel
# header=5 -> Zeile 6 im Sheet enthält die Spaltennamen (Zeilen 1-5 werden übersprungen)
# dtype=str -> alles als Text lesen: 12 bleibt "12" statt 12.0
df = pd.read_excel(
    "/Users/christian/Documents/GitHub/my-repo/Python/SDK Practical Application Assessment/SDK Application file.xlsx",
    sheet_name="Storage Locations - Create",
    header=5,
    dtype=str,
)
# Nur Zeilen entfernen, in denen ALLE vier Spalten leer sind
# (how="all"). Zeilen mit nur Building + Room bleiben erhalten.
df = df.dropna(subset=["Building", "Room", "Cabinet", "Shelf"], how="all")
print(f"{len(df)} storage locations found in sheet")

#%% Build name
def build_name(row) -> str:
    """Building-Room, then ' - Cabinet' and ' - Shelf' only if they have values."""
    # Docstring oben: reine Info, wird von Python nicht ausgeführt

    # clean: macht aus jeder Zelle sauberen Text
    # - leere Zelle (NaN) -> "" (leerer Text)
    # - sonst als Text, ohne Leerzeichen am Anfang/Ende
    def clean(value):
        return "" if pd.isna(value) else str(value).strip()

    building = clean(row["Building"])
    room = clean(row["Room"])
    cabinet = clean(row["Cabinet"])
    shelf = clean(row["Shelf"])

    # Teile zusammenbauen: "Building-Room", dann Cabinet, dann Shelf.
    # Leere Teile ("" zählt als falsch) fallen durch "if p" heraus.
    parts = [p for p in [f"{building}-{room}" if building and room else building or room,
                         cabinet, shelf] if p]
    # Die Teile mit " - " verbinden, z.B. "12-123 - Flammable Cabinet 1 - Shelf 1"
    return " - ".join(parts)

#%% Create Storage Locations
count = 0  # Zähler: wie viele Zeilen wurden verarbeitet
for _, row in df.iterrows():  # _ = Zeilenindex, wird nicht gebraucht
    name = build_name(row)
    # Objekt nur im Speicher bauen, in Albert passiert hier noch nichts
    storage_location = StorageLocation(name=name, location=parent)

    if DRY_RUN:
        print(f"🔍 DRY RUN: would create {name} in {parent.name}")
    else:                                   # nur wenn DRY_RUN = False
        try:                                # versuche das hier
            created = client.storage_locations.get_or_create(storage_location=storage_location)
        except Exception as e:              # wenn dabei ein Fehler auftritt
            print(f"❌ Failed on {name}: {e}")
            raise                           # Fehler weitergeben, Skript stoppt
        print(f"✅ {created.name} | ID: {created.id}")   # nur wenn es geklappt hat
    count += 1  # Zähler um 1 erhöhen (count = count + 1)

print(f"Done. {count} rows processed (DRY_RUN={DRY_RUN})")
