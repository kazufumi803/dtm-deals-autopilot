import logging
import os
import time

import requests
import tweepy

logger = logging.getLogger(__name__)

DEFAULT_SITE_URL = "https://dtm-deals-autopilot.pages.dev"


def build_tweet_message(tweet_text: str, slug: str, site_url: str | None = None) -> str:
    """
    tweet_text (110文字以内) の末尾に公開記事のURL (`{SITE_URL}/deals/{slug}`) を付与する。
    """
    base_url = (site_url or os.environ.get("SITE_URL") or DEFAULT_SITE_URL).strip().rstrip("/")
    clean_slug = slug.strip().strip("/")
    article_url = f"{base_url}/deals/{clean_slug}"
    clean_text = (tweet_text or "").strip()
    if len(clean_text) > 110:
        clean_text = clean_text[:109].rstrip() + "…"
    return f"{clean_text}\n{article_url}"


def post_to_webhook(
    tweet_text: str,
    slug: str,
    site_url: str | None = None,
    webhook_url: str | None = None,
) -> bool:
    """
    環境変数 `WEBHOOK_URL`（Discord / Make / IFTTT / Zapier 等）へ通知・自動投稿ペイロードを送信する。
    Discord用 (`content`)、Slack/Make用 (`text`, `url`, `slug`)、IFTTT用 (`value1`, `value2`, `value3`)
    のフィールドをすべて含めることで各種サービスにそのまま連携可能。
    """
    target_webhook = (webhook_url if webhook_url is not None else os.environ.get("WEBHOOK_URL", "")).strip()
    if not target_webhook:
        return False

    base_url = (site_url or os.environ.get("SITE_URL") or DEFAULT_SITE_URL).strip().rstrip("/")
    clean_slug = slug.strip().strip("/")
    article_url = f"{base_url}/deals/{clean_slug}"
    full_message = build_tweet_message(tweet_text=tweet_text, slug=slug, site_url=base_url)

    payload = {
        "content": full_message,
        "text": full_message,
        "tweet_text": (tweet_text or "").strip(),
        "url": article_url,
        "slug": clean_slug,
        "value1": (tweet_text or "").strip(),
        "value2": article_url,
        "value3": clean_slug,
    }

    try:
        response = requests.post(target_webhook, json=payload, timeout=15)
        response.raise_for_status()
        logger.info("Webhookへの送信が完了しました (slug=%s, status=%d)", clean_slug, response.status_code)
        return True
    except Exception as exc:
        logger.error("Webhook送信中にエラーが発生しました (slug=%s): %s", clean_slug, exc)
        return False


def post_to_x(
    tweet_text: str,
    slug: str,
    site_url: str | None = None,
    max_retries: int = 2,
    backoff_sec: float = 2.0,
) -> bool:
    """
    tweepy.Client (X API v2) を使用してポストを送信する。
    さらに環境変数 `WEBHOOK_URL` が設定されている場合は Webhook への送信も実行し、
    X API が認証エラーやクレジット制限で失敗しても Webhook 経由のフォールバックや
    RSS (`/rss.xml`) 配信によって記事更新パイプラインが一切止まらない設計とする。
    """
    api_key = os.environ.get("X_API_KEY", "").strip()
    api_secret = os.environ.get("X_API_SECRET", "").strip()
    access_token = os.environ.get("X_ACCESS_TOKEN", "").strip()
    access_token_secret = os.environ.get("X_ACCESS_TOKEN_SECRET", "").strip()

    full_message = build_tweet_message(tweet_text=tweet_text, slug=slug, site_url=site_url)
    x_success = False

    if not all([api_key, api_secret, access_token, access_token_secret]):
        logger.warning(
            "X API 認証環境変数 (X_API_KEY 等) が未設定のためX直接投稿をスキップします。生成文面:\n%s",
            full_message,
        )
    else:
        for attempt in range(1, max_retries + 1):
            try:
                client = tweepy.Client(
                    consumer_key=api_key,
                    consumer_secret=api_secret,
                    access_token=access_token,
                    access_token_secret=access_token_secret,
                )
                response = client.create_tweet(text=full_message)
                tweet_id = (
                    getattr(response, "data", {}).get("id")
                    if hasattr(response, "data") and response.data
                    else None
                )
                logger.info("Xへの投稿が完了しました (slug=%s, tweet_id=%s)", slug, tweet_id)
                x_success = True
                break
            except Exception as exc:
                logger.error(
                    "X API 投稿中にエラーが発生しました (slug=%s) [試行 %d/%d]: %s",
                    slug,
                    attempt,
                    max_retries,
                    exc,
                )
                if attempt < max_retries:
                    time.sleep(backoff_sec * attempt)

    # Webhook が設定されていれば送信（X API 失敗時のフォールバックとしても機能）
    webhook_success = post_to_webhook(tweet_text=tweet_text, slug=slug, site_url=site_url)

    if not x_success and not webhook_success:
        logger.warning(
            "X直接投稿は未完了ですが、RSSフィード (/rss.xml) への記事反映は完了しているためパイプライン処理を継続します (slug=%s)",
            slug,
        )

    return x_success or webhook_success
