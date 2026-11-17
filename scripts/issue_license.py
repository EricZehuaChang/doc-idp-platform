"""Issue a signed service license for a private deployment (vendor side only).

The private key stays on the vendor machine (default
D:\\Claude Code\\idp-license-private.pem, outside every repo) and must never
be copied into an image, a deploy package or git. app/license.py carries the
matching public key.

    python scripts/issue_license.py --customer 中银保 --expires 2026-12-31 --out license.json

`issued_at` is stamped with the current UTC time, so a license issued after a
`suspend` reopens the service, and any license issued before it stays locked.
"""
import argparse
import base64
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives import serialization

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.license import signing_payload  # noqa: E402  same canonical bytes as the verifier

DEFAULT_KEY = Path(r"D:\Claude Code\idp-license-private.pem")


def sign_license(customer: str, expires: str, private_key, issued_at: str | None = None) -> dict:
    """Return a license dict whose signature covers signing_payload(...)."""
    date.fromisoformat(expires)                       # reject typos before signing
    issued_at = issued_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    signature = private_key.sign(signing_payload(customer, expires, issued_at))
    return {"customer": customer, "expires": expires, "issued_at": issued_at,
            "signature": base64.b64encode(signature).decode("ascii")}


def main() -> None:
    parser = argparse.ArgumentParser(description="签发私有化部署服务授权文件")
    parser.add_argument("--customer", required=True, help="客户名称")
    parser.add_argument("--expires", required=True, help="有效期至 YYYY-MM-DD（含当天）")
    parser.add_argument("--private-key", type=Path, default=DEFAULT_KEY)
    parser.add_argument("--out", type=Path, default=Path("license.json"))
    args = parser.parse_args()

    if not args.private_key.exists():
        sys.exit(f"找不到私钥：{args.private_key}（不要新建，换钥会让所有已发授权失效）")
    key = serialization.load_pem_private_key(args.private_key.read_bytes(), password=None)
    lic = sign_license(args.customer, args.expires, key)
    args.out.write_text(json.dumps(lic, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已签发：{args.out}  客户={lic['customer']}  有效期至={lic['expires']}  "
          f"签发时间={lic['issued_at']}")


if __name__ == "__main__":
    main()
