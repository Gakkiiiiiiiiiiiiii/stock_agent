param(
    [string]$OutputPath,
    [int]$TimeoutSeconds = 180,
    [string]$CookieHeader
)

. "$PSScriptRoot\project-env.ps1"
Set-ProjectRuntimeEnv

if (-not $OutputPath) {
    $OutputPath = Get-BilibiliCookieFilePath
}

@'
import json
import sys

from engines.content.bilibili_auth import (
    BilibiliQrLoginClient,
    create_cookie_file_from_header,
    render_terminal_qr,
)

output_path = sys.argv[1]
timeout_seconds = int(sys.argv[2])
cookie_header = sys.argv[3]

if cookie_header:
    cookie_path = create_cookie_file_from_header(cookie_header, output_path)
    print(json.dumps({"cookie_path": str(cookie_path), "mode": "cookie_header"}, ensure_ascii=False, indent=2))
    raise SystemExit(0)

client = BilibiliQrLoginClient()
qr_payload = client.create_qr()
print("\u8bf7\u7528\u5df2\u5145\u7535\u7684 Bilibili \u8d26\u53f7\u626b\u7801\u786e\u8ba4\u767b\u5f55\uff1a")
print(render_terminal_qr(qr_payload["url"]))
print(qr_payload["url"])
verify = client.wait_for_login(qr_payload["qrcode_key"], timeout_seconds=timeout_seconds)
cookie_path = client.export_cookie_file(output_path=output_path)
print(json.dumps({"cookie_path": str(cookie_path), "verify": verify, "qr_url": qr_payload["url"]}, ensure_ascii=False, indent=2))
'@ | & (Get-ProjectPython) "-" $OutputPath "$TimeoutSeconds" $CookieHeader
