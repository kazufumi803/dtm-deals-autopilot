import hashlib
import json
import logging
import re
import time
from calendar import timegm
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import feedparser
import requests

logger = logging.getLogger(__name__)

RSS_FEEDS = [
    {
        "name": "Reddit r/AudioProductionDeals",
        "url": "https://www.reddit.com/r/AudioProductionDeals/new/.rss",
    },
    {
        "name": "KVR Audio Deals & Free",
        "url": "https://www.kvraudio.com/rss/kvr_deals.rss",
    },
]

DEFAULT_POSTED_IDS_PATH = Path(__file__).resolve().parent.parent / "data" / "posted_ids.json"
USER_AGENT = "DTMDealsAutoPublisher/1.0 (+https://github.com/dtm-deals-bot)"


def compute_entry_id(url: str, guid: str = "") -> str:
    """URLまたはGUIDから一意のハッシュIDを生成する。"""
    raw = (url.strip() or guid.strip()).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def load_posted_ids(posted_ids_path: Path = DEFAULT_POSTED_IDS_PATH) -> list[str]:
    """処理済みIDリストをJSONから読み込む。"""
    if not posted_ids_path.exists():
        return []
    try:
        with posted_ids_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return [str(x) for x in data]
    except Exception as exc:
        logger.warning("posted_ids.json の読み込みに失敗しました (%s)。空リストとして扱います。", exc)
    return []


def save_posted_ids(
    posted_ids: list[str],
    posted_ids_path: Path = DEFAULT_POSTED_IDS_PATH,
    max_keep: int = 500,
) -> None:
    """処理済みIDリストを最新 max_keep 件に制限して保存する。"""
    posted_ids_path.parent.mkdir(parents=True, exist_ok=True)
    # 順序を維持しつつ重複排除し、末尾 max_keep 件を保持
    deduped = list(dict.fromkeys(posted_ids))[-max_keep:]
    with posted_ids_path.open("w", encoding="utf-8") as f:
        json.dump(deduped, f, ensure_ascii=False, indent=2)


def _fetch_rss_content(url: str, retries: int = 3, backoff_sec: float = 2.0) -> bytes | None:
    """リトライ付きでRSSフィードを取得する。"""
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
    }
    for attempt in range(1, retries + 1):
        try:
            response = requests.get(url, headers=headers, timeout=20)
            response.raise_for_status()
            return response.content
        except requests.RequestException as exc:
            logger.warning(
                "RSS取得失敗 (%s) [試行 %d/%d]: %s",
                url,
                attempt,
                retries,
                exc,
            )
            if attempt < retries:
                time.sleep(backoff_sec * (2 ** (attempt - 1)))
    logger.error("RSSフィードの取得をスキップします: %s", url)
    return None


def _extract_published_timestamp(entry: Any) -> float:
    """エントリの公開日時をUNIXタイムスタンプで返す。取得できない場合は0.0。"""
    for attr in ("published_parsed", "updated_parsed", "created_parsed"):
        parsed = getattr(entry, attr, None) or entry.get(attr)
        if parsed:
            try:
                return float(timegm(parsed))
            except Exception:
                pass
    return 0.0


def _strip_html(html_text: str) -> str:
    """簡易的にHTMLタグを除去してプレーンテキストを抽出する。"""
    if not html_text:
        return ""
    text = re.sub(r"<[^>]+>", " ", html_text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _extract_external_url(link: str, summary_html: str) -> str:
    """
    Reddit等のフィードでは entry.link がスレッドURLになる場合があるため、
    本文内に `[link]` や外部ショップURLがあれば優先的に抽出する。
    """
    if "reddit.com" in link and summary_html:
        # Reddit RSS の <a href="external_url">[link]</a> パターンを検出
        match = re.search(r'<a\s+href=["\'](https?://[^"\']+)["\'][^>]*>\s*\[link\]\s*</a>', summary_html, re.I)
        if match:
            candidate = match.group(1)
            if "reddit.com" not in candidate:
                return candidate
        # それ以外の外部リンクを探索
        all_links = re.findall(r'href=["\'](https?://[^"\']+)["\']', summary_html, re.I)
        for candidate in all_links:
            if not any(domain in candidate for domain in ("reddit.com", "redd.it")):
                return candidate
    return link


def fetch_unprocessed_entries(
    posted_ids_path: Path = DEFAULT_POSTED_IDS_PATH,
    max_items: int = 2,
    feeds: list[dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """
    全RSSフィードから未処理のエントリを収集し、
    新しい順に最大 `max_items` 件（デフォルト2件）を返す。
    """
    posted_ids_set = set(load_posted_ids(posted_ids_path))
    target_feeds = feeds if feeds is not None else RSS_FEEDS
    candidates: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for feed_info in target_feeds:
        feed_name = feed_info.get("name", "Unknown Feed")
        feed_url = feed_info["url"]
        logger.info("フィード取得中: %s (%s)", feed_name, feed_url)

        raw_bytes = _fetch_rss_content(feed_url)
        if not raw_bytes:
            continue

        try:
            parsed = feedparser.parse(raw_bytes)
        except Exception as exc:
            logger.error("RSSのパースに失敗しました (%s): %s", feed_url, exc)
            continue

        for entry in getattr(parsed, "entries", []):
            try:
                title = (entry.get("title") or "").strip()
                link = (entry.get("link") or "").strip()
                guid = (entry.get("id") or link).strip()

                if not title or not link:
                    continue

                entry_id = compute_entry_id(link, guid)
                if entry_id in posted_ids_set or entry_id in seen_ids:
                    continue

                summary_raw = ""
                if entry.get("content") and isinstance(entry["content"], list):
                    summary_raw = entry["content"][0].get("value", "")
                if not summary_raw:
                    summary_raw = entry.get("summary") or entry.get("description") or ""

                external_url = _extract_external_url(link, summary_raw)
                summary_text = _strip_html(summary_raw)
                timestamp = _extract_published_timestamp(entry)
                pub_date_iso = (
                    datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()
                    if timestamp > 0
                    else datetime.now(timezone.utc).isoformat()
                )

                seen_ids.add(entry_id)
                candidates.append(
                    {
                        "id": entry_id,
                        "source": feed_name,
                        "title": title,
                        "link": link,
                        "external_url": external_url,
                        "summary_html": summary_raw,
                        "summary_text": summary_text,
                        "published_ts": timestamp,
                        "pub_date": pub_date_iso,
                    }
                )
            except Exception as exc:
                logger.warning("エントリの解析中に例外が発生したためスキップします: %s", exc)
                continue

    # 公開日時が新しい順にソート
    candidates.sort(key=lambda item: item["published_ts"], reverse=True)
    selected = candidates[:max_items]
    logger.info("未処理エントリ %d 件中、最新 %d 件を処理対象として抽出しました。", len(candidates), len(selected))
    return selected
