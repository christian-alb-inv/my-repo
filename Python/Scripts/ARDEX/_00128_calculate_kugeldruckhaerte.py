# =============================================================================
# Calculate & Write Kugeldruckhärte
# Reads Eindringtiefe values from confirmed property tasks using DAT828,
# calculates H = F / (pi * (h - 0.03)) using the Prüfkraft from the linked
# workflow, then writes both columns back via bulk_load_task_properties.
#
# SAFETY: bulk_load deletes and rewrites the entire interval combination.
# To prevent data loss, ALL trials of an interval are always read and
# rewritten together — including trials where Kugeldruckhärte is already set.
# Skip logic operates at interval level:
#   - Skip interval entirely if ALL trials already have Kugeldruckhärte
#   - Process interval if ANY trial is missing Kugeldruckhärte
#
# Prüfkraft lookup:
#   Compound workflow: parsed from interval_combinations.interval_string
#   Simple workflow:   read from parameter_group_setpoints value
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
DRY_RUN = True

# =============================================================================
# CONSTANTS — adjust Task IDs as needed
# =============================================================================

TASK_IDS = [
    "TASPT2210",  # Kugeldruckhärte mit Automation (no intervals)
    "TASPT2209",  # Kugeldruckhärte 2 Batches + Intervals + 2 Prüfkräfte (mit Automation)
]

PRUEFKRAFT_PRM   = "PRM1167"  # Parameter ID for Prüfkraft in the linked workflow
DATA_TEMPLATE_ID = "DAT828"   # Kugeldruckhärte data template

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
# =============================================================================

def get_pruefkraft_map(wfl) -> dict:
    pruefkraft_map = {}

    if wfl.interval_combinations:
        # Compound: parse F from each combination's interval_string
        for combo in wfl.interval_combinations:
            F = parse_pruefkraft_from_string(combo.interval_string)
            if F is not None:
                pruefkraft_map[combo.interval_id] = F
    else:
        # Simple: read F directly from the Prüfkraft parameter setpoint
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
    wfl = client.workflows.get_by_id(id=final_wfl_ref.id)
    pruefkraft_map = get_pruefkraft_map(wfl)
    if not pruefkraft_map:
        print(f"  WARNING: Could not determine Prüfkraft from workflow {wfl.id}, skipping.")
        continue
    print(f"  Prüfkraft map: {pruefkraft_map}")

    # --- Step 4: Resolve inventory_id (shared across all lots) ---
    inventory_entries = task.inventory_information or []
    if not inventory_entries:
        print(f"  No inventory linked to {task_id}, skipping.")
        continue
    inventory_id = inventory_entries[0].inventory_id

    # --- Step 5: Read ALL trial data per interval ---
    # We must read every trial in every interval — not just unfilled ones —
    # because bulk_load rewrites the entire interval. Dropping any trial
    # would permanently delete its data.
    #
    # Structure: lot_id -> interval_key -> {trial_number: (h_val, H_val)}
    #   h_val: Eindringtiefe (always present if row has data)
    #   H_val: existing Kugeldruckhärte value, or None if not yet calculated
    #
    # We deduplicate by (lot_id, interval_key, trial_number) since the API
    # returns the same row multiple times for tasks with multiple inventories.

    all_data = client.property_data.get_all_task_properties(
        task_id=task_id, with_data_only=True
    )

    # lot_id -> interval_key -> {trial_no: (h_val, H_val)}
    interval_data_map: dict = defaultdict(lambda: defaultdict(dict))

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
                H_val = None
                for col in (trial.data_columns or []):
                    if col.hidden:
                        continue
                    if col.name == COL_EINDRINGTIEFE:
                        if col.property_data and col.property_data.value:
                            h_val = float(col.property_data.value)
                    if col.name == COL_KUGELDRUCKHAERTE:
                        if col.property_data and col.property_data.value:
                            H_val = float(col.property_data.value)

                # Only store rows that have at least Eindringtiefe
                if h_val is not None:
                    dedup_key = trial.trial_number
                    # Dict assignment deduplicates repeated rows
                    interval_data_map[lot_id][interval_key][dedup_key] = (h_val, H_val)

    # --- Step 6: Determine which intervals need processing ---
    # An interval needs processing if ANY trial is missing Kugeldruckhärte.
    # If ALL trials already have Kugeldruckhärte, skip the entire interval.

    intervals_to_process: dict = defaultdict(dict)  # lot_id -> interval_key -> trial_map

    for lot_id, intervals in interval_data_map.items():
        for interval_key, trial_map in intervals.items():
            all_filled = all(H_val is not None for (_, H_val) in trial_map.values())
            if all_filled:
                print(f"  Skipping interval '{interval_key}', lot {lot_id} — all {len(trial_map)} trial(s) already complete.")
            else:
                missing = sum(1 for (_, H_val) in trial_map.values() if H_val is None)
                print(f"  Interval '{interval_key}', lot {lot_id}: {missing} of {len(trial_map)} trial(s) need calculation.")
                intervals_to_process[lot_id][interval_key] = trial_map

    if not intervals_to_process:
        print(f"  Nothing to do — all intervals complete.")
        continue

    # --- Step 7: Calculate and write ---
    # For each interval to process, calculate H for trials missing it,
    # keep existing H for trials that already have it, then bulk_load all
    # trials together to avoid deleting any existing data.
    for lot_id, intervals in intervals_to_process.items():
        for interval_key, trial_map in intervals.items():
            F = pruefkraft_map.get(interval_key) or pruefkraft_map.get("default")
            if F is None:
                print(f"  WARNING: No Prüfkraft for interval '{interval_key}', skipping.")
                continue

            eindringtiefe_series = []
            kugeldruckhaerte_series = []

            for trial_no, (h, H_existing) in sorted(trial_map.items()):
                H = H_existing if H_existing is not None else calc_H(F, h)
                status = "existing" if H_existing is not None else "calculated"
                eindringtiefe_series.append(str(h))
                kugeldruckhaerte_series.append(str(H))
                print(f"  Lot={lot_id} | {interval_key} | Trial #{trial_no} | h={h} | H={H} ({status})")

            if DRY_RUN:
                print(f"  [DRY RUN] Would write {len(eindringtiefe_series)} row(s) for interval '{interval_key}', lot {lot_id} — no changes made.")
                continue

            bulk = BulkPropertyData(columns=[
                BulkPropertyDataColumn(
                    data_column_name=COL_EINDRINGTIEFE,
                    data_series=eindringtiefe_series,
                ),
                BulkPropertyDataColumn(
                    data_column_name=COL_KUGELDRUCKHAERTE,
                    data_series=kugeldruckhaerte_series,
                ),
            ])

            print(f"  Writing {len(eindringtiefe_series)} row(s) for interval '{interval_key}', lot {lot_id}...")
            client.property_data.bulk_load_task_properties(
                task_id=task_id,
                block_id=block_id,
                inventory_id=inventory_id,
                property_data=bulk,
                interval=interval_key,
                lot_id=lot_id,
                return_scope="none",
            )
            print(f"  ✓ Done: interval '{interval_key}', lot {lot_id}")

    print(f"  Done: {task_id}")

print("\nAll tasks processed.")
