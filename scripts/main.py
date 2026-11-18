import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# スクリプト単体実行時とモジュールインポート時の両方に対応
SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from affiliate import (  # noqa: E402
    convert_links_in_text,
    ensure_amazon_gear_section,
    resolve_entry_affiliate_url,
    retroactively_apply_affiliate_links,
)
from fetcher import (  # noqa: E402
    DEFAULT_POSTED_IDS_PATH,
    fetch_unprocessed_entries,
    load_posted_ids,
    save_posted_ids,
)
from generator import generate_deal_content  # noqa: E402
from publisher import post_to_x  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("main")

DEFAULT_DEALS_DIR = ROOT_DIR / "site" / "src" / "content" / "deals"
DEFAULT_PENDING_TWEETS_PATH = ROOT_DIR / "data" / "pending_tweets.json"
MAX_POSTED_IDS = 500


def load_pending_tweets(path: Path = DEFAULT_PENDING_TWEETS_PATH) -> list[dict[str, str]]:
    """次回またはデプロイ後投稿用の待機キューを読み込む。"""
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return [item for item in data if isinstance(item, dict)]
    except Exception as exc:
        logger.warning("pending_tweets.json の読み込みに失敗しました: %s", exc)
    return []


def save_pending_tweets(queue: list[dict[str, str]], path: Path = DEFAULT_PENDING_TWEETS_PATH) -> None:
    """待機キューを保存する。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(queue, f, ensure_ascii=False, indent=2)


def build_markdown_document(
    title: str,
    pub_date: str,
    category: str,
    affiliate_url: str,
    summary_md: str,
    tweet_text: str = "",
) -> str:
    """
    Astro Content Collections 用の Frontmatter 付き Markdown を生成する。
    JSON文字列エンコードを用いることでコロンや引用符を含むタイトルでもYAMLパースエラーを防ぐ。
    """
    valid_category = "Free" if category == "Free" else "Sale"
    enriched_body = ensure_amazon_gear_section(summary_md.strip())
    frontmatter_lines = [
        "---",
        f"title: {json.dumps(title, ensure_ascii=False)}",
        f"pubDate: {json.dumps(pub_date, ensure_ascii=False)}",
        f"category: {json.dumps(valid_category, ensure_ascii=False)}",
        f"affiliateUrl: {json.dumps(affiliate_url, ensure_ascii=False)}",
    ]
    if tweet_text:
        frontmatter_lines.append(f"tweetText: {json.dumps(tweet_text.strip(), ensure_ascii=False)}")
    frontmatter_lines.extend(
        [
            "---",
            "",
            enriched_body.strip(),
            "",
        ]
    )
    return "\n".join(frontmatter_lines)


def save_markdown_article(
    slug: str,
    title: str,
    pub_date: str,
    category: str,
    affiliate_url: str,
    summary_md: str,
    deals_dir: Path = DEFAULT_DEALS_DIR,
    tweet_text: str = "",
) -> tuple[Path, str]:
    """
    `/site/src/content/deals/{slug}.md` に記事を保存する。
    同名ファイルが既に存在する場合はタイムスタンプサフィックスを付与して衝突を回避する。
    """
    deals_dir.mkdir(parents=True, exist_ok=True)
    final_slug = slug
    target_path = deals_dir / f"{final_slug}.md"

    if target_path.exists():
        suffix = datetime.now(timezone.utc).strftime("%Y%m%d%H%M")
        final_slug = f"{slug}-{suffix}"
        target_path = deals_dir / f"{final_slug}.md"

    md_content = build_markdown_document(
        title=title,
        pub_date=pub_date,
        category=category,
        affiliate_url=affiliate_url,
        summary_md=summary_md,
        tweet_text=tweet_text,
    )
    target_path.write_text(md_content, encoding="utf-8")
    logger.info("Markdown記事を保存しました: %s", target_path)
    return target_path, final_slug


def flush_pending_tweets(
    pending_path: Path = DEFAULT_PENDING_TWEETS_PATH,
    delay_between_sec: float = 2.0,
) -> int:
    """
    待機キュー (`pending_tweets.json`) に保存されている未投稿ポストをXへ送信する。
    サイトのビルド・デプロイ完了後や次回実行時の冒頭で呼び出すことでリンク切れを防ぐ。
    """
    queue = load_pending_tweets(pending_path)
    if not queue:
        logger.info("未投稿の待機ポストはありません。")
        return 0

    logger.info("待機中のXポスト %d 件の送信を開始します。", len(queue))
    posted_count = 0

    for idx, item in enumerate(queue):
        slug = item.get("slug", "")
        tweet_text = item.get("tweet_text", "")
        if not slug or not tweet_text:
            continue

        try:
            if post_to_x(tweet_text=tweet_text, slug=slug):
                posted_count += 1
        except Exception as exc:
            logger.info("待機ポスト送信をスキップしました (slug=%s): %s", slug, exc)

        if idx < len(queue) - 1 and delay_between_sec > 0:
            time.sleep(delay_between_sec)

    # /rss.xml 経由で自動配信されるため、処理後は常にキューを空にして肥大化を防止する
    save_pending_tweets([], pending_path)
    logger.info("待機ポスト処理完了: 直接送信 %d 件 / キューをクリーンアップしました。", posted_count)
    return posted_count


def run_pipeline(
    posted_ids_path: Path = DEFAULT_POSTED_IDS_PATH,
    deals_dir: Path = DEFAULT_DEALS_DIR,
    pending_tweets_path: Path = DEFAULT_PENDING_TWEETS_PATH,
    max_items: int = 2,
    defer_x_post: bool = False,
    post_delay_sec: float = 0.0,
) -> int:
    """
    メインパイプライン:
    1. 前回実行分の待機ツイートがあれば先に送信
    2. RSSから未処理エントリを最大2件取得 (`fetcher`)
    3. アフィリエイトURLに変換 (`affiliate`)
    4. Gemini APIで日本語記事＆X告知文を生成 (`generator`)
    5. `/site/src/content/deals/{slug}.md` に保存
    6. `/data/posted_ids.json` にIDを追記保存（最新500件保持）
    7. 即時または遅延キュー経由でXに投稿 (`publisher`)
    """
    # 0. 環境変数にアフィリエイトタグが設定されていれば、既存の全公開済みMarkdown記事にも自動で遡及適用する
    retroactively_apply_affiliate_links(deals_dir=deals_dir, include_amazon_section=True)

    # 1. 前回分の待機ツイートがあれば先に消化
    flush_pending_tweets(pending_path=pending_tweets_path)

    # 2. RSSから未処理エントリを取得
    entries = fetch_unprocessed_entries(posted_ids_path=posted_ids_path, max_items=max_items)
    if not entries:
        logger.info("新規の未処理エントリはありませんでした。処理を終了します。")
        return 0

    posted_ids = load_posted_ids(posted_ids_path)
    pending_queue = load_pending_tweets(pending_tweets_path)
    processed_count = 0

    for entry in entries:
        entry_id = entry["id"]
        english_title = entry["title"]
        english_summary = entry.get("summary_text") or ""
        source_name = entry.get("source") or ""
        pub_date = entry.get("pub_date") or datetime.now(timezone.utc).isoformat()

        logger.info("エントリ処理開始 [%s]: %s", entry_id, english_title)

        try:
            # 3. アフィリエイトリンク変換
            affiliate_url = resolve_entry_affiliate_url(entry)

            # 4. Gemini API による日本語解説記事＆X告知文生成
            generated: dict[str, str] | None = generate_deal_content(
                english_title=english_title,
                english_summary=english_summary,
                source_name=source_name,
            )
            if not generated:
                logger.warning("記事生成に失敗したためこのエントリをスキップします: %s", english_title)
                continue

            # 本文中に含まれるURLも安全にアフィリエイト変換
            summary_md = convert_links_in_text(generated["summary_md"])

            # 5. Markdown ファイルの保存
            _, final_slug = save_markdown_article(
                slug=generated["slug"],
                title=generated["title"],
                pub_date=pub_date,
                category=generated["category"],
                affiliate_url=affiliate_url,
                summary_md=summary_md,
                deals_dir=deals_dir,
                tweet_text=generated.get("tweet_text", ""),
            )

            # 6. 処理済みIDを追記して即時保存（途中クラッシュ時の重複防止、最新500件保持）
            posted_ids.append(entry_id)
            save_posted_ids(posted_ids, posted_ids_path=posted_ids_path, max_keep=MAX_POSTED_IDS)
            processed_count += 1

            # 7. Xへの投稿（遅延キュー保存 または 待機後即時投稿）
            if defer_x_post:
                pending_queue.append(
                    {
                        "slug": final_slug,
                        "tweet_text": generated["tweet_text"],
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "attempts": "0",
                    }
                )
                save_pending_tweets(pending_queue, pending_tweets_path)
                logger.info("サイトデプロイ待ちのためX投稿をキューに保存しました (slug=%s)", final_slug)
            else:
                if post_delay_sec > 0:
                    logger.info("サイト反映待機のため %.1f 秒スリープします...", post_delay_sec)
                    time.sleep(post_delay_sec)
                post_to_x(tweet_text=generated["tweet_text"], slug=final_slug)

        except Exception as exc:
            logger.error("エントリ [%s] の処理中に予期しないエラーが発生しました。スキップして継続します: %s", entry_id, exc)
            continue

    logger.info("パイプライン完了: 新規記事 %d 件を生成・保存しました。", processed_count)
    return processed_count


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="DTM Deals Auto Publisher Pipeline")
    parser.add_argument(
        "--max-items",
        type=int,
        default=int(os.environ.get("MAX_ITEMS_PER_RUN", "2")),
        help="1回の実行で処理する最大エントリ数（デフォルト: 2）",
    )
    parser.add_argument(
        "--defer-x",
        action="store_true",
        default=os.environ.get("DEFER_X_POST", "").lower() in ("1", "true", "yes"),
        help="Xへの投稿を即時実行せず pending_tweets.json に保存する（デプロイ完了後や次回実行時に投稿）",
    )
    parser.add_argument(
        "--post-pending",
        action="store_true",
        help="pending_tweets.json に溜まっている未投稿ツイートのみを送信する",
    )
    parser.add_argument(
        "--retro-affiliate",
        action="store_true",
        help="公開済みの全Markdown記事にアフィリエイトタグを一括遡及適用する",
    )
    parser.add_argument(
        "--post-delay",
        type=float,
        default=float(os.environ.get("POST_DELAY_SECONDS", "0")),
        help="即時投稿モード時にX投稿前に待機する秒数",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.retro_affiliate:
        retroactively_apply_affiliate_links(include_amazon_section=True)
        return 0
    if args.post_pending:
        flush_pending_tweets()
        return 0

    run_pipeline(
        max_items=args.max_items,
        defer_x_post=args.defer_x,
        post_delay_sec=args.post_delay,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
