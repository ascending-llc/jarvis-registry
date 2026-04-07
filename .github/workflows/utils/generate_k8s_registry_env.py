import json
import os

import boto3
import yaml

# Constants
SECRET_NAME = os.environ.get("SECRET_NAME")
REGION_NAME = os.environ.get("AWS_REGION", "us-east-1")
OUTPUT_FILE = "kubernetes/registry_env_externalsecret.yaml"
EXTERNAL_SECRET_NAME = "registry-env"
SECRETSTORE_NAME = "secretstore-jarvis"

# Initialize Secrets Manager client
client = boto3.client("secretsmanager", region_name=REGION_NAME)

# Fetch and parse the secret value
response = client.get_secret_value(SecretId=SECRET_NAME)
secret_string = response.get("SecretString")

if not secret_string:
    raise ValueError("No SecretString found in Secrets Manager response.")

try:
    secret_dict = json.loads(secret_string)
except json.JSONDecodeError as e:
    raise ValueError("SecretString is not valid JSON") from e

# Build ExternalSecret `data` entries dynamically
data_entries = [{"secretKey": key, "remoteRef": {"key": SECRET_NAME, "property": key}} for key in secret_dict]

# Create the YAML structure
external_secret = {
    "apiVersion": "external-secrets.io/v1",
    "kind": "ExternalSecret",
    "metadata": {"name": EXTERNAL_SECRET_NAME},
    "spec": {
        "refreshInterval": "1h",
        "secretStoreRef": {"name": SECRETSTORE_NAME, "kind": "SecretStore"},
        "target": {"name": EXTERNAL_SECRET_NAME, "creationPolicy": "Owner"},
        "data": data_entries,
    },
}

# Ensure directory exists
os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)

# Write to YAML file
with open(OUTPUT_FILE, "w") as f:
    yaml.dump(external_secret, f, sort_keys=False)

# Output deployment summary to GitHub Actions log
summary_path = os.environ.get("GITHUB_STEP_SUMMARY", "/dev/stdout")

with open(summary_path, "a") as summary:
    summary.write(f"YAML file generated at: `{OUTPUT_FILE}`\n\n")
    summary.write("The following environment variables have been deployed:\n\n")
    for key in sorted(secret_dict.keys()):
        summary.write(f"- {key}\n")
