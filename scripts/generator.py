import json
import logging
import os
import time
from typing import Literal

from google import genai
from google.genai import types
from pydantic import BaseModel, Field
from slugify import slugify

logger = logging.getLogger(__name__)

MODEL_NAME = "gemini-2.5-flash"


class DealArticleOutput(BaseModel):
    title: str = Field(
        description="日本語の魅力的な記事タイトル（「【無料】」「【80%OFF】」などのプレフィックス付き）"
    )
    slug: str = Field(
        description="URL用の英数字ハイフン区切りスラグ（例: serum-synth-sale-2026）"
    )
    category: Literal["Free", "Sale"] = Field(
        description="無料配布の場合は 'Free'、割引セールの場合は 'Sale'"
    )
    summary_md: str = Field(
        description=(
            "Astroサイトに掲載する日本語Markdown本文。以下の3セクション構成：\n"
            "1. 製品の概要と特徴（どんな音が出るか、どんなエフェクトか）\n"
            "2. おすすめの音楽ジャンルや活用シーン（例：Future Bassのリード、ボーカル処理、Lo-Fiビートなど具体的に）\n"
            "3. セール期間・割引率・注意事項のまとめ"
        )
    )
    tweet_text: str = Field(
        description=(
            "X投稿用の110文字以内の日本語テキスト。"
            "ハッシュタグ #DTM #プラグインセール 等を含め、末尾にサイトURLを結合する余白を残すこと。"
        )
    )


SYSTEM_INSTRUCTION = """あなたはプロの音楽プロデューサー兼DTM専門メディアの編集長です。
海外のDTMプラグイン・ソフト音源のセール情報や無料配布（Freebie）情報の英語タイトル・要約を受け取り、
日本のDTMer・作曲家にとって魅力的で実用的な日本語解説記事（Markdown）とX（旧Twitter）告知文を作成してください。

以下のルールを厳守してください：
1. `title`: 「【無料】」「【期間限定無料】」「【80%OFF】」「【最安タイ】」など、一目で価値が伝わるプレフィックスを必ず付けること。
2. `slug`: 製品名やブランド名を含む、URLに適した半角英数字とハイフンのみのスラグにすること。
3. `category`: 無料配布・フリープラグインなら `"Free"`、割引・バンドルセールなら `"Sale"` のいずれか。
4. `summary_md`: 以下の見出し（`## `）を含むMarkdown形式で詳しく丁寧に執筆すること（タイトルのH1はFrontmatterで表示するため本文には含めないこと）：
   - `## 1. 製品の概要と特徴`
     どのようなサウンドキャラクターか、シンセ/エフェクトの動作原理や強みを専門的かつ分かりやすく解説。
   - `## 2. おすすめの音楽ジャンルと活用シーン`
     Future Bassのリード、Lo-Fi HipHopのドラムバス処理、ボーカルのミックス、シネマティック劇伴など、具体的なトラックメイクでの使い所を提案。
   - `## 3. セール期間・割引率・注意事項のまとめ`
     元記事から読み取れる価格・割引率・終了日・対象環境（iLokの有無や対応フォーマット等、不明な場合は公式ページでの確認を促す旨）を整理した箇条書き。
5. `tweet_text`: **必ず110文字以内**に収めること。ハッシュタグ（`#DTM` `#プラグインセール` または `#無料プラグイン`）を含め、最後に記事URLを自動付与するための文字数余白を残すこと。自体にはURLを含めないこと。
"""


def _normalize_output(data: DealArticleOutput, fallback_title: str) -> dict[str, str]:
    """生成された構造化データを正規化・安全化する。"""
    clean_slug = slugify(data.slug or fallback_title, max_length=64, word_boundary=True)
    if not clean_slug:
        clean_slug = f"deal-{int(time.time())}"

    category = data.category if data.category in ("Free", "Sale") else "Sale"

    tweet = (data.tweet_text or "").strip()
    if "#DTM" not in tweet:
        tag_suffix = " #DTM #無料プラグイン" if category == "Free" else " #DTM #プラグインセール"
        if len(tweet) + len(tag_suffix) <= 110:
            tweet = f"{tweet}{tag_suffix}"
    if len(tweet) > 110:
        # 110文字以内に収める
        tweet = tweet[:109].rstrip() + "…"

    return {
        "title": data.title.strip(),
        "slug": clean_slug,
        "category": category,
        "summary_md": data.summary_md.strip(),
        "tweet_text": tweet,
    }


def generate_deal_content(
    english_title: str,
    english_summary: str,
    source_name: str = "",
    max_retries: int = 3,
    backoff_sec: float = 3.0,
) -> dict[str, str] | None:
    """
    Gemini API (`gemini-2.5-flash`) の Structured Outputs を用いて
    英語タイトル・要約から日本語記事＆X投稿文を生成する。
    エラー時はリトライし、それでも失敗した場合は None を返す。
    """
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        logger.error("環境変数 GEMINI_API_KEY が設定されていません。")
        return None

    client = genai.Client(api_key=api_key)

    user_prompt = (
        f"情報ソース: {source_name or 'Audio Deals RSS'}\n"
        f"英語タイトル: {english_title}\n"
        f"英語要約・詳細テキスト:\n{english_summary or '(要約なし・タイトルから推測して解説を作成してください)'}\n"
    )

    for attempt in range(1, max_retries + 1):
        try:
            response = client.models.generate_content(
                model=MODEL_NAME,
                contents=user_prompt,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_INSTRUCTION,
                    response_mime_type="application/json",
                    response_schema=DealArticleOutput,
                    temperature=0.4,
                ),
            )

            parsed: DealArticleOutput | None = getattr(response, "parsed", None)
            if parsed is None and response.text:
                raw_dict = json.loads(response.text)
                parsed = DealArticleOutput.model_validate(raw_dict)

            if parsed is None:
                raise ValueError("Gemini API から有効なJSONレスポンスが得られませんでした。")

            return _normalize_output(parsed, fallback_title=english_title)

        except Exception as exc:
            logger.warning(
                "Gemini API 生成失敗 (%s) [試行 %d/%d]: %s",
                english_title[:50],
                attempt,
                max_retries,
                exc,
            )
            if attempt < max_retries:
                time.sleep(backoff_sec * (2 ** (attempt - 1)))

    logger.error("Gemini API での記事生成をスキップします: %s", english_title)
    return None
