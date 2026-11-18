import argparse
import logging
import os
import subprocess
import sys
import webbrowser
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from affiliate import retroactively_apply_affiliate_links  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("affiliate_assistant")

REPO_NAME = "kazufumi803/dtm-deals-autopilot"
SITE_URL = "https://dtm-deals-autopilot.pages.dev/"
X_PROFILE_URL = "https://x.com/DTMnewsbot"
AMAZON_SIGNUP_URL = "https://affiliate.amazon.co.jp/"
PB_SIGNUP_URL = "https://www.pluginboutique.com/affiliates"

JP_DESCRIPTION = (
    "音楽制作者（DTMer・作曲家・トラックメイカー）向けに、海外の最新DTMソフトウェア音源、"
    "VST/AUエフェクトプラグイン、オーディオインターフェースやMIDIキーボード等の音楽制作機材・"
    "セール情報を毎日日本語でわかりやすく解説・紹介する専門メディアです。製品の特徴や具体的な"
    "音楽ジャンルでの活用シーンを整理し、読者の楽曲制作環境の向上を支援するためにAmazonアソシエイトを利用します。"
)

EN_DESCRIPTION = (
    "SYNTH & PLUGIN DEAL RADAR (https://dtm-deals-autopilot.pages.dev/ and https://x.com/DTMnewsbot) "
    "is a dedicated Japanese media platform for music producers, composers, and audio engineers (DTMers). "
    "We publish daily curated reviews, practical sound-design guides, and deal alerts for VST/AU/AAX audio "
    "plugins, virtual instruments, sample packs, and studio gear. We drive targeted organic traffic via "
    "Google SEO and automated social media syndication on X (@DTMnewsbot) to help Japanese creators "
    "discover the best plugin deals on Plugin Boutique."
)


def copy_to_windows_clipboard(text: str) -> bool:
    """Windows の clip.exe を使ってテキストをクリップボードへセットする。"""
    try:
        proc = subprocess.Popen(
            ["clip"],
            stdin=subprocess.PIPE,
            close_fds=True,
        )
        proc.communicate(input=text.encode("utf-16-le"))
        return proc.returncode == 0
    except Exception as exc:
        logger.warning("クリップボードへのコピーに失敗しました: %s", exc)
        return False


def register_secrets_and_retro_push(amazon_tag: str = "", pb_tag: str = "") -> None:
    """
    指定されたアフィリエイトIDを GitHub Secrets に登録し、
    既存の全Markdown記事へ一括遡及適用して GitHub `main` ブランチへ Commit & Push する。
    """
    git_bin = r"C:\Program Files\Microsoft Visual Studio\2022\Community\Common7\IDE\CommonExtensions\Microsoft\TeamFoundation\Team Explorer\Git\cmd\git.exe"
    gh_bin = r"C:\Users\kazuh\.gemini\antigravity\brain\44fb155e-9d5c-43f8-8915-96e32473e742\scratch\gh\bin\gh.exe"

    # Git Credential Manager からトークンを取得して gh に渡す
    env = os.environ.copy()
    if os.path.exists(git_bin):
        proc = subprocess.run(
            [git_bin, "credential-manager", "get"],
            input="protocol=https\nhost=github.com\nusername=kazufumi803\n\n",
            text=True,
            capture_output=True,
            check=False,
        )
        for line in proc.stdout.splitlines():
            if line.startswith("password="):
                env["GH_TOKEN"] = line.split("=", 1)[1].strip()
                break

    if amazon_tag:
        os.environ["AFFILIATE_TAG_AMAZON"] = amazon_tag.strip()
        if os.path.exists(gh_bin) and env.get("GH_TOKEN"):
            subprocess.run(
                [gh_bin, "secret", "set", "AFFILIATE_TAG_AMAZON", "--body", amazon_tag.strip(), "-R", REPO_NAME],
                env=env,
                check=True,
            )
            logger.info("GitHub Secrets に AFFILIATE_TAG_AMAZON (%s) を登録しました。", amazon_tag.strip())

    if pb_tag:
        os.environ["AFFILIATE_TAG_PB"] = pb_tag.strip()
        if os.path.exists(gh_bin) and env.get("GH_TOKEN"):
            subprocess.run(
                [gh_bin, "secret", "set", "AFFILIATE_TAG_PB", "--body", pb_tag.strip(), "-R", REPO_NAME],
                env=env,
                check=True,
            )
            logger.info("GitHub Secrets に AFFILIATE_TAG_PB (%s) を登録しました。", pb_tag.strip())

    updated = retroactively_apply_affiliate_links(include_amazon_section=True)
    logger.info("既存記事 %d 件にアフィリエイトタグを遡及適用しました。", updated)

    if updated > 0 and os.path.exists(git_bin):
        subprocess.run([git_bin, "add", "site/src/content/deals/"], cwd=ROOT_DIR, check=True)
        subprocess.run(
            [git_bin, "commit", "-m", "chore(affiliate): retroactively apply affiliate tags to deals"],
            cwd=ROOT_DIR,
            check=False,
        )
        subprocess.run([git_bin, "pull", "--rebase", "origin", "main"], cwd=ROOT_DIR, check=False)
        subprocess.run([git_bin, "push", "origin", "main"], cwd=ROOT_DIR, check=True)
        logger.info("更新された記事を GitHub main ブランチへ Push しました。")


def launch_signup_pages_and_set_clipboard() -> None:
    """
    AmazonアソシエイトおよびPlugin Boutique登録ページを開き、
    申請フォーム用のテキストをクリップボードへセットする。
    """
    combined_clipboard = (
        f"【WebサイトURL】\n{SITE_URL}\n\n"
        f"【X (Twitter) URL】\n{X_PROFILE_URL}\n\n"
        f"【Amazonアソシエイト用 サイト紹介文（日本語）】\n{JP_DESCRIPTION}\n\n"
        f"【Plugin Boutique (Impact)用 サイト紹介文（英語）】\n{EN_DESCRIPTION}\n"
    )
    copy_to_windows_clipboard(combined_clipboard)

    try:
        webbrowser.open(AMAZON_SIGNUP_URL)
        webbrowser.open(PB_SIGNUP_URL)
    except Exception as exc:
        logger.info("ブラウザ自動起動をスキップしました: %s", exc)


def main() -> int:
    parser = argparse.ArgumentParser(description="Amazon / Plugin Boutique アフィリエイト半自動申請＆即時適用スクリプト")
    parser.add_argument("--set-amazon", default="", help="取得したAmazonアソシエイトID (例: mytag-22) を登録・全記事遡及適用")
    parser.add_argument("--set-pb", default="", help="取得したPlugin Boutique ID を登録・全記事遡及適用")
    parser.add_argument("--launch", action="store_true", help="申請ページを開いて紹介文をクリップボードにセットする")
    args = parser.parse_args()

    if args.set_amazon or args.set_pb:
        register_secrets_and_retro_push(amazon_tag=args.set_amazon, pb_tag=args.set_pb)
        return 0

    launch_signup_pages_and_set_clipboard()
    print(f"Website URL : {SITE_URL}")
    print(f"X Bot URL   : {X_PROFILE_URL}")
    print(f"Amazon URL  : {AMAZON_SIGNUP_URL}")
    print(f"PB URL      : {PB_SIGNUP_URL}")
    print(f"\n[JP Description]\n{JP_DESCRIPTION}")
    print(f"\n[EN Description]\n{EN_DESCRIPTION}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
