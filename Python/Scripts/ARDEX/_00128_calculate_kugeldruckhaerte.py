# =============================================================================
# Calculate & Write Kugeldruckhärte
# Reads Eindringtiefe values from confirmed property tasks using DAT828,
# calculates H = F / (pi * (h - 0.03)) using the Prüfkraft from the linked
# workflow, and writes Kugeldruckhärte only for rows where it is not yet set.
# Rows already containing a value are skipped.
#
# Prüfkraft lookup strategy:
#   Compound workflow (has interval_combinations): parse F from each
#     combination's interval_string, e.g. "Prüfkraft: 50 kp,Zeit: 1 day".
#     Map: interval_id (e.g. "ROW3XROW8") -> F
#   Simple workflow (no interval_combinations): read F directly from the
#     Prüfkraft parameter setpoint value.
#     Map: {"default": F}
#
# Multiple inventories/lots per task are fully supported.
# =============================================================================

import math
import pathlib
import tomllib
from collections import defaultdict
from datetime import date

from albert import Albert
from albert.resources.property_data import BulkPropertyData, BulkPropertyDataColumn

# =============================================================================
# KONFIGURATION
# =============================================================================

# Pfad zur TOML-Datei mit Credentials
CREDENTIALS_FILE = pathlib.Path("/Users/christian/credentials.toml")

# Muss exakt dem Sektionsnamen in der TOML entsprechen
TENANT = "Albert Sandbox"

# Sicherheitsmodus: True = nur Vorschau, False = schreibt tatsächlich
DRY_RUN = False

# =============================================================================
# CONSTANTS — adjust Task IDs as needed
# =============================================================================

TASK_IDS = [
    "TASPT2210",  # Kugeldruckhärte mit Automation (no intervals)
    "TASPT2209",  # Kugeldruckhärte 2 Batches + Intervals + 2 Prüfkräfte (mit Automation)
]

PRUEFKRAFT_PRM   = "PRM1167"  # Parameter ID for Prüfkraft in the linked workflow
DATA_TEMPLATE_ID = "DAT828"   # For reference / documentation only

# Column names — must match exactly as stored in the data template
COL_EINDRINGTIEFE    = "Eindringtiefe"
COL_KUGELDRUCKHAERTE = "Kugeldruckhärte"

# =============================================================================
# AUTH
# =============================================================================

with open(CREDENTIALS_FILE, "rb") as f:
    all_credentials = tomllib.load(f)

tenant_config = all_credentials[TENANT]
client = Albert.from_token(
    base_url=tenant_config["url"],
    token=tenant_config["token"],
)

today = date.today().isoformat()

# =============================================================================
# HELPER — Kugeldruckhärte formula
# =============================================================================

def calc_H(F: float, h: float) -> float:
    """H = F / (pi * (h - 0.03)), rounded to 4 decimal places."""
    return round(F / (math.pi * (h - 0.03)), 4)


# =============================================================================
# HELPER — Parse Prüfkraft value from an interval_string
# e.g. "Prüfkraft: 50 kp,Zeit: 1 day" -> 50.0
# =============================================================================

def parse_pruefkraft_from_string(text: str) -> float | None:
    for part in text.split(','):
        part = part.strip()
        if part.startswith('Prüfkraft:'):
            val_str = part.replace('Prüfkraft:', '').strip()
            try:
                return float(val_str.split()[0])
            except (ValueError, IndexError):
                return None
    return None


# =============================================================================
# HELPER — Build Prüfkraft map from a fully hydrated workflow object
#
# Compound workflow (wfl.interval_combinations is populated):
#   Returns {interval_id: F} e.g. {"ROW3XROW8": 50.0, "ROW4XROW8": 20.0, ...}
#   Prüfkraft is parsed from each combination's interval_string.
#
# Simple workflow (no interval_combinations):
#   Finds the Prüfkraft parameter (PRUEFKRAFT_PRM) in parameter_group_setpoints
#   and reads its value directly.
#   Returns {"default": F}
# =============================================================================

def get_pruefkraft_map(wfl) -> dict:
    pruefkraft_map = {}

    if wfl.interval_combinations:
        # Compound case: parse F from each combination's interval_string
        for combo in wfl.interval_combinations:
            F = parse_pruefkraft_from_string(combo.interval_string)
            if F is not None:
                # interval_id is the key that matches interval_combination in property data
                pruefkraft_map[combo.interval_id] = F
    else:
        # Simple case: read Prüfkraft value directly from parameter setpoints
        for pg_sp in (wfl.parameter_group_setpoints or []):
            for p_setpoint in (pg_sp.parameter_setpoints or []):
                if p_setpoint.parameter_id == PRUEFKRAFT_PRM:
                    if p_setpoint.value is not None:
                        pruefkraft_map["default"] = float(p_setpoint.value)

    return pruefkraft_map


# =============================================================================
# MAIN LOOP
# =============================================================================

for task_id in TASK_IDS:
    print(f"\n{'='*60}")
    print(f"Processing {task_id}")
    print(f"{'='*60}")

    task = client.tasks.get_by_id(id=task_id)

    # --- Step 1: Resolve block ---
    block = next((b for b in (task.blocks or [])), None)
    if not block:
        print(f"  No block found on {task_id}, skipping.")
        continue
    block_id = block.id

    # --- Step 2: Get workflow ID from block (FINAL entry) ---
    workflow_refs = block.workflow if isinstance(block.workflow, list) else [block.workflow]
    final_wfl_ref = next((w for w in workflow_refs if getattr(w, 'category', None) == 'FINAL'), None)
    if not final_wfl_ref:
        final_wfl_ref = workflow_refs[0] if workflow_refs else None
    if not final_wfl_ref:
        print(f"  No workflow found on block {block_id}, skipping.")
        continue

    # --- Step 3: Fetch full workflow and build Prüfkraft map ---
    # The block's workflow reference only carries id/name/category.
    # interval_combinations and parameter_group_setpoints require get_by_id().
    wfl = client.workflows.get_by_id(id=final_wfl_ref.id)
    pruefkraft_map = get_pruefkraft_map(wfl)

    if not pruefkraft_map:
        print(f"  WARNING: Could not determine Prüfkraft from workflow {wfl.id}, skipping.")
        continue
    print(f"  Prüfkraft map: {pruefkraft_map}")

    # --- Step 4: Collect all inventory/lot pairs on this task ---
    # Tasks can have multiple inventories (e.g. two lots of the same formula).
    # inventory_id is always the same across entries; lot_id differs per entry.
    inventory_entries = task.inventory_information or []
    if not inventory_entries:
        print(f"  No inventory linked to {task_id}, skipping.")
        continue

    # inventory_id is shared across all entries — take from first
    inventory_id = inventory_entries[0].inventory_id

    # --- Step 5: Read all existing trial data ---
    all_data = client.property_data.get_all_task_properties(
        task_id=task_id, with_data_only=True
    )

    # Collect only rows where Eindringtiefe is filled AND Kugeldruckhärte is empty.
    # Structure: lot_id -> interval_key -> [(visible_trial_no, h_val)]
    rows_to_calculate: dict = defaultdict(lambda: defaultdict(list))
    skipped = 0

    for entry in all_data:
        lot_id = entry.inventory.lot_id
        for interval_data in (entry.data or []):
            if interval_data.void:
                continue
            interval_key = interval_data.interval_combination
            for trial in (interval_data.trials or []):
                if trial.void:
                    continue
                h_val = None
                H_already_filled = False
                for col in (trial.data_columns or []):
                    if col.hidden:
                        continue
                    if col.name == COL_EINDRINGTIEFE:
                        if col.property_data and col.property_data.value:
                            h_val = float(col.property_data.value)
                    if col.name == COL_KUGELDRUCKHAERTE:
                        if col.property_data and col.property_data.value:
                            H_already_filled = True

                if H_already_filled:
                    skipped += 1
                elif h_val is not None:
                    rows_to_calculate[lot_id][interval_key].append(
                        (trial.trial_number, h_val)
                    )

    if skipped:
        print(f"  Skipped {skipped} trial(s) — Kugeldruckhärte already populated.")

    if not rows_to_calculate:
        print(f"  Nothing to calculate — no unfilled rows found.")
        continue

    # --- Step 6: Calculate H for each qualifying trial ---
    # Structure: lot_id -> interval_key -> [(visible_trial_no, h_val, H_val)]
    results: dict = defaultdict(lambda: defaultdict(list))

    for lot_id, intervals in rows_to_calculate.items():
        for interval_key, trials in intervals.items():
            # interval_key matches interval_id from compound workflows,
            # or is None/some default key for simple workflows
            F = pruefkraft_map.get(interval_key) or pruefkraft_map.get("default")
            if F is None:
                print(f"  WARNING: No Prüfkraft for interval '{interval_key}', skipping.")
                continue
            for (vtn, h) in trials:
                H = calc_H(F, h)
                results[lot_id][interval_key].append((vtn, h, H))
                print(f"  Lot={lot_id} | {interval_key} | Trial #{vtn} | h={h} mm | F={F} kp -> H={H}")

    # --- Step 7: Write Kugeldruckhärte for rows that need it ---
    if DRY_RUN:
        total = sum(len(t) for iv in results.values() for t in iv.values())
        print(f"\n  [DRY RUN] Would write {total} Kugeldruckhärte value(s) — no changes made.")
        continue

    for lot_id, intervals in results.items():
        for interval_key, trials in intervals.items():
            kugeldruckhaerte_series = [str(H) for (_, _, H) in trials]

            bulk = BulkPropertyData(columns=[
                BulkPropertyDataColumn(
                    data_column_name=COL_KUGELDRUCKHAERTE,
                    data_series=kugeldruckhaerte_series,
                ),
            ])

            print(f"  Writing {len(trials)} value(s) for interval '{interval_key}', lot {lot_id}...")
            client.property_data.bulk_load_task_properties(
                task_id=task_id,
                block_id=block_id,
                inventory_id=inventory_id,
                property_data=bulk,
                interval=interval_key,
                lot_id=lot_id,
                return_scope="none",
            )

    print(f"  Done: {task_id}")

print("\nAll tasks processed.")
