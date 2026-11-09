"""Advisory security-scan worker for Jarvis Registry skills.

Claims ``skill_scan_jobs`` with the shared lease library, materializes each skill to a temp dir, runs every
enabled scanner as a bounded, allowlisted-environment subprocess, and records normalized results. See
``.scratchpad/as-1984`` for the design.
"""
