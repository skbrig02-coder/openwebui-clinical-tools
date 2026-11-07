"""Offline checks for the staged queue. Invoked by verify_all_tools.py."""

from __future__ import annotations

import json
import os
from pathlib import Path

import deployment_inventory


REPO = Path(__file__).resolve().parent


def run_stage_queue_tests() -> int:
    checks = 0

    def check(condition: bool, message: str) -> None:
        nonlocal checks
        if not condition:
            raise AssertionError(message)
        checks += 1

    print("=== Staged queue regressions (offline) ===")
    source = (REPO / "deployment_inventory.py").read_text(encoding="utf-8")
    check("import sqlite3" not in source, "inventory must not open a database")
    check("CookieJar" not in source and "http.cookiejar" not in source, "inventory must not read browser cookies")

    untested = deployment_inventory.inventory(repo_root=REPO, api_key="", base_url="http://127.0.0.1:3000")
    check(untested["environment"] == "repository_checkout_not_an_openwebui_instance", untested["environment"])
    check(untested["production_instance_pass"] is False, "a checkout must not be labeled as a production pass")
    check(untested["repository_tools"][0]["callable"] == "untested", untested["repository_tools"][0])
    check(len(untested["repository_tools"][0]["content_sha256"]) == 64, "missing content hash")
    check(untested["remote"]["status"] == "untested", json.dumps(untested["remote"])[:500])
    check(untested["remote"]["installed_tools"] is None, "missing key must not claim an installed tool count")
    check(untested["remote"]["live_registered_execution"]["status"] == "untested", "execution must stay untested")
    names = {item["file"]: item["version"] for item in untested["repository_tools"]}
    check(names.get("clinical_calculators.py") == "1.1.0", str(names))
    check("openfda_dailymed_drug_label.py" in names, str(names))
    rendered = json.dumps(untested)
    check("Bearer" not in rendered, "untested report leaked a credential header")

    secret = "STAGE-A-KEY-SHOULD-NOT-APPEAR"
    calls = []

    def transport(url: str, api_key: str, _extra: dict) -> tuple[int, object]:
        calls.append((url, api_key))
        if url.endswith("/api/v1/tools/"):
            return 503, {"detail": "down"}
        raise AssertionError(url)

    failed = deployment_inventory.inventory(
        repo_root=REPO,
        api_key=secret,
        base_url="http://127.0.0.1:9",
        transport=transport,
    )
    check(failed["status"] == "partial", json.dumps(failed)[:800])
    check(failed["remote"]["installed_tools"] is None, "HTTP failure must not become an empty install")
    check("not a finding that zero tools are installed" in failed["remote"]["note"], failed["remote"])
    check(secret not in json.dumps(failed), "API key was copied into the report")
    check(calls and calls[0][1] == secret, "transport should receive the env key")
    check(all(item[0].startswith("http://127.0.0.1:9/") for item in calls), calls)

    bad_url = deployment_inventory.inventory(repo_root=REPO, api_key=secret, base_url="file:///tmp")
    check(bad_url["remote"]["error_type"] == "invalid_input", bad_url["remote"])

    def ok_transport(url: str, api_key: str, _extra: dict) -> tuple[int, object]:
        check(api_key == secret, "key mismatch")
        if url.endswith("/api/v1/tools/"):
            return 200, [{"id": "calc-1", "name": "Calculators"}]
        if url.endswith("/api/v1/tools/list"):
            return 200, [
                {"id": "calc-1", "access_grants": [{"principal_type": "user", "principal_id": "u1", "permission": "read"}]},
                {"id": "old-tool", "name": "Disabled", "access_grants": []},
            ]
        if url.endswith("/api/v1/tools/id/calc-1"):
            return 200, {"id": "calc-1", "content": '"""\nversion: 1.1.0\n"""\n'}
        if url.endswith("/api/v1/tools/id/calc-1/valves"):
            return 200, {"INCLUDE_FORMULAS_AND_PMIDS": True}
        if "/api/v1/tools/id/old-tool" in url:
            return 200, {"id": "old-tool", "content": ""} if not url.endswith("/valves") else (200, {})
        if url.endswith("/api/v1/models/export"):
            return 200, [{"id": "local-clinical", "meta": {"toolIds": ["calc-1"]}}]
        raise AssertionError(url)

    armed = deployment_inventory.inventory(
        repo_root=REPO,
        api_key=secret,
        base_url="https://webui.example",
        transport=ok_transport,
    )
    remote = armed["remote"]
    check(remote["installed_tool_count"] == 2, remote)
    check(armed["environment"] == "authenticated_read_api_not_a_production_pass", armed["environment"])
    check(armed["production_instance_pass"] is False, armed)
    tool = next(item for item in remote["installed_tools"] if item["id"] == "calc-1")
    disabled = next(item for item in remote["installed_tools"] if item["id"] == "old-tool")
    check(disabled["registered"] == "yes" and disabled["enabled"] == "no", disabled)
    check(disabled["state_note"] == "registered, not enabled", disabled)
    check(disabled["callable"] == "untested" and disabled["reference_tested"] == "untested", disabled)
    check(tool["enabled"] == "yes" and tool["callable"] == "untested", tool)
    check(tool["id"] == "calc-1", tool)
    check(tool["installed_version"] == "1.1.0", tool)
    check(tool["valves"] == {"INCLUDE_FORMULAS_AND_PMIDS": True}, tool)
    check(tool["access_grants"][0]["permission"] == "read", tool)
    check(remote["model_tool_bindings_status"] == "ok", remote)
    check(remote["model_tool_bindings"][0]["toolIds"] == ["calc-1"], remote)
    check(remote["live_registered_execution"]["status"] == "untested", remote)
    check(secret not in json.dumps(armed), "authenticated report leaked the API key")

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        summary = deployment_inventory.write_backup(armed, Path(tmp))
        saved = json.loads((Path(tmp) / "calc-1.json").read_text(encoding="utf-8"))
        check(saved["id"] == "calc-1", saved)
        check(saved["valves"]["INCLUDE_FORMULAS_AND_PMIDS"] is True, saved)
        check(saved["access_grants_present"] is True, saved)
        check(summary["status"] == "ok", summary)

    def absent_bindings(url: str, api_key: str, _extra: dict) -> tuple[int, object]:
        if url.endswith("/api/v1/tools/"):
            return 200, [{"id": "t", "name": "T"}]
        if url.endswith("/api/v1/tools/list"):
            return 200, [{"id": "t", "access_grants": []}]
        if url.endswith("/api/v1/tools/id/t/valves"):
            return 404, {"detail": "none"}
        if url.endswith("/api/v1/tools/id/t"):
            return 200, {"id": "t", "content": ""}
        if url.endswith("/api/v1/models/export"):
            return 200, [{"id": "plain", "meta": {"description": "no binding field"}}]
        raise AssertionError(url)

    unknown = deployment_inventory.inventory(
        repo_root=REPO, api_key=secret, base_url="https://webui.example", transport=absent_bindings
    )
    check(unknown["remote"]["model_tool_bindings_status"] == "field_absent", unknown["remote"])
    check(unknown["remote"]["installed_tools"][0]["valves"] is None, unknown["remote"]["installed_tools"][0])
    check(unknown["remote"]["installed_tools"][0]["valves_status"] == "error", unknown["remote"]["installed_tools"][0])

    previous = os.environ.get("OPENWEBUI_API_KEY")
    os.environ.pop("OPENWEBUI_API_KEY", None)
    try:
        from io import StringIO
        import contextlib
        import sys

        buf = StringIO()
        with contextlib.redirect_stdout(buf):
            code = deployment_inventory.main(["--repo", str(REPO)])
        check(code == 0, "cli failed")
        cli_report = json.loads(buf.getvalue())
        check(cli_report["remote"]["status"] == "untested", cli_report["remote"]["status"])
    finally:
        if previous is not None:
            os.environ["OPENWEBUI_API_KEY"] = previous

    print(f"  [PASS] {checks} staged-queue checks passed.")
    return checks


if __name__ == "__main__":
    run_stage_queue_tests()
