import argparse
import logging
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

logger = logging.getLogger(__name__)

URL_REGEX = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)
DEFAULT_DEALS_DIR = Path(__file__).resolve().parent.parent / "site" / "src" / "content" / "deals"


def _parse_tag_param(raw_tag: str, default_key: str) -> dict[str, str]:
    """
    環境変数の値が `key=value` 形式ならそのまま辞書化し、
    単なるID文字列なら `{default_key: raw_tag}` として返す。
    """
    cleaned = raw_tag.strip().lstrip("?&")
    if not cleaned:
        return {}
    if "=" in cleaned:
        parsed = dict(parse_qsl(cleaned, keep_blank_values=False))
        if parsed:
            return parsed
    return {default_key: cleaned}


def convert_affiliate_url(url: str) -> str:
    """
    対象ドメイン（Plugin Boutique / Amazon等）のURLに対して、
    環境変数で設定されたアフィリエイトタグをクエリパラメータに結合して返す。
    環境変数が未設定またはエラー発生時は元のURLをそのまま返す。
    """
    if not url or not isinstance(url, str):
        return url

    try:
        parsed = urlparse(url.strip())
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return url

        hostname = parsed.netloc.lower()
        extra_params: dict[str, str] = {}

        # Plugin Boutique 用アフィリエイトタグ (標準パラメータ: a_aid)
        pb_tag = os.environ.get("AFFILIATE_TAG_PB", "").strip()
        if pb_tag and ("pluginboutique.com" in hostname or "loopcloud.com" in hostname):
            extra_params.update(_parse_tag_param(pb_tag, default_key="a_aid"))

        # Amazon 用アフィリエイトタグ (標準パラメータ: tag)
        amazon_tag = os.environ.get("AFFILIATE_TAG_AMAZON", "").strip()
        if amazon_tag and ("amazon." in hostname):
            extra_params.update(_parse_tag_param(amazon_tag, default_key="tag"))

        if not extra_params:
            return url

        # 既存クエリパラメータを保持しつつアフィリエイトタグを上書き結合
        query_params = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query_params.update(extra_params)
        new_query = urlencode(query_params, doseq=True)

        updated_url = urlunparse(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                parsed.params,
                new_query,
                parsed.fragment,
            )
        )
        return updated_url
    except Exception as exc:
        logger.warning("URLのアフィリエイト変換中に例外が発生しました (%s): %s", url, exc)
        return url


def convert_links_in_text(text: str) -> str:
    """本文やFrontmatter中の対象URLにもアフィリエイトパラメータを付与する。"""
    if not text:
        return text

    def _replacer(match: re.Match[str]) -> str:
        return convert_affiliate_url(match.group(0))

    return URL_REGEX.sub(_replacer, text)


def resolve_entry_affiliate_url(entry: dict[str, Any]) -> str:
    """
    エントリ辞書から優先すべき遷移先URLを特定し、
    アフィリエイトパラメータを付与したURLを返す。
    本文中に Plugin Boutique や Amazon 等の対象リンクが含まれている場合はそれを優先する。
    """
    primary_url = entry.get("external_url") or entry.get("link") or ""
    summary_html = entry.get("summary_html") or entry.get("summary_text") or ""

    # 本文中に対象アフィリエイトドメインのURLがあれば優先抽出
    if summary_html:
        found_urls = URL_REGEX.findall(summary_html)
        for candidate in found_urls:
            lower = candidate.lower()
            if "pluginboutique.com" in lower or "amazon." in lower:
                return convert_affiliate_url(candidate)

    return convert_affiliate_url(primary_url)


def retroactively_apply_affiliate_links(deals_dir: Path = DEFAULT_DEALS_DIR) -> int:
    """
    すでに公開済みの全Markdown記事（`site/src/content/deals/*.md`）を走査し、
    Frontmatter (`affiliateUrl`) および本文内の全対象リンクに対して、
    現在設定されている `AFFILIATE_TAG_PB` / `AFFILIATE_TAG_AMAZON` を一括遡及付与する。
    更新されたファイル数を返す。
    """
    if not deals_dir.exists():
        return 0

    pb_tag = os.environ.get("AFFILIATE_TAG_PB", "").strip()
    amazon_tag = os.environ.get("AFFILIATE_TAG_AMAZON", "").strip()
    if not pb_tag and not amazon_tag:
        return 0

    updated_count = 0
    for md_path in sorted(deals_dir.glob("*.md")):
        try:
            original = md_path.read_text(encoding="utf-8")
            updated = convert_links_in_text(original)
            if updated != original:
                md_path.write_text(updated, encoding="utf-8")
                updated_count += 1
                logger.info("既存記事にアフィリエイトタグを遡及適用しました: %s", md_path.name)
        except Exception as exc:
            logger.warning("既存記事のアフィリエイト遡及更新に失敗しました (%s): %s", md_path, exc)

    if updated_count > 0:
        logger.info("既存記事 %d 件へのアフィリエイトタグ一括遡及適用が完了しました。", updated_count)
    return updated_count


def format_affiliate_application_guide(site_url: str = "https://dtm-deals-autopilot.pages.dev") -> str:
    """AmazonアソシエイトおよびPlugin Boutique (Impact) 申請用のコピペ用テキストを生成する。"""
    base = site_url.rstrip("/")
    return f"""========================================================================
【1. 登録URL一覧（コピペ用）】
- WebサイトURL (Website URL): {base}/
- RSSフィードURL (RSS Feed):  {base}/rss.xml
- サイトマップURL (Sitemap):   {base}/sitemap.xml
- サンプル記事URL 1:           {base}/deals/mthrbord-acl2500-free-vca-compressor
- サンプル記事URL 2:           {base}/deals/zc-devices-thicket-intro-sale

========================================================================
【2. Amazonアソシエイト申請用（日本語・コピペ用）】
■ サイト名:
SYNTH & PLUGIN DEAL RADAR（DTMプラグイン・セール＆無料配布ナビ）

■ サイト・モバイルアプリの内容 / 紹介文（250文字程度）:
音楽制作者（DTMer・作曲家・トラックメイカー）向けに、海外の最新DTMソフトウェア音源、VST/AUエフェクトプラグイン、オーディオインターフェースやMIDIキーボード等の音楽制作機材・セール情報を毎日日本語でわかりやすく解説・紹介する専門メディアです。製品の特徴や具体的な音楽ジャンルでの活用シーンを整理し、読者の楽曲制作環境の向上を支援するためにAmazonアソシエイトを利用します。

■ 扱っているジャンル / トピック:
楽器・音響機器 / PCソフト / 音楽制作（DTM・DAW）

■ 集客方法:
検索エンジン（SEO）、X（旧Twitter）およびRSSフィード購読

========================================================================
【3. Plugin Boutique / Loopcloud (Impact.com) 申請用（英語・コピペ用）】
■ Website / Company Display Name:
SYNTH & PLUGIN DEAL RADAR

■ Website Description / Promotional Methods (English):
SYNTH & PLUGIN DEAL RADAR ({base}/) is a dedicated Japanese media platform for music producers, composers, and audio engineers (DTMers). We publish daily curated reviews, practical sound-design guides, and deal alerts for VST/AU/AAX audio plugins, virtual instruments, sample packs, and studio gear. We drive targeted organic traffic via Google SEO and automated social media syndication on X (Twitter) to help Japanese creators discover the best plugin deals on Plugin Boutique.
========================================================================"""


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Affiliate URL Converter & Retroactive Updater")
    parser.add_argument(
        "--retro-apply",
        action="store_true",
        help="既存の全Markdown記事に環境変数のアフィリエイトタグを一括遡及適用する",
    )
    parser.add_argument(
        "--print-application-info",
        action="store_true",
        help="Amazonアソシエイト / Plugin Boutique (Impact) 申請用のコピペ文面を出力する",
    )
    args = parser.parse_args()
    if args.retro_apply:
        count = retroactively_apply_affiliate_links()
        print(f"Retroactively updated {count} markdown files.")
    if args.print_application_info or not args.retro_apply:
        print(format_affiliate_application_guide())
