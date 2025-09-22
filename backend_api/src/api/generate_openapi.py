import json
import os

from src.api.main import app

"""
Run this module to regenerate the OpenAPI JSON file after any API change.
Example:
    python -m src.api.generate_openapi
"""

# Get the OpenAPI schema
openapi_schema = app.openapi()

# Write to file
output_dir = "interfaces"
os.makedirs(output_dir, exist_ok=True)
output_path = os.path.join(output_dir, "openapi.json")

with open(output_path, "w") as f:
    json.dump(openapi_schema, f, indent=2)
print(f"OpenAPI schema written to {output_path}")
