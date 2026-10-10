"""Delete every picture from the gallery. For use on or after 30/11/2026.

    uv run python scripts/wipe_gallery.py --yes

Without --yes it does nothing. The gallery is the GALLERY_URL in ~/.nerdfest/.env
(%USERPROFILE%\\.nerdfest\\.env on Windows); the URL is printed first so you can see
which one is being wiped. Deleting a picture also removes its votes.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # run as a script: find `kiosk`

import httpx  # noqa: E402

from kiosk.config import Secrets, load_secrets  # noqa: E402

HTTP_TIMEOUT_S = 30.0
MAX_ROUNDS = 50  # the gallery lists at most 500 pictures at a time


def _list_ids(http: httpx.Client, base: str) -> list[str]:
    response = http.get(f"{base}/api/images")
    response.raise_for_status()
    return [str(image["id"]) for image in response.json()["images"]]


def main(argv: list[str] | None = None, *, transport: httpx.BaseTransport | None = None,
         secrets: Secrets | None = None) -> int:
    parser = argparse.ArgumentParser(description="Delete every picture from the gallery.")
    parser.add_argument("--yes", action="store_true", help="really delete everything")
    args = parser.parse_args(argv)
    if not args.yes:
        print("Refusing to run: this deletes EVERY picture and vote in the gallery. "
              "Run it again with --yes if you mean it.", file=sys.stderr)
        return 2

    secrets = secrets if secrets is not None else load_secrets()
    base = secrets.gallery_url.rstrip("/")
    if not base or not secrets.gallery_token:
        print("GALLERY_URL or GALLERY_TOKEN is not set in the .env file.", file=sys.stderr)
        return 2
    print(f"Wiping the gallery at {base}")

    headers = {"Authorization": f"Bearer {secrets.gallery_token}"}
    deleted = 0
    failed: list[str] = []
    try:
        with httpx.Client(transport=transport, timeout=HTTP_TIMEOUT_S) as http:
            for round_no in range(MAX_ROUNDS):
                ids = [i for i in _list_ids(http, base) if i not in failed]
                if round_no == 0:
                    print(f"{len(ids)} picture(s) found")
                if not ids:
                    break
                for id_ in ids:
                    try:
                        ok = http.delete(f"{base}/api/images/{id_}", headers=headers).is_success
                    except httpx.HTTPError as exc:  # type only: messages can hold the URL
                        print(f"  {id_}: failed ({type(exc).__name__})", file=sys.stderr)
                        ok = False
                    if ok:
                        deleted += 1
                    else:
                        failed.append(id_)
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        print(f"Could not list the gallery ({type(exc).__name__}). "
              f"Deleted {deleted} before it stopped.", file=sys.stderr)
        return 1

    print(f"Deleted {deleted} picture(s).")
    if failed:
        print(f"{len(failed)} could not be deleted: {', '.join(failed)}. Run it again to retry.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
