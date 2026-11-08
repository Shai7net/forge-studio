"""Forge Studio model downloader (standard library only).

  python download_models.py --list
  python download_models.py --preset recommended
  python download_models.py --groups sd15,controlnet,video
  python download_models.py --preset all --hf-token hf_xxx        (or set HF_TOKEN; needed for gated models)

Files land under forge/webui (paths in models.json). A finished file is checked against the size the server reports
and, for Hugging Face files, against their sha256; a broken download resumes where it stopped. aria2c is used when
it is on PATH (several connections: steadier on slow or unstable lines), otherwise Python downloads it itself.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CATALOG = os.path.join(HERE, "models.json")
WEBUI = os.path.join(ROOT, "forge", "webui")
AGENT = "ForgeStudio-installer/1.0"


def load_catalog(path=CATALOG):
    with open(path, encoding="utf8") as f:
        return json.load(f)


def full_url(catalog, url):
    return url if url.startswith("http") else catalog["hf"] + url


def human(n):
    return f"{n / 2**30:.1f} GB" if n >= 2**30 else f"{n / 2**20:.0f} MB"


def pick(catalog, groups):
    """The files of these groups (and the always-on ones), each destination once."""
    wanted = set(groups) | {g["id"] for g in catalog["groups"] if g.get("always")}
    seen, files = set(), []
    for f in catalog["files"]:
        if f["group"] in wanted and f["dest"] not in seen:
            seen.add(f["dest"])
            files.append(f)
    return files


class _Stay(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # keep the first answer: Hugging Face puts X-Linked-Size/-Etag on the redirect itself


def remote_info(url, token):
    """(size, sha256 or None) from a HEAD request; Hugging Face gives an LFS file's sha256 as X-Linked-Etag."""
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": AGENT})
    if token and "huggingface.co" in url:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.build_opener(_Stay).open(req, timeout=60) as r:
            headers = r.headers
    except urllib.error.HTTPError as e:
        if e.code not in (301, 302, 303, 307, 308):
            raise
        headers = e.headers
        if not headers.get("X-Linked-Size"):  # a plain redirect (GitHub releases): ask where it leads
            with urllib.request.urlopen(urllib.request.Request(url, method="HEAD", headers={"User-Agent": AGENT}),
                                        timeout=60) as r:
                headers = r.headers
    size = int(headers.get("X-Linked-Size") or headers.get("Content-Length") or 0)
    etag = (headers.get("X-Linked-Etag") or headers.get("ETag") or "").strip('"').replace("W/", "").strip('"')
    sha = etag if re.fullmatch(r"[0-9a-f]{64}", etag or "") else None
    return size, sha


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(16 * 2**20), b""):
            h.update(block)
    return h.hexdigest()


def download_python(url, part, size, token):
    """urllib with resume (Range) and retries."""
    for attempt in range(1, 9):
        have = os.path.getsize(part) if os.path.exists(part) else 0
        if size and have >= size:
            return
        req = urllib.request.Request(url, headers={"User-Agent": AGENT})
        if have:
            req.add_header("Range", f"bytes={have}-")
        if token and "huggingface.co" in url:
            req.add_header("Authorization", "Bearer " + token)
        try:
            with urllib.request.urlopen(req, timeout=60) as r, open(part, "ab" if have else "wb") as out:
                if have and r.status != 206:  # the server ignored the range: start over
                    out.seek(0)
                    out.truncate()
                    have = 0
                last = time.time()
                while True:
                    block = r.read(4 * 2**20)
                    if not block:
                        break
                    out.write(block)
                    have += len(block)
                    if time.time() - last > 3:
                        pct = f"{100 * have / size:5.1f}%" if size else ""
                        print(f"\r      {human(have)} / {human(size)} {pct}   ", end="", flush=True)
                        last = time.time()
            print(f"\r      {human(have)} / {human(size)}            ", flush=True)
            return
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as e:
            if isinstance(e, urllib.error.HTTPError) and e.code in (401, 403, 404):
                raise
            print(f"\n      connection broke ({e}); retrying in {5 * attempt} s", flush=True)
            time.sleep(5 * attempt)
    raise RuntimeError("download kept failing")


def download_aria2(aria, url, part, token):
    folder, name = os.path.split(part)
    cmd = [aria, "-x", "8", "-s", "8", "-k", "8M", "--file-allocation=none", "--continue=true", "--max-tries=20",
           "--retry-wait=5", "--summary-interval=0", "--console-log-level=warn", "--auto-file-renaming=false",
           "--allow-overwrite=true", "-d", folder, "-o", name, url]
    if token and "huggingface.co" in url:
        cmd[1:1] = ["--header", f"Authorization: Bearer {token}"]
    subprocess.run(cmd, check=True)


def fetch(catalog, entry, token, aria):
    url = full_url(catalog, entry["url"])
    dest = os.path.normpath(os.path.join(WEBUI, entry["dest"]))
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    try:
        size, sha = remote_info(url, token)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            page = url.split("/resolve/")[0]
            raise RuntimeError(f"not allowed ({e.code}). This model is gated: open {page} , accept its license with "
                               "your Hugging Face account, then run again with a token (HF_TOKEN or --hf-token).")
        raise
    if os.path.isfile(dest) and (not size or os.path.getsize(dest) == size):
        print(f"   ok  {entry['dest']} (already there)")
        return
    part = dest + ".part"
    print(f"   get {entry['dest']}  ({human(size)})", flush=True)
    if aria and size > 64 * 2**20:
        download_aria2(aria, url, part, token)
    else:
        download_python(url, part, size, token)
    got = os.path.getsize(part)
    if size and got != size:
        raise RuntimeError(f"size {got} instead of {size}; run again to resume")
    if sha:
        print("      checking sha256...", flush=True)
        if sha256_of(part) != sha:
            os.remove(part)
            raise RuntimeError("sha256 does not match (removed); run again")
    os.replace(part, dest)
    aria_ctl = part + ".aria2"
    if os.path.exists(aria_ctl):
        os.remove(aria_ctl)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true", help="show the groups and presets")
    ap.add_argument("--preset", help="minimal | recommended | video | all")
    ap.add_argument("--groups", help="comma-separated group ids")
    ap.add_argument("--hf-token", default=os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN"))
    ap.add_argument("--catalog", default=CATALOG)
    ap.add_argument("--webui", default=WEBUI, help="where forge/webui is (default: beside this installer)")
    args = ap.parse_args()
    globals()["WEBUI"] = os.path.abspath(args.webui)
    catalog = load_catalog(args.catalog)
    sizes = {}
    for f in catalog["files"]:
        sizes[f["group"]] = sizes.get(f["group"], 0) + f.get("mb", 0)
    if args.list or not (args.preset or args.groups):
        for g in catalog["groups"]:
            flag = " (always)" if g.get("always") else ""
            print(f"  {g['id']:<17} {sizes.get(g['id'], 0) / 1024:5.1f} GB  {g['title']}{flag}")
        print("\n  presets: " + ", ".join(f"{k} = {'+'.join(v)}" for k, v in catalog["presets"].items()))
        return 0
    groups = list(catalog["presets"].get(args.preset, [])) if args.preset else []
    if args.preset and args.preset not in catalog["presets"]:
        print(f"unknown preset {args.preset}")
        return 2
    groups += [g.strip() for g in (args.groups or "").split(",") if g.strip()]
    known = {g["id"] for g in catalog["groups"]}
    unknown = [g for g in groups if g not in known]
    if unknown:
        print("unknown groups: " + ", ".join(unknown))
        return 2
    files = pick(catalog, groups)
    gated = [f for f in files if f.get("gated")]
    if gated and not args.hf_token:
        print("FLUX 3 is gated and no Hugging Face token was given: skipping it (see README, 'FLUX 3').")
        files = [f for f in files if not f.get("gated")]
    total = sum(f.get("mb", 0) for f in files) * 2**20
    free = shutil.disk_usage(WEBUI if os.path.isdir(WEBUI) else ROOT).free
    print(f"Models: {len(files)} files, up to {human(total)} (free on this drive: {human(free)})")
    aria = shutil.which("aria2c")
    failed = []
    for entry in files:
        try:
            fetch(catalog, entry, args.hf_token, aria)
        except Exception as e:  # noqa: BLE001 - one failed file must not stop the rest
            print(f"   FAILED {entry['dest']}: {e}")
            failed.append(entry["dest"])
    if failed:
        print(f"\n{len(failed)} file(s) did not download. Run the same command again to resume them.")
        return 1
    print("\nAll chosen models are in place.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
