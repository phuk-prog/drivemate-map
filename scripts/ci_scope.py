#!/usr/bin/env python3
"""Conservative docs-only scope for public map preflight.

Preserves the required CI job. Uncertain file lists and manual runs receive the
full source preflight; a proven documentation-only change skips heavy setup.
"""
import json
import os
import sys
import urllib.parse
import urllib.request


def documentation_only(paths):
    paths = list(paths)
    return bool(paths) and all(
        p.endswith((".md", ".markdown")) or p in ("LICENSE", "NOTICE") for p in paths
    )


def github_api(route):
    req = urllib.request.Request(
        "https://api.github.com/repos/" + os.environ["GITHUB_REPOSITORY"] + "/" + route,
        headers={
            "Authorization": "Bearer " + os.environ["GH_TOKEN"],
            "Accept": "application/vnd.github+json",
            "User-Agent": "DriveMate-map-source-scope",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.load(response)


def files_for(event):
    kind = os.environ.get("GITHUB_EVENT_NAME")
    if kind == "workflow_dispatch":
        return None
    if kind == "pull_request":
        number = event["pull_request"]["number"]
        files = []
        for page in range(1, 5):
            data = github_api(f"pulls/{number}/files?per_page=100&page={page}")
            files.extend(
                name
                for entry in data
                for name in (entry["filename"], entry.get("previous_filename"))
                if name
            )
            if len(data) < 100:
                return files
        return None
    if kind == "push":
        before, after = event.get("before"), event.get("after")
        if not before or before == "0" * 40 or not after:
            return None
        b = urllib.parse.quote(before, safe="")
        a = urllib.parse.quote(after, safe="")
        data = github_api(f"compare/{b}...{a}")
        files = data.get("files", [])
        if len(files) >= 300:
            return None
        return [
            name
            for entry in files
            for name in (entry["filename"], entry.get("previous_filename"))
            if name
        ]
    return None


def main():
    reason, run_full = "Uncertain/source changes: full map source validation", True
    try:
        with open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8") as source:
            paths = files_for(json.load(source))
        if paths is not None and documentation_only(paths):
            reason, run_full = "Documentation-only: source preflight not required", False
    except Exception as error:
        reason = f"Could not verify changed files ({type(error).__name__}); full preflight"
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        output.write(f"run_full={'true' if run_full else 'false'}\n")
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as report:
        report.write(f"Scope: **{reason}**\n")
    print(reason)


if __name__ == "__main__":
    sys.exit(main() or 0)
