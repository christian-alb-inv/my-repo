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
import json
import mimetypes
import pathlib
import tomllib
from typing import Dict, List, Set, Tuple

import requests
from albert import Albert
from albert.resources.files import FileNamespace
from albert.resources.notebooks import Notebook, NotebookBlock
from pydantic import TypeAdapter

# ── Configuration ─────────────────────────────────────────────────────────────

CREDENTIALS_FILE = pathlib.Path("/Users/christian/credentials.toml")
SOURCE_TENANT    = "Albert Sandbox"   # ← must match section name in TOML
DEST_TENANT      = "ARDEX EU Sandbox" # ← must match section name in TOML
DRY_RUN          = False              # True = preview only, False = actually writes

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

# ── Helpers ───────────────────────────────────────────────────────────────────

NB_ADAPTER       = TypeAdapter(NotebookBlock)
ATTACHMENT_TYPES = {"attaches", "image"}


def get_block_type(block) -> str:
    return block.type.value if hasattr(block.type, "value") else block.type


def download_attachment(url: str, fallback_name: str) -> Tuple[io.BytesIO, str, str]:
    """Download an attachment from a signed URL and return its bytes, filename, and content type."""
    with requests.get(url, stream=True) as r:
        r.raise_for_status()
        content_type = (
            r.headers.get("Content-Type")
            or mimetypes.guess_type(fallback_name)[0]
            or "application/octet-stream"
        )
        return io.BytesIO(r.content), fallback_name, content_type


def upload_attachment(
    dest_client: Albert,
    notebook_id: str,
    block_id: str,
    filename: str,
    data: io.BytesIO,
    content_type: str,
) -> dict:
    """Upload a file to the destination tenant and return the content payload for the block."""
    file_key = f"{notebook_id}/{block_id}/{filename}"
    dest_client.files.sign_and_upload_file(
        data=data,
        name=file_key,
        namespace=FileNamespace.RESULT,
        content_type=content_type,
    )
    namespace_value = (
        FileNamespace.RESULT.value
        if hasattr(FileNamespace.RESULT, "value")
        else FileNamespace.RESULT
    )
    return {"title": filename, "fileKey": file_key, "namespace": namespace_value}


def copy_blocks(
    src_notebook,
    dest_notebook_id: str,
) -> Tuple[List[NotebookBlock], Dict[int, dict]]:
    """Copy all blocks from a source notebook; download and re-upload any attachments."""
    blocks: List[NotebookBlock] = []
    attachments: Dict[int, dict] = {}
    seen: Set[str] = set()

    for idx, block in enumerate(src_notebook.blocks):
        if block.id in seen:
            continue
        seen.add(block.id)

        btype = get_block_type(block)

        if btype in ATTACHMENT_TYPES:
            signed_url = (
                getattr(getattr(block, "content", None), "signed_url", None)
                or getattr(getattr(block, "content", None), "signedUrl", None)
            )
            if not signed_url:
                print(f"  ⚠️  Block {block.id}: no signed URL — skipped")
                continue
            try:
                filename = getattr(block.content, "title", None) or "file"
                data, filename, content_type = download_attachment(signed_url, filename)
                new_content = upload_attachment(
                    client_dest, dest_notebook_id, block.id,
                    filename, data, content_type
                )
                attachments[idx] = new_content
                blocks.append(NB_ADAPTER.validate_python({"type": btype, "content": new_content}))
            except Exception as exc:
                print(f"  ⚠️  Block {block.id}: attachment upload failed — {exc}")
        else:
            blocks.append(block)

    return blocks, attachments


def patch_attachment_blocks(
    dest_client: Albert,
    notebook_id: str,
    new_blocks: List[NotebookBlock],
    attachments: Dict[int, dict],
) -> None:
    """Patch attachment block content via REST (SDK does not yet support this directly)."""
    token = dest_client.session.headers.get("Authorization", "")
    base_url = str(dest_client.session.base_url).rstrip("/")
    headers = {
        "Authorization": token if token.startswith("Bearer ") else f"Bearer {token}",
        "Content-Type": "application/json",
    }
    for block_idx, content in attachments.items():
        block_id = new_blocks[block_idx].id
        url = f"{base_url}/api/v3/notebooks/{notebook_id}/blocks/{block_id}"
        body = {"data": [{"operation": "update", "attribute": "content", "newValue": content}]}
        r = requests.patch(url, headers=headers, data=json.dumps(body))
        if r.status_code in (200, 204):
            print(f"  ✅  Block {block_idx} attachment patched")
        else:
            print(f"  ❌  Block {block_idx} patch failed: {r.status_code} — {r.text}")


def migrate_notebook(src_notebook, dest_parent_id: str) -> None:
    """Migrate a single notebook to the destination tenant."""
    print(f"\n📋 Migrating notebook: '{src_notebook.name}' ({src_notebook.id})")

    if DRY_RUN:
        print(f"  [DRY RUN] Would create notebook with {len(src_notebook.blocks)} blocks — skipping write.")
        return

    # Create notebook in destination
    dest_nb = client_dest.notebooks.create(
        notebook=Notebook(name=src_notebook.name, parent_id=dest_parent_id)
    )
    print(f"  ✅  Created: {dest_nb.id}")

    # Copy blocks
    blocks, attachments = copy_blocks(src_notebook, dest_nb.id)

    # Write blocks
    dest_nb.blocks = blocks
    dest_nb.links  = src_notebook.links
    dest_nb = client_dest.notebooks.update_block_content(notebook=dest_nb)

    # Patch attachments
    if attachments:
        patch_attachment_blocks(client_dest, dest_nb.id, dest_nb.blocks, attachments)

    print(f"  ✅  Done ({len(blocks)} blocks, {len(attachments)} attachments)")


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
