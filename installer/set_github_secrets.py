#!/usr/bin/env python3
"""把 macOS 簽署 / 公證用的憑證存進 GitHub Actions Secrets（只需執行一次，換憑證時再跑）。

用法:
  installer/set_github_secrets.py [--repo owner/name] [--dir 憑證資料夾]

會設定的 secrets:
  MACOS_CERT_P12, MACOS_CERT_PASSWORD, NOTARY_KEY_P8, NOTARY_KEY_ID, NOTARY_ISSUER_ID

.p12 密碼在終端機以隱藏方式輸入，先用 openssl 驗證正確才上傳。
GitHub token 取自 git 的 credential helper（與 git push 用的相同，需 repo 權限）。
"""
from __future__ import annotations

import argparse
import base64
import getpass
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_DIR = Path.home() / "Library" / "Application Support" / "PlayRemote" / "signing"


def git_token() -> str:
    out = subprocess.run(["git", "credential", "fill"], input="protocol=https\nhost=github.com\n\n",
                         capture_output=True, text=True).stdout
    for line in out.splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1]
    raise SystemExit("取不到 GitHub token（請先成功 git push 一次讓鑰匙圈記住）。")


def api(token: str, url: str, method: str = "GET", data: dict | None = None) -> dict:
    req = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None, method=method)
    req.add_header("Authorization", f"token {token}")
    req.add_header("Accept", "application/vnd.github+json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as r:
            body = r.read()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        raise SystemExit(f"GitHub API {method} {url} 失敗: {e.code} {e.read().decode(errors='replace')[:300]}")


def p12_password_ok(p12: Path, pw: str) -> bool:
    for extra in ([], ["-legacy"]):
        r = subprocess.run(["openssl", "pkcs12", "-in", str(p12), "-passin", f"pass:{pw}", "-noout", *extra],
                           capture_output=True)
        if r.returncode == 0:
            return True
    return False


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--repo", default="stark640423/playremote")
    ap.add_argument("--dir", default=str(DEFAULT_DIR))
    ap.add_argument("--key-id", default="8KNMDXJDDG")
    ap.add_argument("--issuer-id", default="85ed977f-cb5c-4616-9bae-340241a609c0")
    a = ap.parse_args()

    try:
        from nacl import encoding, public
    except ImportError:
        raise SystemExit("缺少 pynacl：./.venv/bin/pip install pynacl")

    d = Path(a.dir)
    p12 = d / "DeveloperID.p12"
    p8 = d / f"AuthKey_{a.key_id}.p8"
    for f in (p12, p8):
        if not f.exists():
            raise SystemExit(f"找不到 {f}")

    for attempt in range(3):
        pw = getpass.getpass(".p12 密碼（匯出憑證時設的，輸入時不會顯示）: ")
        if p12_password_ok(p12, pw):
            print("密碼正確。")
            break
        print("密碼不對，再試一次。")
    else:
        raise SystemExit("三次都不對。請在原本那台 Mac 重新匯出 .p12 並設一個你記得的密碼。")

    token = git_token()
    base = f"https://api.github.com/repos/{a.repo}/actions/secrets"
    key = api(token, f"{base}/public-key")
    pk = public.PublicKey(key["key"].encode(), encoding.Base64Encoder())
    box = public.SealedBox(pk)

    secrets = {
        "MACOS_CERT_P12": base64.b64encode(p12.read_bytes()).decode(),
        "MACOS_CERT_PASSWORD": pw,
        "NOTARY_KEY_P8": base64.b64encode(p8.read_bytes()).decode(),
        "NOTARY_KEY_ID": a.key_id,
        "NOTARY_ISSUER_ID": a.issuer_id,
    }
    for name, value in secrets.items():
        enc = base64.b64encode(box.encrypt(value.encode())).decode()
        api(token, f"{base}/{name}", "PUT", {"encrypted_value": enc, "key_id": key["key_id"]})
        print(f"  已設定 {name}")

    existing = [s["name"] for s in api(token, base).get("secrets", [])]
    print("repo 目前的 secrets:", ", ".join(sorted(existing)))
    print("完成。之後推 v* 標籤，CI 就會產生已簽署並公證的 macOS DMG。")


if __name__ == "__main__":
    sys.exit(main())
