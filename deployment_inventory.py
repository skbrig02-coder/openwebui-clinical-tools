"""
Read-only deployment inventory for this repository and, optionally, one Open WebUI.

Backward compatible: this file is new. It does not change tool signatures, valves,
or installed copies. It never writes to Open WebUI.

The API key is read only from the OPENWEBUI_API_KEY environment variable.
OPENWEBUI_BASE_URL is optional and defaults to http://127.0.0.1:3000.
This program does not open webui.db, browser cookies, or secret files.

Documented read endpoints, used only when a key is present
(Open WebUI docs and backend/open_webui/routers/tools.py):

- GET /api/v1/tools/
- GET /api/v1/tools/list
- GET /api/v1/tools/id/{id}
- GET /api/v1/tools/id/{id}/valves
- GET /api/v1/models/export

Live registered execution stays untested. The documented way to invoke a
registered tool is POST /api/chat/completions with tool_ids, which also runs
a model. This inventory does not call that endpoint.

When no API key is set, installed tools, valves, grants, model bindings, and
live execution are reported as untested. The local repository inventory still runs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


_VERSION_RE = re.compile(r"^version:\s*(\S+)\s*$", re.MULTILINE)
_TITLE_RE = re.compile(r"^title:\s*(.+)\s*$", re.MULTILINE)

Transport = Callable[[str, str, dict[str, str]], tuple[int, Any]]


def local_tool_inventory(repo_root: Path) -> list[dict[str, str]]:
    """Versions parsed from the standalone tool files in this repository."""
    found = []
    for path in sorted(repo_root.glob("*.py")):
        text = path.read_text(encoding="utf-8", errors="replace")
        head = "\n".join(text.splitlines()[:20])
        if "title:" not in head or "version:" not in head:
            continue
        title = (_TITLE_RE.search(head).group(1).strip() if _TITLE_RE.search(head) else path.stem)
        version_match = _VERSION_RE.search(head)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        found.append(
            {
                "file": path.name,
                "title": title,
                "version": version_match.group(1) if version_match else "not_declared",
                "content_sha256": digest,
                "registered": "untested",
                "enabled": "untested",
                "callable": "untested",
                "reference_tested": "untested",
                "note": "File is in this repository checkout. Instance registration was not queried.",
            }
        )
    return found


def _untested_remote(reason: str) -> dict[str, Any]:
    return {
        "status": "untested",
        "reason": reason,
        "installed_tools": None,
        "valves": None,
        "access_grants": None,
        "model_tool_bindings": None,
        "live_registered_execution": {
            "status": "untested",
            "reason": (
                "Open WebUI documents registered tool use only through "
                "POST /api/chat/completions with tool_ids, which also runs a model. "
                "This inventory does not call that endpoint."
            ),
        },
    }


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        return None


def _default_transport(url: str, api_key: str, _extra: dict[str, str] | None = None, timeout: float = 15.0) -> tuple[int, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "User-Agent": "OpenWebUI-Clinical-Tools-Inventory/1.0",
        },
        method="GET",
    )
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return response.status, json.loads(raw)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        payload: Any
        try:
            payload = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            payload = None
        return exc.code, payload


def _tool_ids_from_model(model: dict[str, Any]) -> tuple[list[str], bool]:
    """Return (ids, key_was_present). An absent key is not an empty binding list."""
    present = False
    ids: list[str] = []
    for container in (model, model.get("info") if isinstance(model.get("info"), dict) else {}):
        if not isinstance(container, dict):
            continue
        meta = container.get("meta")
        if isinstance(meta, dict) and "toolIds" in meta:
            present = True
            value = meta.get("toolIds")
            if isinstance(value, list):
                ids.extend(str(item) for item in value)
    return ids, present


def _version_in_content(content: str) -> str:
    match = _VERSION_RE.search(content or "")
    return match.group(1) if match else "not_declared"


def inventory(
    *,
    repo_root: Path,
    base_url: str | None = None,
    api_key: str | None = None,
    transport: Transport | None = None,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "generated_at_utc": _utc_now_iso(),
        "status": "ok",
        "environment": "repository_checkout_not_an_openwebui_instance",
        "production_instance_pass": False,
        "plan_status": "FIXED_NOT_INSTALLED for the six PR3 tools; this inventory is repository code and is not deployed",
        "provenance": "local_repository",
        "repository_tools": local_tool_inventory(repo_root),
        "credentials": "not_used",
        "remote": _untested_remote("OPENWEBUI_API_KEY is not set. No Open WebUI request was sent."),
    }
    key = (api_key or "").strip()
    if not key:
        return report

    root = (base_url or "http://127.0.0.1:3000").rstrip("/")
    if not root.startswith("http://") and not root.startswith("https://"):
        report["status"] = "error"
        report["remote"] = {
            "status": "error",
            "error_type": "invalid_input",
            "note": "OPENWEBUI_BASE_URL must start with http:// or https://. No request was sent.",
        }
        return report

    fetch = transport or _default_transport
    report["credentials"] = "environment"
    report["environment"] = "authenticated_read_api_not_a_production_pass"
    remote: dict[str, Any] = {
        "status": "ok",
        "base_url": root,
        "live_registered_execution": _untested_remote("")["live_registered_execution"],
    }

    def get(path: str) -> tuple[str, int, Any]:
        try:
            status, payload = fetch(root + path, key, {})
        except Exception:
            return "error", 0, {"error_type": "network_error"}
        if status == 200:
            return "ok", status, payload
        error_type = "malformed_response" if status == 200 else "http_error"
        if status == 0:
            error_type = "network_error"
        return "error", status, {"error_type": error_type, "http_status": status}

    list_state, list_http, listed = get("/api/v1/tools/")
    grant_state, grant_http, grants = get("/api/v1/tools/list")
    if list_state != "ok" or not isinstance(listed, list):
        remote["status"] = "error"
        remote["error_type"] = "http_error" if list_http not in (0, 200) else "malformed_response"
        if list_http == 0:
            remote["error_type"] = (listed or {}).get("error_type", "network_error") if isinstance(listed, dict) else "network_error"
        remote["http_status"] = list_http
        remote["installed_tools"] = None
        remote["note"] = "The tools list request failed. This is not a finding that zero tools are installed."
        report["status"] = "partial"
        report["remote"] = remote
        return report

    grant_by_id: dict[str, Any] = {}
    registered_items: list[dict[str, Any]] = []
    if grant_state == "ok" and isinstance(grants, list):
        for item in grants:
            if isinstance(item, dict) and item.get("id"):
                grant_by_id[str(item["id"])] = item.get("access_grants")
                registered_items.append(item)
        remote["access_grants_status"] = "ok"
        remote["registry_note"] = (
            "GET /api/v1/tools/ is the active tool list. GET /api/v1/tools/list is the broader registry. "
            "A tool present only in the registry is registered, not enabled."
        )
    else:
        remote["access_grants_status"] = "error"
        remote["access_grants_http_status"] = grant_http
        remote["registry_note"] = (
            "GET /api/v1/tools/list failed. Enabled tools from the active list are shown. "
            "This is not a finding that every registered tool is enabled."
        )

    active_ids = {str(item.get("id")) for item in listed if isinstance(item, dict) and item.get("id")}
    if registered_items:
        source_items = registered_items
        registry_complete = True
    else:
        source_items = [item for item in listed if isinstance(item, dict)]
        registry_complete = False

    tools_out = []
    seen_ids: set[str] = set()
    for item in source_items:
        if not isinstance(item, dict):
            continue
        tool_id = str(item.get("id") or "")
        detail_state, detail_http, detail = ("skipped", 0, None)
        valves_state, valves_http, valves = ("skipped", 0, None)
        if tool_id:
            quoted = urllib.parse.quote(tool_id, safe="")
            detail_state, detail_http, detail = get("/api/v1/tools/id/" + quoted)
            valves_state, valves_http, valves = get("/api/v1/tools/id/" + quoted + "/valves")
        content = ""
        if detail_state == "ok" and isinstance(detail, dict):
            content = str(detail.get("content") or "")
        seen_ids.add(tool_id)
        if registry_complete and tool_id in active_ids:
            enabled = "yes"
            state_note = "Present on the active tool list."
        elif registry_complete:
            enabled = "no"
            state_note = "registered, not enabled"
        elif tool_id in active_ids:
            enabled = "yes"
            state_note = "Active list only. Inactive registrations were not retrieved."
        else:
            enabled = "unknown"
            state_note = "Enablement could not be determined."
        entry = {
            "id": tool_id or None,
            "name": item.get("name"),
            "registered": "yes" if tool_id else "unknown",
            "enabled": enabled,
            "callable": "untested",
            "reference_tested": "untested",
            "state_note": state_note,
            "installed_version": _version_in_content(content) if content else "not_retrieved",
            "content_retrieved": bool(content),
            "detail_status": detail_state,
            "detail_http_status": detail_http,
            "valves_status": valves_state,
            "valves_http_status": valves_http,
            "valves": valves if valves_state == "ok" else None,
            "access_grants": grant_by_id.get(tool_id) if tool_id in grant_by_id else None,
            "access_grants_present": tool_id in grant_by_id,
        }
        tools_out.append(entry)
    for item in listed:
        if not isinstance(item, dict):
            continue
        tool_id = str(item.get("id") or "")
        if not tool_id or tool_id in seen_ids:
            continue
        tools_out.append(
            {
                "id": tool_id,
                "name": item.get("name"),
                "registered": "yes",
                "enabled": "yes",
                "callable": "untested",
                "reference_tested": "untested",
                "state_note": "Present on the active list and absent from the registry list.",
                "installed_version": "not_retrieved",
                "content_retrieved": False,
                "valves": None,
                "valves_status": "not_requested",
                "access_grants": None,
                "access_grants_present": False,
            }
        )
    remote["installed_tools"] = tools_out
    remote["installed_tool_count"] = len(tools_out)

    model_state, model_http, models = get("/api/v1/models/export")
    if model_state != "ok" or not isinstance(models, list):
        remote["model_tool_bindings"] = None
        remote["model_tool_bindings_status"] = "error"
        remote["model_export_http_status"] = model_http
        remote["model_note"] = "Model export failed. This is not a finding that zero tools are bound."
        remote["status"] = "partial"
        report["status"] = "partial"
    else:
        bindings = []
        saw_key = False
        for model in models:
            if not isinstance(model, dict):
                continue
            ids, present = _tool_ids_from_model(model)
            saw_key = saw_key or present
            bindings.append({"model_id": model.get("id"), "toolIds": ids, "toolIds_field_present": present})
        remote["model_tool_bindings"] = bindings
        if saw_key:
            remote["model_tool_bindings_status"] = "ok"
        else:
            remote["model_tool_bindings_status"] = "field_absent"
            remote["model_note"] = (
                "GET /api/v1/models/export returned models without meta.toolIds. "
                "Bindings are unknown, not an empty binding list."
            )
    report["provenance"] = "local_repository_and_openwebui_read_api"
    report["remote"] = remote
    return report


def write_backup(report: dict[str, Any], directory: Path) -> dict[str, Any]:
    """Write a local JSON backup of ids, content, valves, and grants already fetched.

    Does not call Open WebUI again and does not update the instance.
    """
    directory.mkdir(parents=True, exist_ok=True)
    remote = report.get("remote") or {}
    tools = remote.get("installed_tools")
    summary = {
        "generated_at_utc": report.get("generated_at_utc"),
        "status": "untested" if remote.get("status") == "untested" else "ok",
        "files": [],
        "note": "Backup preserves fetched ids, valves, and access_grants. It does not deploy tools.",
    }
    if not isinstance(tools, list):
        summary["status"] = "untested" if remote.get("status") == "untested" else "error"
        summary["note"] = "No installed tool payload was available to back up."
        (directory / "manifest.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        return summary
    for tool in tools:
        tool_id = str(tool.get("id") or "unknown")
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", tool_id)[:80]
        target = directory / f"{safe_name}.json"
        payload = {
            "id": tool.get("id"),
            "name": tool.get("name"),
            "installed_version": tool.get("installed_version"),
            "valves": tool.get("valves"),
            "valves_status": tool.get("valves_status"),
            "access_grants": tool.get("access_grants"),
            "access_grants_present": tool.get("access_grants_present"),
            "content_retrieved": tool.get("content_retrieved"),
        }
        target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        summary["files"].append(target.name)
    (directory / "manifest.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only Open WebUI and repository inventory.")
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parent))
    parser.add_argument("--backup-dir", default="")
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv)
    report = inventory(
        repo_root=Path(args.repo),
        base_url=os.environ.get("OPENWEBUI_BASE_URL"),
        api_key=os.environ.get("OPENWEBUI_API_KEY"),
    )
    if args.backup_dir:
        report["backup"] = write_backup(report, Path(args.backup_dir))
    text = json.dumps(report, indent=2)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
