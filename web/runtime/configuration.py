"""Build the internal application URI without putting credentials in argv."""

import os
from urllib.parse import quote


def mongo_uri_from_env(environ=None):
    values = os.environ if environ is None else environ
    # Explicit URI supports the existing local integration tooling. Compose
    # uses separate env values and never includes a password in a command.
    if values.get("MONGODB_URI"):
        return values["MONGODB_URI"]
    if "MONGODB_URI" in values:
        raise ValueError("MongoDB URI is empty.")
    if "MONGODB_USERNAME" not in values and "MONGODB_PASSWORD" not in values:
        return None
    username = values.get("MONGODB_USERNAME", "")
    password = values.get("MONGODB_PASSWORD", "")
    if not username or not password or any(ord(char) < 32 or ord(char) == 127 for char in username + password):
        raise ValueError("MongoDB application credentials are missing or invalid.")
    return (f"mongodb://{quote(username, safe='')}:{quote(password, safe='')}"
            "@databack-mongodb:27017/databack?authSource=databack&replicaSet=databack")
