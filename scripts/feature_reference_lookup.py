"""Anonymous, fixed-host reference reads; run with only /state writable.

No repository clone, downloaded code execution, credentials, redirects, arbitrary
URLs, source upload or third-party artwork reuse. All returned text is untrusted.
"""

import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import time
from urllib.parse import quote, urlencode
from urllib.request import build_opener, ProxyHandler, HTTPRedirectHandler, Request

MAX_REQUESTS = 16
MAX_BYTES = 512 * 1024


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Reader:
    def __init__(self):
        self.deadline = time.monotonic() + 90
        self.count = self.bytes = 0
        self.open = build_opener(ProxyHandler({}), NoRedirect()).open

    def get(self, path, *, query=None, limit=65536):
        if not path.startswith(("/repos/", "/search/repositories")) or any(
            c in path for c in "\r\n?#"
        ):
            raise ValueError("Unsupported fixed reference route")
        self.count += 1
        left = self.deadline - time.monotonic()
        if self.count > MAX_REQUESTS or left <= 0:
            raise ValueError("Reference lookup operation limit reached")
        url = "https://api.github.com" + path + ("?" + urlencode(query) if query else "")
        request = Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "TwichUI-Workbench-reference-reader",
                "X-GitHub-Api-Version": "2026-03-10",
            },
        )
        with self.open(request, timeout=min(8, left)) as response:
            if response.geturl() != url or response.status != 200:
                raise ValueError("Reference source redirected or was unavailable")
            data = response.read(limit + 1)
        self.bytes += len(data)
        if len(data) > limit or self.bytes > MAX_BYTES or time.monotonic() > self.deadline:
            raise ValueError("Reference response exceeded its operation limit")
        return json.loads(data)


def github(reader, full_name):
    if not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}", full_name
    ):
        raise ValueError("Invalid repository identity")
    route = "/repos/" + full_name
    metadata = reader.get(route)
    actual = metadata["full_name"]
    if actual.casefold() != full_name.casefold() or metadata.get("private") is not False:
        raise ValueError("Repository identity or access differs from the supplied public source")
    source_url = "https://github.com/" + actual
    if metadata["html_url"] != source_url or type(metadata["id"]) is not int:
        raise ValueError("Repository identity is not bound to its source URL")
    branch = metadata["default_branch"]
    if (
        not isinstance(branch, str)
        or not 1 <= len(branch) <= 200
        or any(ord(c) < 32 for c in branch)
    ):
        raise ValueError("Invalid default branch")
    revision = reader.get(route + "/commits/" + quote(branch, safe=""))["sha"]
    if not re.fullmatch("[a-f0-9]{40}", revision):
        raise ValueError("Reference revision is unavailable")
    value = dict(
        status="inspected",
        provider="github",
        project_id=str(metadata["id"]),
        name=actual,
        source_url=source_url,
        revision=revision,
        license=(metadata.get("license") or {}).get("spdx_id") or "Unknown",
        description=str(metadata.get("description") or "")[:800],
        game_version="Not established; Retail compatibility does not establish Forever compatibility.",
        documentation=None,
        limitations=["Read-only reference data. No code or artwork reuse is authorized."],
    )
    try:
        document = reader.get(route + "/readme", query={"ref": revision})
        name = document["path"]
        if document["encoding"] != "base64" or not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", name):
            raise ValueError("Unsupported reference documentation")
        raw = base64.b64decode(document["content"].replace("\n", ""), validate=True)
        if len(raw) > 49152 or len(raw) != document["size"] or b"\0" in raw:
            raise ValueError("Documentation exceeds its text boundary")
        text = raw.decode("utf-8")
        value["documentation"] = dict(
            path=name,
            source_url=source_url + "/blob/" + revision + "/" + quote(name),
            sha256=hashlib.sha256(raw).hexdigest(),
            bytes=len(raw),
            excerpt=text[:3000],
            excerpt_truncated=len(text) > 3000,
        )
    except ValueError, OSError, KeyError, TypeError:
        value["limitations"].append(
            "README unavailable through bounded anonymous access; repository metadata and revision were read."
        )
    return value


def inspect(reader, reference):
    kind, identity = reference["kind"], reference["identity"]
    if kind == "github":
        return github(reader, identity)
    if kind == "curseforge":
        if not re.fullmatch("[a-z0-9][a-z0-9-]{0,99}", identity):
            raise ValueError("Invalid CurseForge project identity")
        return dict(
            status="unavailable",
            provider=kind,
            name=identity,
            source_url="https://www.curseforge.com/wow/addons/" + identity,
            revision=None,
            license="Not inspected",
            game_version="Not established",
            documentation=None,
            limitations=[
                "Supported API access is not configured. No authenticated API call, scraping, download or compatibility claim was made."
            ],
        )
    if kind == "name":
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._'\-]{0,79}", identity):
            raise ValueError("Invalid reference name")
        found = reader.get(
            "/search/repositories",
            query={"q": identity + " in:name language:Lua", "per_page": 5},
            limit=131072,
        )
        choices = []
        for item in found["items"][:5]:
            full = item["full_name"]
            if (
                re.fullmatch(
                    r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}", full
                )
                and item["html_url"] == "https://github.com/" + full
                and not item.get("private")
            ):
                choices.append(
                    dict(
                        project_id=str(item["id"]),
                        name=full,
                        source_url=item["html_url"],
                        description=str(item.get("description") or "")[:300],
                    )
                )
        return dict(
            status="needs_clarification",
            provider="name",
            name=identity,
            source_url=None,
            revision=None,
            license="Not inspected",
            game_version="Not established",
            documentation=None,
            choices=choices,
            limitations=[
                "Choose the intended project or provide its GitHub or CurseForge project link. Search results do not establish addon identity."
            ],
        )
    raise ValueError("Unsupported reference provider")


def run(references, *, reader=None):
    if not isinstance(references, list) or not 1 <= len(references) <= 4:
        raise ValueError("Use one to four optional references")
    reader = reader or Reader()
    results = []
    for reference in references:
        try:
            result = inspect(reader, reference)
        except ValueError, OSError, KeyError, TypeError:
            result = dict(
                status="unavailable",
                provider=reference.get("kind"),
                name=reference.get("identity"),
                source_url=reference.get("source_url"),
                revision=None,
                license="Not inspected",
                game_version="Not established",
                documentation=None,
                limitations=[
                    "This reference could not be read through bounded anonymous access. No content was executed or copied."
                ],
            )
        results.append(
            {
                **result,
                "inspected_at": datetime.now(timezone.utc).isoformat(),
                "provenance": "Anonymous fixed-host metadata/documentation read; untrusted reference data.",
            }
        )
    return dict(
        version=1, references=results, request_count=reader.count, received_bytes=reader.bytes
    )


if __name__ == "__main__":
    __import__("os").umask(0o077)
    state = Path("/state")
    original = json.loads((state / "request.json").read_text())
    result = run(original["references"])
    with (state / "result.json").open("x") as out:
        json.dump(result, out, ensure_ascii=False, sort_keys=True)
        out.flush()
        __import__("os").fsync(out.fileno())
    print(
        json.dumps(
            dict(reference_count=len(result["references"]), request_count=result["request_count"])
        )
    )
