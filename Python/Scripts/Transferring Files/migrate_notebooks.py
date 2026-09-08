"""
Standalone script: Migrate notebooks from one Albert tenant to another.

Requirements:
    pip install albert-python requests
    Python 3.11+ (for built-in tomllib; on 3.10 use: pip install tomli)

Usage:
    Update the configuration section below:
        CREDENTIALS_FILE  = path to your TOML credentials file
        SOURCE_TENANT     = section name in the TOML for the origin tenant
        DEST_TENANT       = section name in the TOML for the destination tenant
        NOTEBOOK_IDS      = list of specific notebook IDs to migrate (e.g. ["NTB123", "NTB456"])
        DEST_PARENT_ID    = ID of the project/task to write notebooks into
        DRY_RUN           = True for preview only, False to actually write

Note:
    User and inventory mentions (e.g. USR123, INV456) are not remapped between tenants.
    If a block contains such mentions, the original ID will remain — Albert will show
    it as an unresolved link in the destination tenant.

Based on:
    notebooks_helpers.py from the Kenvue tenant migration framework (tech-ops branch).
"""

import io
import mimetypes
import pathlib
import tomllib
import uuid
from pathlib import Path

import requests
from albert import Albert
from albert.resources.files import FileNamespace
from albert.resources.notebooks import (
    AttachesBlock,
    AttachesContent,
    BlockType,
    Notebook,
)

# ── Configuration ─────────────────────────────────────────────────────────────

CREDENTIALS_FILE = pathlib.Path("/Users/christian/credentials.toml")
SOURCE_TENANT    = "Albert Sandbox"    # ← must match section name in TOML
DEST_TENANT      = "ARDEX EU Sandbox"  # ← must match section name in TOML
DRY_RUN          = False               # True = preview only, False = actually writes

NOTEBOOK_IDS   = ["NTB123", "NTB456"]  # ← update this
DEST_PARENT_ID = "PRJ456"              # ← update this

# ── Credentials & Clients ─────────────────────────────────────────────────────

def load_credentials(toml_path: pathlib.Path) -> dict:
    """Load the TOML credentials file and return its contents as a dict."""
    resolved = toml_path.resolve()
    if not resolved.exists():
        raise FileNotFoundError(
            f"Credentials file not found: {resolved}\n"
            f"Please update CREDENTIALS_FILE in the script."
        )
    with open(resolved, "rb") as f:
        return tomllib.load(f)


def make_client(tenant_name: str, creds: dict) -> Albert:
    """Create an Albert client for the given tenant name."""
    if tenant_name not in creds:
        available = list(creds.keys())
        raise KeyError(
            f"Tenant '{tenant_name}' not found in credentials file.\n"
            f"Available sections: {available}"
        )
    entry = creds[tenant_name]
    return Albert.from_token(
        base_url=entry["url"],
        token=entry["token"],
    )


creds         = load_credentials(CREDENTIALS_FILE)
client_origin = make_client(SOURCE_TENANT, creds)
client_dest   = make_client(DEST_TENANT, creds)

# ── Migration ─────────────────────────────────────────────────────────────────

def migrate_notebook(src_notebook, dest_parent_id: str) -> None:
    """Migrate a single notebook to the destination tenant."""
    print(f"\n📋 Migrating notebook: '{src_notebook.name}' ({src_notebook.id})")

    if DRY_RUN:
        print(f"  [DRY RUN] Would create notebook with {len(src_notebook.blocks)} blocks — skipping write.")
        return

    # Create notebook in destination
    dest_nb = client_dest.notebooks.create(
        notebook=Notebook(name=src_notebook.name, parent_id=dest_parent_id, blocks=[])
    )
    print(f"  ✅  Created: {dest_nb.id}")

    new_blocks = []

    for block in src_notebook.blocks:
        if block.type == BlockType.ATTACHES:
            src_content = block.content

            # Download file from source tenant
            try:
                signed_url = client_origin.files.get_signed_download_url(
                    name=src_content.file_key,
                    namespace=src_content.namespace,
                )
            except Exception as exc:
                print(f"  ⚠️  Skipping attachment '{src_content.title}' — could not get download URL: {exc}")
                continue

            try:
                resp = requests.get(signed_url)
                resp.raise_for_status()
                file_bytes = io.BytesIO(resp.content)
                print(f"  ⬇️  Downloaded: {src_content.title}")
            except Exception as exc:
                print(f"  ⚠️  Skipping attachment '{src_content.title}' — download failed: {exc}")
                continue

            # Upload to destination tenant
            filename  = src_content.title
            block_id  = str(uuid.uuid4())
            file_key  = f"{dest_nb.id}/{block_id}/{filename}"
            ctype     = mimetypes.guess_type(filename)[0] or "application/octet-stream"
            namespace = FileNamespace.RESULT

            file_bytes.seek(0)
            client_dest.files.sign_and_upload_file(
                data=file_bytes,
                name=file_key,
                namespace=namespace,
                content_type=ctype,
            )
            print(f"  ⬆️  Uploaded: {file_key}")

            # Rebuild AttachesBlock with new file key
            ext = (Path(filename).suffix or "").lstrip(".").lower() or "bin"
            new_blocks.append(AttachesBlock(
                id=block_id,
                type=BlockType.ATTACHES,
                content=AttachesContent(
                    title=filename,
                    namespace=namespace.value,
                    file_key=file_key,
                    format=ext,
                ),
            ))

        else:
            # All other block types (text, tables, headers, etc.) copy as-is
            new_blocks.append(block)

    # Write blocks to destination notebook
    dest_nb.blocks = new_blocks
    if new_blocks:
        client_dest.notebooks.update_block_content(notebook=dest_nb)
        print(f"  ✅  Done ({len(new_blocks)} blocks)")
    else:
        print(f"  ⚠️  No blocks to write — skipping update.")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    print(f"Migrating {len(NOTEBOOK_IDS)} notebook(s) to {DEST_PARENT_ID}")

    for nb_id in NOTEBOOK_IDS:
        try:
            nb = client_origin.notebooks.get_by_id(id=nb_id)
            migrate_notebook(nb, DEST_PARENT_ID)
        except Exception as exc:
            print(f"  ❌  Error migrating '{nb_id}': {exc}")

    print("\n✅  Migration complete.")


if __name__ == "__main__":
    main()
