"""
transfer_dts_and_pgs.py
=======================
Kopiert Data Templates (DTs) und/oder Parameter Groups (PGs)
von einem Albert-Tenant in einen anderen.

Was wird kopiert:
  - Name, Beschreibung, Tags
  - Metadata (Custom Fields) — Keys müssen im Dest-Tenant bereits existieren
  - Data Columns (DTs) — werden per Name gesucht, bei Fehlen automatisch
    angelegt inkl. Unit (via get_or_create)
  - Parameter (DTs + PGs) — werden via get_or_create angelegt

Verhalten bei bereits existierenden Records: SKIP (kein Überschreiben)

Usage:
  1. SOURCE_TENANT / DEST_TENANT auf den exakten Sektionsnamen in credentials.toml setzen
  2. DT_IDS und PG_IDS befüllen
  3. DRY_RUN = True zum Prüfen, dann False zum tatsächlichen Schreiben
"""

import tomllib
import pathlib

from albert import Albert
from albert.resources.data_columns import DataColumn
from albert.resources.data_templates import DataTemplate, DataColumnValue
from albert.resources.parameter_groups import DataType, ParameterGroup, ParameterValue
from albert.resources.parameters import Parameter
from albert.resources.units import Unit

# ── Configuration ──────────────────────────────────────────────────────────────

# Pfad zur TOML-Datei mit Credentials
CREDENTIALS_FILE = pathlib.Path("/Users/christian/credentials.toml")

# Muss exakt dem Sektionsnamen in der TOML entsprechen, z.B. "Albert Sandbox"
SOURCE_TENANT = "Albert Production"
DEST_TENANT   = "Albert Sandbox"

# Sicherheitsmodus: True = nur Vorschau, False = schreibt tatsächlich
DRY_RUN = True

# IDs der zu transferierenden Records (leere Liste = Typ wird übersprungen)
DT_IDS: list[str] = [
    # "DAT1",
    # "DAT7",
]
PG_IDS: list[str] = [
    # "PRG1",
    # "PRG5",
]

# ── Credentials laden ──────────────────────────────────────────────────────────

def load_credentials(toml_path: pathlib.Path) -> dict:
    """Lädt die TOML-Datei und gibt den Inhalt als Dict zurück."""
    resolved = toml_path.resolve()
    if not resolved.exists():
        raise FileNotFoundError(
            f"Credentials-Datei nicht gefunden: {resolved}\n"
            f"Bitte CREDENTIALS_FILE im Skript anpassen."
        )
    with open(resolved, "rb") as f:
        return tomllib.load(f)


def make_client(tenant_name: str, creds: dict) -> Albert:
    """Erstellt einen Albert-Client für den angegebenen Tenant-Namen."""
    if tenant_name not in creds:
        available = list(creds.keys())
        raise KeyError(
            f"Tenant '{tenant_name}' nicht in der TOML-Datei gefunden.\n"
            f"Verfügbare Sektionen: {available}"
        )
    entry = creds[tenant_name]
    return Albert.from_token(
        base_url=entry["url"],
        token=entry["token"],
    )


# ── Clients initialisieren ─────────────────────────────────────────────────────

creds = load_credentials(CREDENTIALS_FILE)
src = make_client(SOURCE_TENANT, creds)
dst = make_client(DEST_TENANT, creds)

# ── Helpers ────────────────────────────────────────────────────────────────────

def copy_metadata(src_metadata: dict | None) -> dict:
    """
    Kopiert den Metadata-Dict direkt vom Source- in den Dest-Tenant.

    Hinweis: Die Custom Fields (Keys) müssen im Dest-Tenant bereits existieren.
    List-Werte (LST-IDs) werden ebenfalls direkt übernommen — ob der
    Dest-Tenant diese IDs kennt, hängt vom jeweiligen Setup ab.
    """
    return dict(src_metadata) if src_metadata else {}


def resolve_unit_in_dest(src_unit: Unit | None) -> Unit | None:
    """
    Stellt sicher, dass eine Unit im Dest-Tenant existiert.

    Strategie: get_or_create per Name. Unit-IDs sind tenant-spezifisch
    und können nicht direkt übertragen werden — nur der Name ist portabel.
    Falls die Source-Unit keinen Namen hat, wird None zurückgegeben.
    """
    if src_unit is None:
        return None

    unit_name = getattr(src_unit, "name", None)
    if not unit_name:
        return None

    # Only pass category if it has a valid non-None value
    # The API rejects category=null and requires a value from its allowed list
    unit_kwargs = {"name": src_unit.name}
    src_symbol = getattr(src_unit, "symbol", None)
    src_category = getattr(src_unit, "category", None)
    if src_symbol:
        unit_kwargs["symbol"] = src_symbol
    if src_category:
        unit_kwargs["category"] = src_category

    dst_unit = dst.units.get_or_create(unit=Unit(**unit_kwargs))
    return dst_unit


def datatype_to_column_type(dcv: DataColumnValue) -> str | None:
    """
    Liest den DataType aus der validation-Liste eines DataColumnValue
    und mappt ihn auf den Albert Column-Typ-String.

    DataColumnValue.validation ist die zuverlässigste Quelle für den Typ,
    da DataColumn.type von der API nicht immer zurückgeliefert wird.

    Mapping:
      DataType.NUMBER  -> "Numeric"
      DataType.STRING  -> "Text"
      DataType.ENUM    -> "List"
      DataType.DATE    -> "Date"
      alles andere     -> None (Albert setzt Default)

    Hinweis: DataColumnType-Enum ist in manchen SDK-Versionen nicht
    exportiert — daher direkte String-Werte.
    """
    validation = getattr(dcv, "validation", None) or []
    if not validation:
        return None

    datatype = getattr(validation[0], "datatype", None)
    if datatype is None:
        return None

    datatype_str = datatype.value if hasattr(datatype, "value") else str(datatype)
    mapping = {
        "number": "Numeric",
        "string": "Text",
        "enum":   "List",
        "date":   "Date",
    }
    return mapping.get(datatype_str.lower(), None)


def get_or_create_data_column(src_col: DataColumn, dcv: DataColumnValue) -> DataColumn:
    """
    Sucht eine DataColumn per Name im Dest-Tenant.
    Legt sie an falls sie nicht existiert.

    Typ-Strategie (Option B):
      DataColumn.type wird von der API nicht zuverlaessig zurueckgeliefert.
      Stattdessen lesen wir den Typ aus dcv.validation (dem DataColumnValue
      auf dem Source-DT) und mappen ihn auf DataColumnType.

    Hinweis: DataColumnCollection hat kein get_or_create(), daher
    try/except-Pattern analog zu bestehenden Projektskripten.
    """
    # Zuerst per Name suchen
    existing = dst.data_columns.get_by_name(name=src_col.name)
    if existing:
        return existing

    # Typ aus dcv.validation ableiten (zuverlaessiger als src_col.type)
    col_type: str | None = datatype_to_column_type(dcv)
    if col_type:
        print(f"      [TYPE] Column-Typ aus validation abgeleitet: {col_type}")
    else:
        print(f"      [TYPE] Kein Typ ableitbar — Albert setzt Default")

    # Unit im Dest-Tenant sicherstellen (IDs sind tenant-spezifisch)
    dst_unit = resolve_unit_in_dest(getattr(src_col, "unit", None))
    if dst_unit:
        print(f"      [UNIT] '{dst_unit.name}' im Dest-Tenant gesichert (id={dst_unit.id})")

    # Neue Column anlegen
    new_col = DataColumn(
        name=src_col.name,
        type=col_type,
        unit=dst_unit,
        options=getattr(src_col, "options", None),
    )

    try:
        created = dst.data_columns.create(data_column=new_col)
        print(f"      [CREATED] Column '{src_col.name}' neu angelegt (id={created.id})")
        return created
    except Exception as e:
        # Fallback: nochmal per Name holen (Race condition oder API-Fehler)
        fallback = dst.data_columns.get_by_name(name=src_col.name)
        if fallback:
            print(f"      [OK] Column '{src_col.name}' per Fallback gefunden (id={fallback.id})")
            return fallback
        raise RuntimeError(
            f"Column '{src_col.name}' konnte weder gefunden noch angelegt werden: {e}"
        )


# ── Data Templates transferieren ───────────────────────────────────────────────

def transfer_data_templates(ids: list[str]) -> None:
    print(f"\n{'='*60}")
    print(f"  Data Templates ({len(ids)} IDs)")
    print(f"{'='*60}")

    for dt_id in ids:
        # Vollständigen Record vom Source-Tenant laden
        try:
            src_dt: DataTemplate = src.data_templates.get_by_id(id=dt_id)
        except Exception as e:
            print(f"  [ERROR] {dt_id} konnte nicht geladen werden: {e}")
            continue

        print(f"\n  → {dt_id}: '{src_dt.name}'")

        # Prüfen ob im Dest-Tenant bereits vorhanden (Match per Name)
        existing = dst.data_templates.get_by_name(name=src_dt.name)
        if existing:
            print(f"    [SKIP] Existiert bereits im Dest-Tenant (id={existing.id})")
            continue

        # DRY RUN: Vorschau ausgeben, nicht schreiben
        if DRY_RUN:
            col_info = []
            for dcv in (src_dt.data_column_values or []):
                try:
                    src_col = src.data_columns.get_by_id(id=dcv.data_column_id)
                    # Typ aus dcv.validation (zuverlässiger als src_col.type)
                    col_type: str = datatype_to_column_type(dcv) or "—"
                    # Unit sitzt auf dcv.unit direkt (die Ergebnis-Unit, z.B. MPa)
                    unit_name = getattr(getattr(dcv, "unit", None), "name", None)
                    col_info.append(f"{src_col.name} (type={col_type}, unit={unit_name or '—'})")
                except Exception as e:
                    col_info.append(f"{dcv.name} (⚠️  nicht ladbar: {e})")

            # Parameter: Name + Typ + Unit + Default Value anzeigen
            param_info = []
            for pv in (src_dt.parameter_values or []):
                name = (
                    getattr(pv, "original_name", None)
                    or getattr(pv, "name", None)
                    or getattr(getattr(pv, "parameter", None), "name", None)
                    or str(pv.id)
                )
                # Typ aus validation ableiten (DataType.NUMBER -> "#", STRING -> "T")
                pv_validation = getattr(pv, "validation", None) or []
                if pv_validation:
                    pv_dtype = getattr(pv_validation[0], "datatype", None)
                    pv_dtype_str = pv_dtype.value if hasattr(pv_dtype, "value") else ""
                    pv_type = {"number": "#", "string": "T", "enum": "☰", "date": "📅"}.get(pv_dtype_str.lower(), "?")
                else:
                    pv_type = "?"
                pv_unit = getattr(getattr(pv, "unit", None), "name", None) or "—"
                pv_value = getattr(pv, "value", None) or "—"
                param_info.append(f"{pv_type} {name} (value={pv_value}, unit={pv_unit})")
            print(
                f"    [DRY RUN] Würde erstellen:\n"
                f"      Name:        {src_dt.name}\n"
                f"      Beschreibung:{src_dt.description or '—'}\n"
                f"      Tags:        {[t.tag for t in (src_dt.tags or [])]}\n"
                f"      Columns:     {col_info}\n"
                f"      Parameter:   {param_info}\n"
                f"      Metadata:    {list((src_dt.metadata or {}).keys())}"
            )
            continue

        # Neues DT anlegen (nur skalare Felder — Columns/Parameter danach)
        new_dt = DataTemplate(
            name=src_dt.name,
            description=src_dt.description,
            tags=src_dt.tags or [],
            metadata=copy_metadata(src_dt.metadata),
        )

        try:
            created_dt = dst.data_templates.create(data_template=new_dt)
            print(f"    [CREATED] id={created_dt.id}")
        except Exception as e:
            print(f"    [ERROR] Erstellen fehlgeschlagen: {e}")
            continue

        # Data Columns hinzufügen
        # Pattern analog zu Parametern: jede Column einzeln hinzufügen
        # und direkt danach Validation + Unit per update() setzen.
        if src_dt.data_column_values:
            added_col_count = 0
            for dcv in src_dt.data_column_values:
                # Source-Column vollständig laden
                try:
                    src_col = src.data_columns.get_by_id(id=dcv.data_column_id)
                except Exception as e:
                    print(f"    [WARN] Source-Column '{dcv.name}' konnte nicht geladen werden: {e} — übersprungen")
                    continue

                # Column im Dest-Tenant holen oder anlegen
                try:
                    dst_col = get_or_create_data_column(src_col=src_col, dcv=dcv)
                except Exception as e:
                    print(f"    [WARN] Column '{src_col.name}': {e} — übersprungen")
                    continue

                # Column einzeln zum DT hinzufügen
                try:
                    working_dt_col = dst.data_templates.add_data_columns(
                        data_template_id=created_dt.id,
                        data_columns=[DataColumnValue(data_column_id=dst_col.id)],
                    )
                except Exception as e:
                    print(f"    [WARN] add_data_columns für '{src_col.name}' fehlgeschlagen: {e}")
                    continue

                # Target-DataColumnValue auf dem zurückgegebenen DT finden (per Column-ID)
                target_dcv = next(
                    (x for x in (working_dt_col.data_column_values or []) if x.data_column_id == dst_col.id),
                    None
                )
                if target_dcv is None:
                    print(f"    [WARN] target_dcv für '{src_col.name}' nicht gefunden nach add_data_columns")
                    added_col_count += 1
                    continue

                # Pass 1: Validation setzen (bestimmt # vs T für die Column)
                # Validation sitzt auf dcv (dem Source-DataColumnValue), nicht auf src_col
                src_col_validation = getattr(dcv, "validation", None) or []
                if src_col_validation:
                    target_dcv.validation = src_col_validation
                    try:
                        working_dt_col = dst.data_templates.update(data_template=working_dt_col)
                        target_dcv = next(
                            (x for x in (working_dt_col.data_column_values or []) if x.data_column_id == dst_col.id),
                            target_dcv
                        )
                    except Exception as e:
                        print(f"    [WARN] Column-Validation-Update für '{src_col.name}' fehlgeschlagen: {e}")

                # Pass 2: Unit setzen (sitzt ebenfalls auf dcv, nicht auf src_col)
                src_dcv_unit = getattr(dcv, "unit", None)
                if src_dcv_unit:
                    try:
                        dst_unit = resolve_unit_in_dest(src_dcv_unit)
                        if dst_unit:
                            target_dcv.unit = dst_unit
                            dst.data_templates.update(data_template=working_dt_col)
                    except Exception as e:
                        print(f"    [WARN] Column-Unit für '{src_col.name}' fehlgeschlagen: {e}")

                added_col_count += 1

            print(f"    [OK] {added_col_count} Column(s) hinzugefügt")


        # Parameter hinzufügen via get_or_create
        # Pattern aus _00080_build_DTs.py: jeden Parameter einzeln hinzufügen
        # und direkt danach Validation + Value + Unit per update() setzen.
        # Batch-Add + Batch-Update funktioniert nicht zuverlässig (API-Bug).
        if src_dt.parameter_values:
            added_count = 0
            for pv in src_dt.parameter_values:
                param_name = (
                    getattr(pv, "original_name", None)
                    or getattr(pv, "name", None)
                    or getattr(getattr(pv, "parameter", None), "name", None)
                )
                if not param_name:
                    print(f"    [WARN] ParameterValue ohne Namen übersprungen: id={pv.id}")
                    continue

                try:
                    dst_param = dst.parameters.get_or_create(
                        parameter=Parameter(name=param_name)
                    )
                except Exception as e:
                    print(f"    [WARN] Parameter '{param_name}' get_or_create fehlgeschlagen: {e}")
                    continue

                # Parameter einzeln zum DT hinzufügen — gibt aktualisiertes DT zurück
                try:
                    working_dt = dst.data_templates.add_parameters(
                        data_template_id=created_dt.id,
                        parameters=[ParameterValue(parameter=dst_param)],
                    )
                except Exception as e:
                    print(f"    [WARN] add_parameters für '{param_name}' fehlgeschlagen: {e}")
                    continue

                # Target-ParameterValue auf dem zurückgegebenen DT finden (per Parameter-ID)
                target_pv = next(
                    (x for x in (working_dt.parameter_values or []) if x.id == dst_param.id),
                    None
                )
                if target_pv is None:
                    print(f"    [WARN] target_pv für '{param_name}' nicht gefunden nach add_parameters")
                    added_count += 1
                    continue

                # Pass 1: Validation setzen (bestimmt # vs T vs Dropdown)
                src_validation = getattr(pv, "validation", None) or []
                if src_validation:
                    target_pv.validation = src_validation
                    try:
                        working_dt = dst.data_templates.update(data_template=working_dt)
                        # target_pv neu holen nach update
                        target_pv = next(
                            (x for x in (working_dt.parameter_values or []) if x.id == dst_param.id),
                            target_pv
                        )
                    except Exception as e:
                        print(f"    [WARN] Validation-Update für '{param_name}' fehlgeschlagen: {e}")

                # Pass 2: Value + Unit separat
                src_value = getattr(pv, "value", None)
                src_unit = getattr(pv, "unit", None)
                needs_value_unit = src_value is not None or src_unit is not None

                if needs_value_unit:
                    if src_value is not None:
                        target_pv.value = src_value
                    if src_unit:
                        try:
                            dst_unit = resolve_unit_in_dest(src_unit)
                            if dst_unit:
                                target_pv.unit = dst_unit
                        except Exception as e:
                            print(f"    [WARN] Unit für '{param_name}' fehlgeschlagen: {e}")
                    try:
                        dst.data_templates.update(data_template=working_dt)
                    except Exception as e:
                        print(f"    [WARN] Value/Unit-Update für '{param_name}' fehlgeschlagen: {e}")

                added_count += 1

            print(f"    [OK] {added_count} Parameter hinzugefügt")



# ── Parameter Groups transferieren ─────────────────────────────────────────────

def transfer_parameter_groups(ids: list[str]) -> None:
    print(f"\n{'='*60}")
    print(f"  Parameter Groups ({len(ids)} IDs)")
    print(f"{'='*60}")

    for pg_id in ids:
        # Vollständigen Record vom Source-Tenant laden
        try:
            src_pg: ParameterGroup = src.parameter_groups.get_by_id(id=pg_id)
        except Exception as e:
            print(f"  [ERROR] {pg_id} konnte nicht geladen werden: {e}")
            continue

        print(f"\n  → {pg_id}: '{src_pg.name}'")

        # Prüfen ob im Dest-Tenant bereits vorhanden (Match per Name)
        existing = dst.parameter_groups.get_by_name(name=src_pg.name)
        if existing:
            print(f"    [SKIP] Existiert bereits im Dest-Tenant (id={existing.id})")
            continue

        # DRY RUN: Vorschau ausgeben, nicht schreiben
        if DRY_RUN:
            param_names = [p.name for p in (src_pg.parameters or [])]
            print(
                f"    [DRY RUN] Würde erstellen:\n"
                f"      Name:      {src_pg.name}\n"
                f"      Parameter: {param_names}\n"
                f"      Metadata:  {list((src_pg.metadata or {}).keys())}"
            )
            continue

        # Parameter im Dest-Tenant via get_or_create sicherstellen
        dst_params = []
        for p in (src_pg.parameters or []):
            try:
                dst_param = dst.parameters.get_or_create(parameter=p)
                dst_params.append(dst_param)
            except Exception as e:
                print(f"    [WARN] Parameter '{p.name}' fehlgeschlagen: {e}")

        # Neue PG anlegen
        new_pg = ParameterGroup(
            name=src_pg.name,
            description=getattr(src_pg, "description", None),
            tags=getattr(src_pg, "tags", []) or [],
            metadata=copy_metadata(src_pg.metadata),
            parameters=dst_params,
        )

        try:
            created_pg = dst.parameter_groups.create(parameter_group=new_pg)
            print(f"    [CREATED] id={created_pg.id}")
            if dst_params:
                print(f"    [OK] {len(dst_params)} Parameter hinzugefügt")
        except Exception as e:
            print(f"    [ERROR] Erstellen fehlgeschlagen: {e}")


# ── Main ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mode = "⚠️  DRY RUN — kein Schreiben" if DRY_RUN else "🚀 LIVE — schreibt in Dest-Tenant"
    print(f"\n[{mode}]")
    print(f"Source : {SOURCE_TENANT}")
    print(f"Dest   : {DEST_TENANT}")
    print(f"Credentials: {CREDENTIALS_FILE.resolve()}")

    if not DT_IDS and not PG_IDS:
        print("\n⚠️  Keine IDs konfiguriert. Bitte DT_IDS und/oder PG_IDS befüllen.")
    else:
        if DT_IDS:
            transfer_data_templates(DT_IDS)

        if PG_IDS:
            transfer_parameter_groups(PG_IDS)

    print("\nDone.")
