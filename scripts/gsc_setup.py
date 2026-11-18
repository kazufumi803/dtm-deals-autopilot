import argparse
import html
import logging
import os
import re
import subprocess
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode, urlparse

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("gsc_setup")

ROOT_DIR = Path(__file__).resolve().parent.parent
BASE_LAYOUT_PATH = ROOT_DIR / "site" / "src" / "layouts" / "BaseLayout.astro"
DEFAULT_SITE_URL = "https://dtm-deals-autopilot.pages.dev/"

# Google Cloud SDK Application Default Credentials Public Installed-App Client
OAUTH_CLIENT_ID = "764086051850-6qr4p6gpi6hn506pt8ejuq83di341hur.apps.googleusercontent.com"
OAUTH_CLIENT_SECRET = "d-FL95Q19q7MQmFpd7hHD0Ty"
OAUTH_REDIRECT_URI = "http://localhost:8085/"
OAUTH_SCOPES = [
    "https://www.googleapis.com/auth/siteverification",
    "https://www.googleapis.com/auth/webmasters",
]


def extract_token(raw_input: str) -> str:
    """`<meta name="google-site-verification" content="XYZ" />` または生トークン文字列から `XYZ` を抽出する。"""
    cleaned = raw_input.strip()
    match = re.search(r'content=["\']([^"\']+)["\']', cleaned, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return cleaned


def inject_token_into_base_layout(token: str, layout_path: Path = BASE_LAYOUT_PATH) -> bool:
    """`BaseLayout.astro` のデフォルト `googleSiteVerification` フォールバック値にトークンを埋め込む。"""
    clean_token = extract_token(token)
    if not clean_token:
        return False

    content = layout_path.read_text(encoding="utf-8")
    pattern = r"(process\.env\.GOOGLE_SITE_VERIFICATION\s*\|\|\s*)['\"][^'\"]*['\"]"
    replacement = f"\\1'{clean_token}'"
    new_content, count = re.subn(pattern, replacement, content, count=1)

    if count == 0:
        logger.error("BaseLayout.astro 内の埋め込み対象箇所が見つかりませんでした。")
        return False

    if new_content != content:
        layout_path.write_text(new_content, encoding="utf-8")
        logger.info("BaseLayout.astro に google-site-verification トークン (%s) を埋め込みました。", clean_token)
        return True

    logger.info("BaseLayout.astro には既に同一のトークンが設定されています。")
    return False


def api_get_verification_token(access_token: str, site_url: str = DEFAULT_SITE_URL) -> str:
    """Google Site Verification API (`getToken`) から META タグ用トークンを取得する。"""
    endpoint = "https://www.googleapis.com/siteVerification/v1/token"
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    payload = {
        "site": {"type": "SITE", "identifier": site_url},
        "verificationMethod": "META",
    }
    resp = requests.post(endpoint, headers=headers, json=payload, timeout=20)
    resp.raise_for_status()
    token_data = resp.json()
    raw_token = token_data.get("token", "")
    logger.info("Site Verification API からメタタグを取得しました: %s", raw_token)
    return extract_token(raw_token)


def api_verify_site_and_submit_sitemap(access_token: str, site_url: str = DEFAULT_SITE_URL) -> None:
    """Site Verification API で所有権を確認し、Search Console API でサイト登録＆sitemap.xml送信を行う。"""
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    encoded_site = quote(site_url, safe="")
    sitemap_url = f"{site_url.rstrip('/')}/sitemap.xml"
    encoded_sitemap = quote(sitemap_url, safe="")

    # 1. Search Console にサイトプロパティを追加
    add_site_url = f"https://www.googleapis.com/webmasters/v3/sites/{encoded_site}"
    resp_add = requests.put(add_site_url, headers=headers, timeout=20)
    resp_add.raise_for_status()
    logger.info("Search Console にサイトプロパティを登録しました: %s", site_url)

    # 2. Site Verification API で所有権を Verify
    verify_url = "https://www.googleapis.com/siteVerification/v1/webResource?verificationMethod=META"
    verify_payload = {"site": {"type": "SITE", "identifier": site_url}}
    resp_verify = requests.post(verify_url, headers=headers, json=verify_payload, timeout=20)
    resp_verify.raise_for_status()
    logger.info("Site Verification API で所有権確認 (Verify) が完了しました: %s", resp_verify.json())

    # 3. Search Console API で sitemap.xml を送信
    submit_sitemap_url = f"https://www.googleapis.com/webmasters/v3/sites/{encoded_site}/sitemaps/{encoded_sitemap}"
    resp_sitemap = requests.put(submit_sitemap_url, headers=headers, timeout=20)
    resp_sitemap.raise_for_status()
    logger.info("Search Console にサイトマップを自動送信しました: %s", sitemap_url)


def git_commit_and_push(message: str = "chore(seo): add google-site-verification meta tag") -> None:
    """変更された BaseLayout.astro を GitHub main ブランチへ Commit & Push する。"""
    git_candidates = [
        r"C:\Program Files\Microsoft Visual Studio\2022\Community\Common7\IDE\CommonExtensions\Microsoft\TeamFoundation\Team Explorer\Git\cmd\git.exe",
        "git",
    ]
    git_bin = next((g for g in git_candidates if os.path.exists(g) or g == "git"), "git")
    subprocess.run([git_bin, "add", "site/src/layouts/BaseLayout.astro"], cwd=ROOT_DIR, check=True)
    subprocess.run([git_bin, "commit", "-m", message], cwd=ROOT_DIR, check=False)
    subprocess.run([git_bin, "pull", "--rebase", "origin", "main"], cwd=ROOT_DIR, check=False)
    subprocess.run([git_bin, "push", "origin", "main"], cwd=ROOT_DIR, check=True)


def wait_for_meta_tag_on_live_site(token: str, site_url: str = DEFAULT_SITE_URL, timeout_sec: int = 120) -> bool:
    """Cloudflare Pages 上のトップページに google-site-verification メタタグが反映されるまで待機する。"""
    clean_token = extract_token(token)
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            resp = requests.get(site_url, timeout=10)
            if resp.status_code == 200 and clean_token in resp.text:
                logger.info("本番サイト上で google-site-verification タグの反映を確認しました。")
                return True
        except requests.RequestException:
            pass
        time.sleep(5)
    return False


def build_oauth_authorize_url() -> str:
    params = {
        "client_id": OAUTH_CLIENT_ID,
        "redirect_uri": OAUTH_REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(OAUTH_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
    }
    return f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"


def exchange_code_for_access_token(code: str) -> str:
    resp = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "code": code,
            "client_id": OAUTH_CLIENT_ID,
            "client_secret": OAUTH_CLIENT_SECRET,
            "redirect_uri": OAUTH_REDIRECT_URI,
            "grant_type": "authorization_code",
        },
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def run_local_oauth_and_auto_register(site_url: str = DEFAULT_SITE_URL, timeout_sec: int = 300) -> bool:
    """
    ローカルポート 8085 で OAuth2 コールバックを待ち受け、
    ユーザーがブラウザで1クリック承認した瞬間にアクセストークンを取得して
    Site Verification 取得 → BaseLayout.astro 更新＆Push → Cloudflare 反映待機 → Verify → Sitemap 送信
    まで全自動で完遂する。
    """
    auth_url = build_oauth_authorize_url()
    captured: dict[str, str] = {}

    class OAuthCallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            if "code" in qs:
                captured["code"] = qs["code"][0]
                body = (
                    "<html><head><meta charset='utf-8'><title>認証完了</title></head>"
                    "<body style='background:#07090e;color:#e2e8f0;font-family:sans-serif;padding:40px;text-align:center;'>"
                    "<h2 style='color:#00f5d4;'>Google 認証が完了しました！</h2>"
                    "<p>現在バックグラウンドで メタタグ取得 → GitHub Push → Cloudflareデプロイ待機 → 所有権Verify → sitemap.xml送信 を全自動実行中です。</p>"
                    "<p>このタブは閉じて構いません。</p></body></html>"
                )
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(body.encode("utf-8"))
            else:
                err = html.escape(qs.get("error", ["unknown"])[0])
                captured["error"] = err
                self.send_response(400)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(f"OAuth Error: {err}".encode("utf-8"))

        def log_message(self, format: str, *args: object) -> None:
            return

    server = HTTPServer(("127.0.0.1", 8085), OAuthCallbackHandler)
    server.timeout = 1.0

    logger.info("=================================================================")
    logger.info("以下の Google OAuth 承認URLをブラウザで開いてアカウントを1クリック承認してください:")
    logger.info("%s", auth_url)
    logger.info("=================================================================")
    try:
        webbrowser.open(auth_url)
    except Exception:
        pass

    deadline = time.time() + timeout_sec
    while time.time() < deadline and "code" not in captured and "error" not in captured:
        server.handle_request()
    server.server_close()

    if "code" not in captured:
        logger.error("OAuth 承認コードを取得できませんでした (%s)", captured.get("error", "timeout"))
        return False

    logger.info("OAuth 承認コードを受信しました。アクセストークンへ交換中...")
    access_token = exchange_code_for_access_token(captured["code"])
    token = api_get_verification_token(access_token, site_url=site_url)
    changed = inject_token_into_base_layout(token)
    if changed:
        git_commit_and_push()
        wait_for_meta_tag_on_live_site(token, site_url=site_url)
    api_verify_site_and_submit_sitemap(access_token, site_url=site_url)
    logger.info("Google Search Console の所有権確認と sitemap.xml の自動送信がすべて完了しました！")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Google Search Console 自動設定・サイトマップ送信スクリプト")
    parser.add_argument(
        "--token",
        default=os.environ.get("GOOGLE_SITE_VERIFICATION", ""),
        help="google-site-verification のメタタグ全体または content 値",
    )
    parser.add_argument(
        "--access-token",
        default=os.environ.get("GSC_ACCESS_TOKEN", ""),
        help="Google OAuth2 アクセストークン（指定時は API 経由で取得・Verify・サイトマップ送信まで全自動実行）",
    )
    parser.add_argument(
        "--oauth-local",
        action="store_true",
        help="ローカルポート8085でOAuth認証を起動し、1クリック承認後に全自動でメタタグ取得・Push・Verify・sitemap送信を行う",
    )
    parser.add_argument(
        "--site-url",
        default=DEFAULT_SITE_URL,
        help="対象サイトURL（末尾スラッシュ付き）",
    )
    parser.add_argument(
        "--push",
        action="store_true",
        help="BaseLayout.astro 更新後に自動で git commit & push を実行する",
    )
    args = parser.parse_args()

    if args.oauth_local:
        ok = run_local_oauth_and_auto_register(site_url=args.site_url)
        return 0 if ok else 1

    if args.access_token:
        token = api_get_verification_token(args.access_token, site_url=args.site_url)
        changed = inject_token_into_base_layout(token)
        if changed or args.push:
            git_commit_and_push()
            wait_for_meta_tag_on_live_site(token, site_url=args.site_url)
        api_verify_site_and_submit_sitemap(args.access_token, site_url=args.site_url)
        return 0

    if args.token:
        changed = inject_token_into_base_layout(args.token)
        if changed and args.push:
            git_commit_and_push()
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
