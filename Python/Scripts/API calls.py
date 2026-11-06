import successHelpers.credentials as credentials
from albert import Albert

# %% settings
tenant = "ARDEX EU Sandbox"
DRY_RUN = False

# %% client
base_url, token = credentials.get_base_url_and_bearer_token_for(tenant)
client = Albert.from_token(base_url=base_url, token=token)


# %% helper
def call_api(method: str, endpoint: str, payload: dict | None = None, params: dict | None = None):
    """Send a raw request through the SDK session (base URL and auth are already set)."""
    print(method.upper(), endpoint, payload or "", params or "")
    if DRY_RUN and method.lower() != "get":
        print("DRY RUN, nothing sent")
        return None

    response = client.session.request(method, endpoint, json=payload, params=params)
    print(response.status_code)
    response.raise_for_status()  # fails loudly on 4xx/5xx
    return response.json() if response.content else None


# %% your call: POST /api/v3/labels
payload = {
    "name": "Required",
    "category": "TAR",
}

result = call_api("DELETE", "/api/v3/datatemplates/DAT18")
print(result)

# %% other verbs, same pattern
# call_api("GET", "/api/v3/labels", params={"limit": 10})
# call_api("PATCH", f"/api/v3/labels/{label_id}", payload={...})
# call_api("DELETE", f"/api/v3/labels/{label_id}")
# call_api("POST", f"/api/v3/labels", payload={...})