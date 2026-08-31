# =============================================================================
# Calculate & Write Kugeldruckhärte
# Reads Eindringtiefe values from confirmed property tasks using DAT828,
# calculates H = F / (pi * (h - 0.03)) using the Prüfkraft from the linked
# workflow, and writes Kugeldruckhärte only into the specific trial row where
# Eindringtiefe was read. Rows already containing a Kugeldruckhärte value
# are skipped. Eindringtiefe is never touched.
#
# Write strategy: update_or_create_task_properties with explicit trial_number
# so only the Kugeldruckhärte column is patched — no delete/rewrite needed.
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
from albert.resources.property_data import TaskPropertyCreate, TaskDataColumn

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
#
# Compound workflow (wfl.interval_combinations populated):
#   Returns {interval_id: F}  e.g. {"ROW3XROW8": 50.0, ...}
#
# Simple workflow (no interval_combinations):
#   Returns {"default": F}
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

    # --- Step 4: Fetch data template to get Kugeldruckhärte column ID + sequence ---
    # These are needed for TaskPropertyCreate to target the correct column.
    dt = client.data_templates.get_by_id(id=DATA_TEMPLATE_ID)
    kugeldruckhaerte_dc_id = None
    kugeldruckhaerte_col_seq = None
    for dcv in (dt.data_column_values or []):
        dc = client.data_columns.get_by_id(id=dcv.data_column_id)
        if dc.name == COL_KUGELDRUCKHAERTE:
            kugeldruckhaerte_dc_id   = dcv.data_column_id
            kugeldruckhaerte_col_seq = dcv.sequence  # SDK uses 'sequence', not 'column_sequence'
            break

    if not kugeldruckhaerte_dc_id:
        print(f"  ERROR: Could not find '{COL_KUGELDRUCKHAERTE}' column in {DATA_TEMPLATE_ID}, skipping.")
        continue

    # --- Step 5: Resolve inventory_id (shared across all lots) ---
    inventory_entries = task.inventory_information or []
    if not inventory_entries:
        print(f"  No inventory linked to {task_id}, skipping.")
        continue
    inventory_id = inventory_entries[0].inventory_id

    # --- Step 6: Read all existing trial data ---
    all_data = client.property_data.get_all_task_properties(
        task_id=task_id, with_data_only=True
    )

    # Collect rows where Eindringtiefe is filled AND Kugeldruckhärte is empty.
    # Structure: lot_id -> interval_key -> {trial_number: h_val}
    # Using a dict keyed by trial_number deduplicates rows the API returns
    # multiple times (once per inventory entry on the task).
    rows_to_calculate: dict = defaultdict(lambda: defaultdict(dict))
    seen_skipped: set = set()  # track (lot_id, interval_key, trial_no) to avoid double-counting

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

                dedup_key = (lot_id, interval_key, trial.trial_number)
                if H_already_filled:
                    seen_skipped.add(dedup_key)
                elif h_val is not None:
                    # Dict assignment deduplicates: same row seen multiple times is stored once
                    rows_to_calculate[lot_id][interval_key][trial.trial_number] = h_val

    skipped = len(seen_skipped)
    if skipped:
        print(f"  Skipped {skipped} trial(s) — Kugeldruckhärte already populated.")

    if not rows_to_calculate:
        print(f"  Nothing to calculate — no unfilled rows found.")
        continue

    # --- Step 7: Calculate H and build write payload ---
    # One TaskPropertyCreate per trial row — targets Kugeldruckhärte column only.
    # trial_number ensures we patch the exact row where Eindringtiefe lives.
    for lot_id, intervals in rows_to_calculate.items():
        payload = []

        for interval_key, trial_map in intervals.items():
            F = pruefkraft_map.get(interval_key) or pruefkraft_map.get("default")
            if F is None:
                print(f"  WARNING: No Prüfkraft for interval '{interval_key}', skipping.")
                continue
            for trial_no, h in trial_map.items():
                H = calc_H(F, h)
                print(f"  Lot={lot_id} | {interval_key} | Trial #{trial_no} | h={h} mm | F={F} kp -> H={H}")

                payload.append(TaskPropertyCreate(
                    interval_combination=interval_key,
                    data_column=TaskDataColumn(
                        data_column_id=kugeldruckhaerte_dc_id,
                        column_sequence=kugeldruckhaerte_col_seq,
                    ),
                    value=str(H),
                    data_template=dt,
                    trial_number=trial_no,  # targets the exact row — Eindringtiefe untouched
                ))

        if not payload:
            continue

        if DRY_RUN:
            print(f"\n  [DRY RUN] Would write {len(payload)} Kugeldruckhärte value(s) for lot {lot_id} — no changes made.")
            continue

        print(f"  Writing {len(payload)} value(s) for lot {lot_id}...")
        client.property_data.update_or_create_task_properties(
            task_id=task_id,
            block_id=block_id,
            inventory_id=inventory_id,
            lot_id=lot_id,
            properties=payload,
            return_scope="none",
        )

    print(f"  Done: {task_id}")

print("\nAll tasks processed.")
