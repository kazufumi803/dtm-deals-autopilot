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
        logger.info("Webhook送信をスキップしました (slug=%s): %s", clean_slug, exc)
        return False


def post_to_x(
    tweet_text: str,
    slug: str,
    site_url: str | None = None,
    max_retries: int = 2,
    backoff_sec: float = 2.0,
) -> bool:
    """
    Xへの投稿は `/rss.xml` (dlvr.it 等) を主軸としつつ、
    X API や `WEBHOOK_URL` が有効な場合は補助的に送信を行う。
    X API が 401 Unauthorized 等を返した場合は警告ログを出さずに静かにスキップする。
    """
    api_key = os.environ.get("X_API_KEY", "").strip()
    api_secret = os.environ.get("X_API_SECRET", "").strip()
    access_token = os.environ.get("X_ACCESS_TOKEN", "").strip()
    access_token_secret = os.environ.get("X_ACCESS_TOKEN_SECRET", "").strip()

    full_message = build_tweet_message(tweet_text=tweet_text, slug=slug, site_url=site_url)
    x_success = False

    if all([api_key, api_secret, access_token, access_token_secret]):
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
                logger.info("X API直接投稿が完了しました (slug=%s, tweet_id=%s)", slug, tweet_id)
                x_success = True
                break
            except Exception as exc:
                err_str = str(exc)
                if isinstance(exc, tweepy.errors.Unauthorized) or "401" in err_str or "Unauthorized" in err_str:
                    # 401 エラー時は警告ログを出さず即座にスキップ（/rss.xml 経由の自動投稿へ委譲）
                    logger.info("X直接送信をスキップしました（/rss.xml 経由で自動配信されます: slug=%s）", slug)
                    break
                logger.info(
                    "X直接送信は完了しませんでした (slug=%s) [試行 %d/%d]: %s",
                    slug,
                    attempt,
                    max_retries,
                    exc,
                )
                if attempt < max_retries:
                    time.sleep(backoff_sec * attempt)
    else:
        logger.info("X直接送信は未設定のため /rss.xml 経由で配信します (slug=%s)", slug)

    webhook_success = post_to_webhook(tweet_text=tweet_text, slug=slug, site_url=site_url)
    return x_success or webhook_success
