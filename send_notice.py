"""Send an approved notification to Apple's deployed LINE bridge."""
import argparse
import json
import os
from pathlib import Path
import urllib.request
import urllib.error
import uuid


def load_local_env():
    path = Path(__file__).with_name(".env")
    if path.exists():
        for line in path.read_text().splitlines():
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key, value)


def main():
    load_local_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", required=True, help="UTF-8 notification text file")
    parser.add_argument("--retry-key", default=str(uuid.uuid4()), help="Reuse this UUID if retrying the same notification")
    args = parser.parse_args()
    uuid.UUID(args.retry_key)
    base = os.environ.get("NOTIFY_BASE_URL", "").rstrip("/")
    key = os.environ.get("NOTIFY_API_KEY", "")
    if not base.startswith("https://") or not key:
        parser.error("Set NOTIFY_BASE_URL (https) and NOTIFY_API_KEY in local .env")
    payload = json.dumps({"text": Path(args.file).read_text()}, ensure_ascii=False).encode()
    request = urllib.request.Request(base + "/api/notify", data=payload,
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json",
                 "X-Line-Retry-Key": args.retry_key})
    print("Notification retry key:", args.retry_key, flush=True)
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            print(response.read().decode())
    except urllib.error.HTTPError as error:
        print("Notification failed: HTTP", error.code)
        raise SystemExit(1) from None
    except (urllib.error.URLError, TimeoutError):
        print("Connection failed. Retry with the same --retry-key to avoid duplicate delivery.")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
