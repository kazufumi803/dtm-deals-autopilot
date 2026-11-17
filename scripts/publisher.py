import logging
import os
import time

import tweepy

logger = logging.getLogger(__name__)

DEFAULT_SITE_URL = "https://dtm-deals.example.com"


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


def post_to_x(
    tweet_text: str,
    slug: str,
    site_url: str | None = None,
    max_retries: int = 2,
    backoff_sec: float = 2.0,
) -> bool:
    """
    tweepy.Client (X API v2) を使用してポストを送信する。
    認証情報未設定やAPIエラーが発生しても例外を外に投げず False を返し、処理を継続させる。
    """
    api_key = os.environ.get("X_API_KEY", "").strip()
    api_secret = os.environ.get("X_API_SECRET", "").strip()
    access_token = os.environ.get("X_ACCESS_TOKEN", "").strip()
    access_token_secret = os.environ.get("X_ACCESS_TOKEN_SECRET", "").strip()

    full_message = build_tweet_message(tweet_text=tweet_text, slug=slug, site_url=site_url)

    if not all([api_key, api_secret, access_token, access_token_secret]):
        logger.warning(
            "X API 認証環境変数 (X_API_KEY 等) が未設定のため投稿をスキップします。生成文面:\n%s",
            full_message,
        )
        return False

    for attempt in range(1, max_retries + 1):
        try:
            client = tweepy.Client(
                consumer_key=api_key,
                consumer_secret=api_secret,
                access_token=access_token,
                access_token_secret=access_token_secret,
            )
            response = client.create_tweet(text=full_message)
            tweet_id = getattr(response, "data", {}).get("id") if hasattr(response, "data") and response.data else None
            logger.info("Xへの投稿が完了しました (slug=%s, tweet_id=%s)", slug, tweet_id)
            return True
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

    logger.warning("Xへの投稿が失敗しましたが、パイプライン処理は継続します (slug=%s)", slug)
    return False
