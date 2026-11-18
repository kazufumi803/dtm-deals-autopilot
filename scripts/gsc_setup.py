import argparse
import logging
import os
import re
import subprocess
import time
from pathlib import Path
from urllib.parse import quote

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("gsc_setup")

ROOT_DIR = Path(__file__).resolve().parent.parent
BASE_LAYOUT_PATH = ROOT_DIR / "site" / "src" / "layouts" / "BaseLayout.astro"
DEFAULT_SITE_URL = "https://dtm-deals-autopilot.pages.dev/"


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
    # 既存のフォールバック空文字またはトークン文字列を置換
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
        "git",
        r"C:\Program Files\Microsoft Visual Studio\2022\Community\Common7\IDE\CommonExtensions\Microsoft\TeamFoundation\Team Explorer\Git\cmd\git.exe",
    ]
    git_bin = next((g for g in git_candidates if os.path.exists(g) or g == "git"), "git")
    subprocess.run([git_bin, "add", "site/src/layouts/BaseLayout.astro"], cwd=ROOT_DIR, check=True)
    subprocess.run([git_bin, "commit", "-m", message], cwd=ROOT_DIR, check=False)
    subprocess.run([git_bin, "pull", "--rebase", "origin", "main"], cwd=ROOT_DIR, check=False)
    subprocess.run([git_bin, "push", "origin", "main"], cwd=ROOT_DIR, check=True)


def wait_for_meta_tag_on_live_site(token: str, site_url: str = DEFAULT_SITE_URL, timeout_sec: int = 90) -> bool:
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
