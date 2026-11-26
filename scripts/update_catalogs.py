"""Refresh the two Cheese catalogs from verified GitHub release files."""

import argparse
from dataclasses import dataclass
from functools import cache
from hashlib import sha256
from http.client import HTTPException
from itertools import zip_longest
import json
import os
from pathlib import Path
import re
from struct import unpack_from
from tempfile import TemporaryDirectory
from urllib.error import HTTPError
from urllib.request import Request, urlopen


CATALOGS = {"catalog.tsv": "IZSTORE2", "catalog-new.tsv": "IZSTORE3"}
CATALOG_DIR = Path(__file__).resolve().parent.parent / "catalog"
MAX_FILE_BYTES = 128 * 1024 * 1024
SLUG = r"[A-Za-z0-9_-][A-Za-z0-9._-]*"
RELEASE_URL = re.compile(
    rf"https://github\.com/({SLUG}/{SLUG})/releases/download/({SLUG})/({SLUG})"
)
SHA256 = re.compile(r"[0-9a-f]{64}")
COMPANION = re.compile(r"asset-([0-9a-f]{64})\.izdata")
VERSION = re.compile(r"([0-9]+(?:\.[0-9]+)*)([-_.A-Za-z][A-Za-z0-9._-]*)?")
USER_AGENT = "switch2-homebrew-catalog"

# Keep the exact build selected in the TSV. These publishers put version
# numbers in their filenames, and their templates match the current builds.
VERSIONED_FILENAMES = {
    "blitbun": "blitbun-{version}.iza",
    "game-vault": "game-vault-v{digits}.nro",
    "iztwins2": "IZtwiNS2-{version}.nro",
    "jksv-ns2": "JKSV-NS2-{version}.nro",
    "nxengine-evo": "Ounce-NXEngine-Evo-{core}.nro",
    "vacootube": "vacootube-{version}.iza",
}


class IncompatibleRelease(ValueError):
    """The current catalog entry should be kept."""


@dataclass(frozen=True)
class Build:
    app: tuple[str, ...]
    repository: str
    files: tuple[str, ...]
    url: str

    def lines(self):
        return (
            "\t".join(self.app),
            *([self.repository] if self.repository else []),
            *self.files,
            f"github\t{self.url}",
        )


@dataclass(frozen=True)
class Asset:
    name: str
    size: int
    digest: str
    url: str

    def row(self, name):
        return f"file\t{self.size}\t{self.digest}\t{name}"


def load_catalog(path, header):
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != header:
        raise ValueError(f"{path.name}: expected {header}")
    builds = []
    seen = set()
    cursor = 1
    while cursor < len(lines):
        app = tuple(lines[cursor].split("\t"))
        if len(app) != 8 or app[0] != "app" or not re.fullmatch(r"[a-z0-9_-]{1,47}", app[1]):
            raise ValueError(f"{path.name}:{cursor + 1}: invalid app row")
        app_id, kind = app[1:3]
        key = (app_id, kind) if header == "IZSTORE3" else app_id
        if kind not in {"iza", "nro"} or key in seen:
            raise ValueError(f"{path.name}:{cursor + 1}: invalid or duplicate build")
        seen.add(key)
        cursor += 1
        repository = ""
        if cursor < len(lines) and lines[cursor].startswith("repository\t"):
            repository = lines[cursor]
            cursor += 1
        files = []
        while cursor < len(lines) and lines[cursor].startswith("file\t"):
            files.append(lines[cursor])
            cursor += 1
        if not files or cursor == len(lines) or not lines[cursor].startswith("github\t"):
            raise ValueError(f"{path.name}: incomplete build {app_id}/{kind}")
        builds.append(Build(app, repository, tuple(files), lines[cursor].split("\t", 1)[1]))
        cursor += 1
    return builds


def version_of(tag):
    return re.sub(r"^[vV](?=[0-9])", "", tag)


def newer_version(version, current):
    new, old = VERSION.fullmatch(version), VERSION.fullmatch(current)
    if not new or not old:
        return False
    numbers = zip_longest(new[1].split("."), old[1].split("."), fillvalue="0")
    for left, right in numbers:
        if int(left) != int(right):
            return int(left) > int(right)
    new_suffix, old_suffix = (new[2] or "").lower(), (old[2] or "").lower()
    if not new_suffix and re.match(r"[-_.]?(alpha|beta|pre|rc)([0-9._-]|$)", old_suffix):
        return True
    # For ports with numbered suffixes, compare only within the same series.
    new_parts, old_parts = (re.split(r"([0-9]+)", suffix) for suffix in (new_suffix, old_suffix))
    if new_parts[::2] != old_parts[::2]:
        return False
    return [int(n) for n in new_parts[1::2]] > [int(n) for n in old_parts[1::2]]


def filename_for(app_id, name, current, version):
    template = VERSIONED_FILENAMES.get(app_id)
    if not template:
        return name

    def render(value):
        match = VERSION.fullmatch(value)
        core = match[1] if match else value
        return template.format(version=value, core=core, digits=core.replace(".", ""))

    return render(version) if name == render(current) else name


def read_release(project, token):
    request = Request(
        f"https://api.github.com/repos/{project}/releases/latest",
        headers={"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT},
    )
    if token:
        request.add_unredirected_header("Authorization", f"Bearer {token}")
    with urlopen(request, timeout=25) as response:
        return json.load(response)


@cache
def latest_release(project):
    token = os.environ.get("GH_TOKEN")
    try:
        return read_release(project, token)
    except HTTPError as error:
        with error:
            # An Actions installation token may have access only to this catalog repo.
            needs_public_request = token and (
                error.code == 404
                or (error.code == 403 and b"Resource not accessible by integration" in error.read())
            )
            if not needs_public_request:
                if error.code == 404:
                    return None
                raise
    try:
        return read_release(project, None)
    except HTTPError as error:
        with error:
            if error.code == 404:
                return None
            raise


def release_asset(raw, project, tag):
    name = raw["name"]
    size = raw.get("size")
    digest = raw.get("digest") or ""
    url = raw.get("browser_download_url") or ""
    address = RELEASE_URL.fullmatch(url)
    if (
        raw.get("state") != "uploaded"
        or type(size) is not int
        or not 0 < size <= MAX_FILE_BYTES
        or not digest.startswith("sha256:")
        or not SHA256.fullmatch(digest[7:])
        or not address
        or len(url) >= 512
        or address[1].lower() != project.lower()
        or address[2] != tag
        or address[3] != name
    ):
        raise IncompatibleRelease(f"invalid metadata for {name}")
    return Asset(name, size, digest[7:], url)


def verified_header(asset):
    request = Request(asset.url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=45) as response:
        head = response.read(min(4096, asset.size))
        digest = sha256(head)
        total = len(head)
        while chunk := response.read(1024 * 1024):
            total += len(chunk)
            if total > asset.size:
                break
            digest.update(chunk)
    if total != asset.size or digest.hexdigest() != asset.digest:
        raise IncompatibleRelease(f"size or SHA-256 mismatch for {asset.name}")
    return head


def validate_nro(head):
    if len(head) < 4096 or head[16:20] != b"NRO0":
        raise IncompatibleRelease("invalid NRO header")
    mod, = unpack_from("<I", head, 4)
    if 128 <= mod <= len(head) - 28 and head[mod:mod + 4] == b"MOD0":
        marker = b"OUN0\x02\x00\x00\x00\x01\x00\x00\x00"
        for offset in range(mod + 28, min(len(head), mod + 128) - 11, 4):
            if head[offset:offset + 12] == marker:
                return
    raise IncompatibleRelease("NRO lacks the Switch 2 platform marker")


def iza_companions(head, main, app_id, project, tag, assets):
    if len(head) < 512 or head[:8] != b"IZSDAPP1":
        raise IncompatibleRelease("expected a raw IZA launcher")
    version, header_size, size, flags = unpack_from("<4I", head, 8)
    if (version not in {1, 2} or header_size != 512 or size != main.size
            or head[24:72].split(b"\0", 1)[0] != app_id.encode("ascii")):
        raise IncompatibleRelease("IZA header or application ID does not match")
    if not flags & 8:
        return []
    count, total = unpack_from("<2I", head, 160)
    companions = sorted(
        (release_asset(raw, project, tag) for raw in assets if COMPANION.fullmatch(raw["name"])),
        key=lambda asset: asset.name,
    )
    if (version != 2 or not 0 < count <= 16 or count != len(companions)
            or len({asset.name for asset in companions}) != count
            or total != sum(asset.size for asset in companions)
            or any(asset.name != f"asset-{asset.digest}.izdata" for asset in companions)):
        raise IncompatibleRelease("IZA companion files do not match the launcher")
    # Match Cheese's canonical asset-list digest from the launcher header.
    rows = [[asset.name, asset.size, asset.digest] for asset in companions]
    manifest = b"iz2s.sd-assets.v1\n" + json.dumps(rows, separators=(",", ":")).encode("ascii")
    if sha256(manifest).digest() != head[368:400]:
        raise IncompatibleRelease("IZA companion manifest digest does not match")
    for asset in companions:
        verified_header(asset)
    return companions


@cache
def find_update(build):
    app_id, kind, current = build.app[1:4]
    address = RELEASE_URL.fullmatch(build.url)
    if not address or not address[3].lower().endswith(f".{kind}"):
        print(f"Keeping {app_id}/{kind}: unsupported download URL.")
        return build
    project, old_tag, old_name = address.groups()
    release = latest_release(project)
    if not release or release.get("draft") or release.get("prerelease"):
        print(f"Keeping {app_id}/{kind}: no published stable release.")
        return build
    tag = release.get("tag_name") or ""
    version = version_of(tag)
    if not re.fullmatch(SLUG, tag) or len(version) >= 24:
        print(f"Keeping {app_id}/{kind}: unsupported release tag.")
        return build
    if tag != old_tag and not (
        newer_version(version, current) and newer_version(version, version_of(old_tag))
    ):
        print(f"Keeping {app_id}/{kind}: {tag} does not supersede {old_tag}.")
        return build
    name = filename_for(app_id, old_name, version_of(old_tag), version)
    assets = release["assets"]
    matches = [asset for asset in assets if asset["name"] == name]
    if len(matches) != 1:
        print(f"Keeping {app_id}/{kind}: {tag} does not have a unique {name}.")
        return build
    try:
        main = release_asset(matches[0], project, tag)
        launcher = main.row(f"launcher.{kind}")
        if tag == old_tag and launcher == build.files[0]:
            return build
        head = verified_header(main)
        companions = []
        if kind == "nro":
            validate_nro(head)
        else:
            companions = iza_companions(head, main, app_id, project, tag, assets)
    except (IncompatibleRelease, OSError, HTTPException) as error:
        print(f"Keeping {app_id}/{kind}: {error}")
        return build
    app = (*build.app[:3], version, *build.app[4:])
    files = (launcher, *(asset.row(asset.name) for asset in companions))
    return Build(app, build.repository, files, main.url)


def refresh_catalogs(directory, dry_run):
    catalogs = {name: load_catalog(directory / name, header) for name, header in CATALOGS.items()}
    changes = {}
    for name, builds in catalogs.items():
        updated = []
        for build in builds:
            app_id, kind, version = build.app[1:4]
            # Moader needs a matching resident and backend. The legacy Cheese
            # self-updater requires its original companion asset.
            if app_id == "moader" or (app_id == "iz2s-cheese" and name == "catalog.tsv"):
                updated.append(build)
                continue
            candidate = find_update(build)
            if candidate != build:
                print(f"{name}: {app_id}/{kind} {version} -> {candidate.app[3]}")
            updated.append(candidate)
        if updated != builds:
            changes[name] = "\n".join([CATALOGS[name], *(line for build in updated for line in build.lines())]) + "\n"
    if changes and not dry_run:
        with TemporaryDirectory(dir=directory) as staging:
            for name, content in changes.items():
                (Path(staging) / name).write_text(content, encoding="utf-8")
            for name in changes:
                (Path(staging) / name).replace(directory / name)
    print(f"Checked {latest_release.cache_info().misses} GitHub repositories. "
          f"{len(changes)} catalog files {'would change' if dry_run else 'changed'}.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Check releases without changing either catalog")
    args = parser.parse_args()
    refresh_catalogs(CATALOG_DIR, args.dry_run)


if __name__ == "__main__":
    main()
