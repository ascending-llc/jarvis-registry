"""
End-to-end test for Skill Sync Source CRUD + sync + OAuth APIs.

Env vars:
    COOKIE                    Session cookie (required)
    CSRF                      CSRF token (auto-extracted from COOKIE if missing)
    GITHUB_CLIENT_ID          GitHub App OAuth client ID (required)
    GITHUB_CLIENT_SECRET      GitHub App client secret (required)
    BASE_URL                  Registry base URL (default: http://localhost)
    GITHUB_OWNER              GitHub owner (default: ascending-llc)
    GITHUB_REPO               GitHub repo (default: jarvis-skill)

Usage:
    uv run python scripts/test_skill_sync_source_e2e.py
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time

import dotenv
import httpx

dotenv.load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("skill_sync_e2e")

PASS = 0
FAIL = 0


def _result(name: str, passed: bool, detail: str = "") -> None:
    global PASS, FAIL
    if passed:
        PASS += 1
        logger.info("[PASS] %s %s", name, detail)
    else:
        FAIL += 1
        logger.error("[FAIL] %s %s", name, detail)


def _pp(data: dict | list) -> str:
    return json.dumps(data, indent=2, default=str)


class SkillSyncE2E:
    def __init__(self) -> None:
        base_url = os.environ.get("BASE_URL", "http://localhost")
        cookie = os.environ.get("COOKIE", "")
        csrf = os.environ.get("CSRF", "")

        if not cookie:
            logger.error("COOKIE env var required")
            sys.exit(1)

        if not csrf:
            for part in cookie.split(";"):
                part = part.strip()
                if part.startswith("jarvis_registry_csrf="):
                    csrf = part.split("=", 1)[1]
                    logger.info("Auto-extracted CSRF from cookie string")
                    break
        if not csrf:
            logger.error("CSRF env var required (or include jarvis_registry_csrf in COOKIE)")
            sys.exit(1)

        self.github_client_id = os.environ.get("GITHUB_CLIENT_ID", "")
        self.github_client_secret = os.environ.get("GITHUB_CLIENT_SECRET", "")
        if not self.github_client_id or not self.github_client_secret:
            logger.error("GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET env vars required")
            sys.exit(1)

        self.github_owner = os.environ.get("GITHUB_OWNER", "ascending-llc")
        self.github_repo = os.environ.get("GITHUB_REPO", "jarvis-skill")

        self.api = f"{base_url.rstrip('/')}/api/v1/skill-sync-sources"
        self.client = httpx.Client(
            headers={
                "Content-Type": "application/json",
                "Cookie": cookie,
                "X-Jarvis-CSRF": csrf,
            },
            follow_redirects=False,
            timeout=30,
        )
        self.created_ids: list[str] = []

    def run_all(self) -> None:
        logger.info("=" * 60)
        logger.info("Skill Sync Source E2E Tests")
        logger.info("=" * 60)

        self.test_create_source()
        self.test_create_validation_errors()
        self.test_get_source()
        self.test_get_source_not_found()
        self.test_list_sources()
        self.test_list_sources_pagination()
        self.test_list_sources_filters()
        self.test_update_source()
        self.test_update_validation_errors()
        self.test_sync_needs_auth()
        self.test_oauth_initiate_redirects()
        self.test_oauth_callback_missing_params()
        self.test_oauth_callback_error_param()
        self.test_get_job_not_found()
        self.test_delete_source()

        self.cleanup()

        logger.info("=" * 60)
        logger.info("Results: %d passed, %d failed", PASS, FAIL)
        logger.info("=" * 60)

    def _create_source(self, display_name: str = "E2E Test Source", **overrides) -> dict | None:
        payload = {
            "displayName": display_name,
            "owner": self.github_owner,
            "repo": self.github_repo,
            "ref": "main",
            "paths": ["jarvis-registry-backend-practices"],
            "githubAppClientId": self.github_client_id,
            "githubAppClientSecret": self.github_client_secret,
            **overrides,
        }
        r = self.client.post(self.api, json=payload)
        if r.status_code == 201:
            body = r.json()
            self.created_ids.append(body["id"])
            return body
        return None

    def _require_source(self) -> str | None:
        if self.created_ids:
            return self.created_ids[0]
        return None

    def test_create_source(self) -> None:
        body = self._create_source(
            display_name="E2E Test Source",
            description="Automated e2e test",
            tags=["e2e", "test"],
            skillDiscoveryDepth=2,
        )
        _result("CREATE source → 201", body is not None)
        if body is None:
            return

        _result("CREATE id present", bool(body["id"]))
        _result("CREATE displayName", body["displayName"] == "E2E Test Source")
        _result("CREATE owner/repo", body["owner"] == self.github_owner and body["repo"] == self.github_repo)
        _result("CREATE status=active", body["status"] == "active")
        _result("CREATE syncStatus=idle", body["syncStatus"] == "idle")
        _result("CREATE hasClientSecret=true", body["hasClientSecret"] is True)
        _result("CREATE githubAppClientId", body["githubAppClientId"] == self.github_client_id)
        _result(
            "CREATE permissions=owner",
            body.get("permissions", {}).get("VIEW") is True and body.get("permissions", {}).get("DELETE") is True,
        )
        _result("CREATE createdBy set", body.get("createdBy") is not None)
        _result("CREATE recentJobs empty", body.get("recentJobs") == [])
        logger.info("  Created source: %s", body["id"])

    def test_create_validation_errors(self) -> None:
        base = {
            "displayName": "Validation Test",
            "owner": self.github_owner,
            "repo": self.github_repo,
            "ref": "main",
            "paths": ["skills"],
            "githubAppClientId": "test-client",
            "githubAppClientSecret": "test-secret",
        }
        cases = [
            ("empty displayName", {**base, "displayName": ""}),
            ("empty paths", {**base, "paths": []}),
            ("path traversal", {**base, "paths": ["../etc/passwd"]}),
            ("absolute path", {**base, "paths": ["/absolute"]}),
            ("duplicate paths", {**base, "paths": ["skills", "skills"]}),
            ("bad ref", {**base, "ref": "refs/../hack"}),
            ("bad owner", {**base, "owner": "-invalid"}),
            ("missing required", {"displayName": "missing fields"}),
            ("depth > 10", {**base, "skillDiscoveryDepth": 99}),
        ]
        for label, payload in cases:
            r = self.client.post(self.api, json=payload)
            _result(f"VALIDATE {label} → 422", r.status_code == 422, f"status={r.status_code}")

    def test_get_source(self) -> None:
        sid = self._require_source()
        if not sid:
            _result("GET source", False, "no source created")
            return
        r = self.client.get(f"{self.api}/{sid}")
        _result("GET source → 200", r.status_code == 200, f"status={r.status_code}")
        if r.status_code != 200:
            return
        body = r.json()
        _result("GET id match", body["id"] == sid)
        _result("GET has detail fields", "githubAppClientId" in body and "recentJobs" in body)

    def test_get_source_not_found(self) -> None:
        r = self.client.get(f"{self.api}/000000000000000000000000")
        _result("GET nonexistent → 404", r.status_code == 404, f"status={r.status_code}")

        r = self.client.get(f"{self.api}/not-an-objectid")
        _result("GET invalid id → 404 or 422", r.status_code in (404, 422), f"status={r.status_code}")

    def test_list_sources(self) -> None:
        r = self.client.get(self.api)
        _result("LIST → 200", r.status_code == 200, f"status={r.status_code}")
        if r.status_code != 200:
            return
        body = r.json()
        _result("LIST has sources array", isinstance(body.get("sources"), list))
        _result("LIST has pagination", "pagination" in body)
        _result("LIST total >= 1", body["pagination"]["total"] >= 1)
        if body["sources"]:
            item = body["sources"][0]
            _result("LIST item has owner", "owner" in item)
            _result(
                "LIST no secret exposed",
                "githubAppClientSecret" not in item and "githubAppClientSecretEncrypted" not in item,
            )

    def test_list_sources_pagination(self) -> None:
        r = self.client.get(self.api, params={"page": 1, "per_page": 1})
        _result("LIST per_page=1 → 200", r.status_code == 200, f"status={r.status_code}")
        if r.status_code == 200:
            body = r.json()
            _result("LIST returns at most 1", len(body["sources"]) <= 1)
            _result("LIST pagination.perPage=1", body["pagination"]["perPage"] == 1)

        r = self.client.get(self.api, params={"page": 9999})
        _result(
            "LIST high page → empty",
            r.status_code == 200 and len(r.json()["sources"]) == 0,
            f"status={r.status_code}",
        )

    def test_list_sources_filters(self) -> None:
        r = self.client.get(self.api, params={"tag": "e2e"})
        _result("LIST tag=e2e → 200", r.status_code == 200, f"status={r.status_code}")
        if r.status_code == 200:
            _result("LIST tag filter >=1", r.json()["pagination"]["total"] >= 1)

        r = self.client.get(self.api, params={"query": "E2E Test"})
        _result("LIST query filter → 200", r.status_code == 200, f"status={r.status_code}")

        r = self.client.get(self.api, params={"syncStatus": "idle"})
        _result("LIST syncStatus=idle → 200", r.status_code == 200, f"status={r.status_code}")

    # ── UPDATE ────────────────────────────────────────────────

    def test_update_source(self) -> None:
        sid = self._require_source()
        if not sid:
            _result("UPDATE source", False, "no source created")
            return

        r = self.client.put(
            f"{self.api}/{sid}",
            json={"displayName": "E2E Updated Name", "description": "Updated", "tags": ["e2e", "updated"]},
        )
        _result("UPDATE → 200", r.status_code == 200, f"status={r.status_code}")
        if r.status_code != 200:
            return
        body = r.json()
        _result("UPDATE displayName", body["displayName"] == "E2E Updated Name")
        _result("UPDATE tags", body["tags"] == ["e2e", "updated"])
        _result("UPDATE owner unchanged", body["owner"] == self.github_owner)

        # partial update — only ref
        r = self.client.put(f"{self.api}/{sid}", json={"ref": "develop"})
        _result("UPDATE partial ref", r.status_code == 200 and r.json()["ref"] == "develop", f"status={r.status_code}")

        # restore
        self.client.put(f"{self.api}/{sid}", json={"ref": "main"})

        # syncAfterUpdate=true → triggers sync (returns 200 with needsAuthorization or source detail)
        r = self.client.put(f"{self.api}/{sid}", json={"displayName": "Sync After", "syncAfterUpdate": True})
        _result(
            "UPDATE syncAfterUpdate triggers sync",
            r.status_code == 200,
            f"status={r.status_code}",
        )
        if r.status_code == 200:
            body = r.json()
            has_needs_auth = body.get("needsAuthorization") is True
            has_source_detail = "displayName" in body
            _result(
                "UPDATE syncAfterUpdate response shape",
                has_needs_auth or has_source_detail,
                f"needsAuth={has_needs_auth}, hasDetail={has_source_detail}",
            )

    def test_update_validation_errors(self) -> None:
        sid = self._require_source()
        if not sid:
            _result("UPDATE validation", False, "no source created")
            return

        cases = [
            ("empty displayName", {"displayName": ""}),
            ("path traversal", {"paths": ["../escape"]}),
            ("bad ref", {"ref": "refs/../hack"}),
            ("negative depth", {"skillDiscoveryDepth": -1}),
        ]
        for label, payload in cases:
            r = self.client.put(f"{self.api}/{sid}", json=payload)
            _result(f"UPDATE {label} → 422", r.status_code == 422, f"status={r.status_code}")

        r = self.client.put(f"{self.api}/000000000000000000000000", json={"displayName": "ghost"})
        _result("UPDATE nonexistent → 404", r.status_code == 404, f"status={r.status_code}")

    def test_sync_needs_auth(self) -> None:
        sid = self._require_source()
        if not sid:
            _result("SYNC needs_auth", False, "no source created")
            return
        r = self.client.post(f"{self.api}/{sid}/sync")
        _result("SYNC → 200", r.status_code == 200, f"status={r.status_code}")
        if r.status_code != 200:
            return
        body = r.json()
        has_needs_auth = body.get("needsAuthorization") is True
        has_job = body.get("job") is not None
        _result(
            "SYNC returns needsAuth or job",
            has_needs_auth or has_job,
            f"needsAuth={has_needs_auth}, hasJob={has_job}",
        )
        if has_job:
            job = body["job"]
            _result("SYNC job has id", bool(job.get("id")))
            _result("SYNC job sourceId matches", job.get("sourceId") == sid)
            _result("SYNC job status", job.get("status") in ("pending", "running"))
            self._poll_job(sid, job["id"], label="SYNC")

    def _poll_job(self, source_id: str, job_id: str, label: str = "JOB", max_wait: int = 60) -> dict | None:
        start = time.time()
        while time.time() - start < max_wait:
            r = self.client.get(f"{self.api}/{source_id}/jobs/{job_id}")
            if r.status_code != 200:
                _result(f"{label} poll job → 200", False, f"status={r.status_code}")
                return None
            job = r.json()
            status = job.get("status")
            if status in ("completed", "failed"):
                _result(f"{label} job finished", True, f"status={status}, phase={job.get('phase')}")
                return job
            time.sleep(2)
        _result(f"{label} job finished within {max_wait}s", False, "timeout")
        return None

    def test_oauth_initiate_redirects(self) -> None:
        sid = self._require_source()
        if not sid:
            _result("OAUTH initiate", False, "no source created")
            return
        r = self.client.get(f"{self.api}/{sid}/oauth/initiate")
        _result("OAUTH initiate → 307", r.status_code == 307, f"status={r.status_code}")
        if r.status_code == 307:
            location = r.headers.get("location", "")
            _result(
                "OAUTH initiate → GitHub authorize URL",
                "github.com" in location and "oauth" in location.lower(),
                f"location={location[:100]}",
            )

    def test_oauth_callback_missing_params(self) -> None:
        sid = self._require_source()
        if not sid:
            _result("OAUTH callback", False, "no source created")
            return
        r = self.client.get(f"{self.api}/{sid}/oauth/callback")
        _result("OAUTH callback no params → 307", r.status_code == 307, f"status={r.status_code}")
        if r.status_code == 307:
            _result("OAUTH callback → error redirect", "error=auth_failed" in r.headers.get("location", ""))

    def test_oauth_callback_error_param(self) -> None:
        sid = self._require_source()
        if not sid:
            _result("OAUTH callback error", False, "no source created")
            return
        r = self.client.get(f"{self.api}/{sid}/oauth/callback", params={"error": "access_denied"})
        _result(
            "OAUTH callback error param → redirect",
            r.status_code == 307 and "error=auth_failed" in r.headers.get("location", ""),
            f"status={r.status_code}",
        )

    def test_get_job_not_found(self) -> None:
        sid = self._require_source()
        if not sid:
            _result("GET job", False, "no source created")
            return
        r = self.client.get(f"{self.api}/{sid}/jobs/000000000000000000000000")
        _result("GET nonexistent job → 404", r.status_code == 404, f"status={r.status_code}")

    def test_delete_source(self) -> None:
        source = self._create_source(display_name="E2E Delete Target", tags=["e2e", "delete-test"])
        if not source:
            _result("DELETE create target", False, "could not create source for delete test")
            return
        sid = source["id"]

        r = self.client.delete(f"{self.api}/{sid}")
        _result("DELETE → 202", r.status_code == 202, f"status={r.status_code}")
        if r.status_code != 202:
            return
        body = r.json()
        _result("DELETE sourceId matches", body.get("sourceId") == sid)
        _result("DELETE has jobId", bool(body.get("jobId")))
        _result("DELETE status=deleting", body.get("status") == "deleting")

        if body.get("jobId"):
            self._poll_job(sid, body["jobId"], label="DELETE")

        self.created_ids.remove(sid)

    def cleanup(self) -> None:
        logger.info("--- Cleanup ---")
        for source_id in list(self.created_ids):
            r = self.client.delete(f"{self.api}/{source_id}")
            if r.status_code == 202:
                logger.info("  Deleted source %s (status=202)", source_id)
            else:
                logger.warning("  Failed to delete source %s (status=%d)", source_id, r.status_code)


def main() -> None:
    runner = SkillSyncE2E()
    runner.run_all()
    sys.exit(1 if FAIL > 0 else 0)


if __name__ == "__main__":
    main()
