"""
Created on Fri May 22 14:33:25 2026

@author: christian
"""

from albert.resources.files import FileNamespace
from albert.resources.notebooks import (
    Notebook,
    BlockType,
    AttachesBlock,
    AttachesContent,
)
from albert import Albert
import io
import mimetypes
import pathlib
from pathlib import Path
import requests
import tomllib
import uuid

SOURCE_TENANT = "Akzo sandbox"
SOURCE_PROJECT = "PROAKZ009"

DESTINATION_TENANT = "Albert Sandbox"
DESTINATION_PROJECT = "PROP872"

# ------------------ Authentication ------------------ #
BEARER_FILE = "bearer.toml"
with open(pathlib.Path().home() / BEARER_FILE, "rb") as toml:
    toml_dict = tomllib.load(toml)

source_client = Albert(base_url=toml_dict[SOURCE_TENANT]["url"], token=toml_dict[SOURCE_TENANT]["token"])
if source_client:
    print(f"Connected to {SOURCE_TENANT}")

destination_client = Albert(base_url=toml_dict[DESTINATION_TENANT]["url"], token=toml_dict[DESTINATION_TENANT]["token"])
if destination_client:
    print(f"Connected to {DESTINATION_TENANT}")

# ------------------ Load notebooks from source project ------------------ #
notebook_copy_list = []
for notebook in source_client.notebooks.list_by_parent_id(parent_id=SOURCE_PROJECT):
    notebook_copy_list.append(notebook)
    print(f"Found notebook: {notebook.name} ({notebook.id})")

print(f"\nTotal notebooks to copy: {len(notebook_copy_list)}\n")

# ------------------ Copy notebooks to destination project ------------------ #
START = 0
STOP = None  # None means copy all notebooks

for idx, notebook in enumerate(notebook_copy_list):
    if idx < START:
        continue
    if STOP is not None and idx > STOP:
        break

    print(f"\n[{idx + 1}/{len(notebook_copy_list)}] Processing: {notebook.name}")

    new_notebook = destination_client.notebooks.create(
        notebook=Notebook(
            parent_id=DESTINATION_PROJECT,
            blocks=[],
            name=notebook.name,
        )
    )
    print(f"  Created notebook: {new_notebook.name} ({new_notebook.id})")

    new_notebook_blocks = []

    for block in notebook.blocks:
        if block.type == BlockType.ATTACHES:
            src_content = block.content

            # Download the file from the source tenant
            try:
                signed_url = source_client.files.get_signed_download_url(
                    name=src_content.file_key,
                    namespace=src_content.namespace,
                )
            except Exception as e:
                print(f"  Skipping attachment '{src_content.title}' — could not get download URL: {e}")
                continue

            resp = requests.get(signed_url)
            resp.raise_for_status()
            file_bytes = io.BytesIO(resp.content)
            print(f"  Downloaded attachment: {src_content.title}")

            # Build the new file key for the destination tenant
            filename = src_content.title
            notebook_id = new_notebook.id
            block_id = str(uuid.uuid4())
            name = f"{notebook_id}/{block_id}/{filename}"
            ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
            namespace = FileNamespace.RESULT

            # Upload to destination tenant
            file_bytes.seek(0)
            destination_client.files.sign_and_upload_file(
                data=file_bytes,
                name=name,
                namespace=namespace,
                content_type=ctype,
            )
            print(f"  Uploaded to destination: {name}")

            # Rebuild the attaches block with the new file key
            ext = (Path(filename).suffix or "").lstrip(".").lower() or "bin"
            new_attaches = AttachesBlock(
                id=block_id,
                type=BlockType.ATTACHES,
                content=AttachesContent(
                    title=filename,
                    namespace=namespace.value,
                    file_key=name,
                    format=ext,
                ),
            )
            new_notebook_blocks.append(new_attaches)

        else:
            # All other block types (headers, paragraphs, tables, etc.) copy as-is
            new_notebook_blocks.append(block)

    # Push all blocks to the new notebook
    new_notebook.blocks = new_notebook_blocks
    if len(new_notebook.blocks) >= 1:
        destination_client.notebooks.update_block_content(notebook=new_notebook)
        print(f"  Updated with {len(new_notebook.blocks)} blocks.")
    else:
        print(f"  No blocks to write, skipping update.")

print("\nDone!")