import logging
import os
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

logger = logging.getLogger(__name__)

URL_REGEX = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)


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
    """本文やHTML中の対象URLにもアフィリエイトパラメータを付与する。"""
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
