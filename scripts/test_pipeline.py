import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import affiliate  # noqa: E402
import fetcher  # noqa: E402
import generator  # noqa: E402
import gsc_setup  # noqa: E402
import main as pipeline_main  # noqa: E402
import publisher  # noqa: E402


SAMPLE_RSS_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Sample Audio Deals</title>
    <item>
      <title>[Plugin Boutique] SynthMaster 3 Sale - 70% Off ($29)</title>
      <link>https://www.reddit.com/r/AudioProductionDeals/comments/abc111/synthmaster_3/</link>
      <guid>guid-111</guid>
      <pubDate>Tue, 17 Nov 2026 08:00:00 GMT</pubDate>
      <description><![CDATA[<p>Awesome synth deal! <a href="https://www.pluginboutique.com/product/1-Instruments/4-Synth/999-SynthMaster">[link]</a></p>]]></description>
    </item>
    <item>
      <title>[KVR] Free Vintage EQ Plugin by AnalogLab</title>
      <link>https://www.kvraudio.com/news/free-vintage-eq-60001</link>
      <guid>guid-222</guid>
      <pubDate>Tue, 17 Nov 2026 09:00:00 GMT</pubDate>
      <description><![CDATA[<p>Get this warm passive EQ for free until Nov 30.</p>]]></description>
    </item>
    <item>
      <title>[KVR] Older Deal 50% Off Compressor</title>
      <link>https://www.kvraudio.com/news/older-deal-60000</link>
      <guid>guid-333</guid>
      <pubDate>Tue, 17 Nov 2026 06:00:00 GMT</pubDate>
      <description><![CDATA[<p>Classic bus compressor deal.</p>]]></description>
    </item>
  </channel>
</rss>
"""


class TestDTMDealsPipeline(unittest.TestCase):
    def test_affiliate_conversion_with_and_without_env(self) -> None:
        pb_url = "https://www.pluginboutique.com/product/1-Instruments?existing=1"
        amz_url = "https://www.amazon.co.jp/dp/B099999999"
        other_url = "https://xferrecords.com/products/serum"

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AFFILIATE_TAG_PB", None)
            os.environ.pop("AFFILIATE_TAG_AMAZON", None)
            self.assertEqual(affiliate.convert_affiliate_url(pb_url), pb_url)
            self.assertEqual(affiliate.convert_affiliate_url(amz_url), amz_url)

        with patch.dict(
            os.environ,
            {"AFFILIATE_TAG_PB": "pb_partner_123", "AFFILIATE_TAG_AMAZON": "myamz-22"},
            clear=False,
        ):
            converted_pb = affiliate.convert_affiliate_url(pb_url)
            self.assertIn("existing=1", converted_pb)
            self.assertIn("a_aid=pb_partner_123", converted_pb)

            converted_amz = affiliate.convert_affiliate_url(amz_url)
            self.assertIn("tag=myamz-22", converted_amz)

            # 非対象ドメインはそのまま
            self.assertEqual(affiliate.convert_affiliate_url(other_url), other_url)

    def test_fetcher_deduplication_and_max_2_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            posted_path = Path(tmpdir) / "posted_ids.json"
            posted_path.write_text("[]", encoding="utf-8")

            test_feeds = [{"name": "TestFeed", "url": "https://example.com/rss"}]
            with patch.object(fetcher, "_fetch_rss_content", return_value=SAMPLE_RSS_XML):
                entries = fetcher.fetch_unprocessed_entries(
                    posted_ids_path=posted_path,
                    max_items=2,
                    feeds=test_feeds,
                )
                # 3件中、新しい順の2件のみ返ることを確認
                self.assertEqual(len(entries), 2)
                self.assertIn("Free Vintage EQ", entries[0]["title"])
                self.assertIn("SynthMaster 3", entries[1]["title"])
                # Redditスレッド内の外部リンク(Plugin Boutique)が抽出されていることを確認
                self.assertTrue(
                    entries[1]["external_url"].startswith("https://www.pluginboutique.com/")
                )

                # 1件目を処理済みに登録して再実行すると、残りの2件が返ることを確認
                fetcher.save_posted_ids([entries[0]["id"]], posted_ids_path=posted_path)
                entries_second = fetcher.fetch_unprocessed_entries(
                    posted_ids_path=posted_path,
                    max_items=2,
                    feeds=test_feeds,
                )
                self.assertEqual(len(entries_second), 2)
                self.assertNotIn(entries[0]["id"], [e["id"] for e in entries_second])

    def test_posted_ids_capped_at_500(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            posted_path = Path(tmpdir) / "posted_ids.json"
            ids = [f"id-{i}" for i in range(600)]
            fetcher.save_posted_ids(ids, posted_ids_path=posted_path, max_keep=500)
            loaded = fetcher.load_posted_ids(posted_path)
            self.assertEqual(len(loaded), 500)
            self.assertEqual(loaded[0], "id-100")
            self.assertEqual(loaded[-1], "id-599")

    def test_publisher_resilience_on_error(self) -> None:
        # 環境変数がある状態で Tweepy が例外を投げてもクラッシュせず False を返すことを確認
        with patch.dict(
            os.environ,
            {
                "X_API_KEY": "k",
                "X_API_SECRET": "s",
                "X_ACCESS_TOKEN": "t",
                "X_ACCESS_TOKEN_SECRET": "ts",
                "SITE_URL": "https://dtm-deals.example.com",
            },
            clear=False,
        ):
            with patch("publisher.tweepy.Client") as mock_client_cls:
                mock_instance = MagicMock()
                mock_instance.create_tweet.side_effect = RuntimeError("X API Rate Limit Exceeded")
                mock_client_cls.return_value = mock_instance

                result = publisher.post_to_x(
                    tweet_text="【無料】ヴィンテージEQ配布中！ #DTM #プラグインセール",
                    slug="free-vintage-eq",
                    max_retries=1,
                    backoff_sec=0.01,
                )
                self.assertFalse(result)

    def test_publisher_webhook_fallback_when_x_fails(self) -> None:
        with patch.dict(
            os.environ,
            {
                "X_API_KEY": "k",
                "X_API_SECRET": "s",
                "X_ACCESS_TOKEN": "t",
                "X_ACCESS_TOKEN_SECRET": "ts",
                "SITE_URL": "https://dtm-deals-autopilot.pages.dev",
                "WEBHOOK_URL": "https://hooks.example.com/webhook/123",
            },
            clear=False,
        ):
            with patch("publisher.tweepy.Client") as mock_client_cls:
                mock_instance = MagicMock()
                mock_instance.create_tweet.side_effect = RuntimeError("401 Unauthorized")
                mock_client_cls.return_value = mock_instance

                with patch("publisher.requests.post") as mock_post:
                    mock_resp = MagicMock()
                    mock_resp.status_code = 200
                    mock_post.return_value = mock_resp

                    result = publisher.post_to_x(
                        tweet_text="【無料】ヴィンテージEQ配布中！ #DTM #プラグインセール",
                        slug="free-vintage-eq",
                        max_retries=1,
                        backoff_sec=0.01,
                    )
                    self.assertTrue(result)
                    mock_post.assert_called_once()
                    _, kwargs = mock_post.call_args
                    self.assertIn("https://dtm-deals-autopilot.pages.dev/deals/free-vintage-eq", kwargs["json"]["content"])

    def test_pending_tweets_cleaned_up_on_401_without_warnings(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pending_path = Path(tmpdir) / "pending_tweets.json"
            pending_path.write_text(
                json.dumps([{"slug": "test-deal", "tweet_text": "テスト #DTM"}]),
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {
                    "X_API_KEY": "k",
                    "X_API_SECRET": "s",
                    "X_ACCESS_TOKEN": "t",
                    "X_ACCESS_TOKEN_SECRET": "ts",
                },
                clear=False,
            ):
                os.environ.pop("WEBHOOK_URL", None)
                with patch("publisher.tweepy.Client") as mock_client_cls:
                    mock_instance = MagicMock()
                    mock_instance.create_tweet.side_effect = RuntimeError("401 Unauthorized")
                    mock_client_cls.return_value = mock_instance

                    pipeline_main.flush_pending_tweets(pending_path=pending_path, delay_between_sec=0)
                    cleaned = json.loads(pending_path.read_text(encoding="utf-8"))
                    self.assertEqual(cleaned, [])

    def test_retroactive_affiliate_links_on_existing_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            deals_dir = Path(tmpdir)
            md_file = deals_dir / "existing-deal.md"
            md_file.write_text(
                '---\n'
                'title: "【セール】テストシンセ"\n'
                'pubDate: "2026-11-18T00:00:00Z"\n'
                'category: "Sale"\n'
                'affiliateUrl: "https://www.pluginboutique.com/products/1234"\n'
                '---\n\n'
                '詳細はこちら: https://www.amazon.co.jp/dp/B012345678\n',
                encoding="utf-8",
            )

            with patch.dict(
                os.environ,
                {"AFFILIATE_TAG_PB": "retro_pb_99", "AFFILIATE_TAG_AMAZON": "retroamz-22"},
                clear=False,
            ):
                updated_count = affiliate.retroactively_apply_affiliate_links(deals_dir=deals_dir)
                self.assertEqual(updated_count, 1)

                updated_text = md_file.read_text(encoding="utf-8")
                self.assertIn(
                    'affiliateUrl: "https://www.pluginboutique.com/products/1234?a_aid=retro_pb_99"',
                    updated_text,
                )
                self.assertIn("https://www.amazon.co.jp/dp/B012345678?tag=retroamz-22", updated_text)

                # 2回目の実行では変更なし（冪等性）を確認
                second_count = affiliate.retroactively_apply_affiliate_links(deals_dir=deals_dir)
                self.assertEqual(second_count, 0)

    def test_gsc_token_extraction_and_injection(self) -> None:
        raw_meta = '<meta name="google-site-verification" content="abc123XYZ_token" />'
        self.assertEqual(gsc_setup.extract_token(raw_meta), "abc123XYZ_token")
        self.assertEqual(gsc_setup.extract_token("plain_token_999"), "plain_token_999")

        with tempfile.TemporaryDirectory() as tmpdir:
            dummy_layout = Path(tmpdir) / "BaseLayout.astro"
            dummy_layout.write_text(
                "const rawVerification = (\n"
                "  process.env.GOOGLE_SITE_VERIFICATION ||\n"
                "  ''\n"
                ").trim();\n",
                encoding="utf-8",
            )
            changed = gsc_setup.inject_token_into_base_layout(raw_meta, layout_path=dummy_layout)
            self.assertTrue(changed)
            self.assertIn("'abc123XYZ_token'", dummy_layout.read_text(encoding="utf-8"))

    def test_end_to_end_pipeline_creates_markdown_and_updates_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            posted_path = tmp_path / "posted_ids.json"
            pending_path = tmp_path / "pending_tweets.json"
            deals_dir = tmp_path / "deals"

            posted_path.write_text("[]", encoding="utf-8")
            pending_path.write_text("[]", encoding="utf-8")

            fake_entries = [
                {
                    "id": "entryhash001",
                    "source": "Reddit r/AudioProductionDeals",
                    "title": "[Plugin Boutique] SynthMaster 3 - 70% Off",
                    "link": "https://www.reddit.com/r/AudioProductionDeals/comments/abc111/",
                    "external_url": "https://www.pluginboutique.com/product/1-Instruments/4-Synth/999-SynthMaster",
                    "summary_html": "",
                    "summary_text": "70% off SynthMaster 3 until Nov 30.",
                    "published_ts": 1794902400.0,
                    "pub_date": "2026-11-17T08:00:00+00:00",
                }
            ]

            fake_generated = {
                "title": "【70%OFF】万能ハイブリッドシンセ「SynthMaster 3」が特別セール！",
                "slug": "synthmaster-3-70-off",
                "category": "Sale",
                "summary_md": (
                    "## 1. 製品の概要と特徴\nテスト概要\n\n"
                    "## 2. おすすめの音楽ジャンルと活用シーン\nFuture Bassに最適\n\n"
                    "## 3. セール期間・割引率・注意事項のまとめ\n- 70% OFF"
                ),
                "tweet_text": "【70%OFF】SynthMaster 3が特価セール中！Future BassやEDMのリードに最適 #DTM #プラグインセール",
            }

            with patch.dict(os.environ, {"AFFILIATE_TAG_PB": "pbtag99"}, clear=False):
                with patch.object(pipeline_main, "fetch_unprocessed_entries", return_value=fake_entries):
                    with patch.object(pipeline_main, "generate_deal_content", return_value=fake_generated):
                        count = pipeline_main.run_pipeline(
                            posted_ids_path=posted_path,
                            deals_dir=deals_dir,
                            pending_tweets_path=pending_path,
                            max_items=2,
                            defer_x_post=True,
                        )
                        self.assertEqual(count, 1)

                        created_md = deals_dir / "synthmaster-3-70-off.md"
                        self.assertTrue(created_md.exists())
                        content = created_md.read_text(encoding="utf-8")
                        self.assertIn('title: "【70%OFF】万能ハイブリッドシンセ「SynthMaster 3」が特別セール！"', content)
                        self.assertIn('category: "Sale"', content)
                        self.assertIn("a_aid=pbtag99", content)

                        saved_ids = json.loads(posted_path.read_text(encoding="utf-8"))
                        self.assertEqual(saved_ids, ["entryhash001"])

                        queued = json.loads(pending_path.read_text(encoding="utf-8"))
                        self.assertEqual(len(queued), 1)
                        self.assertEqual(queued[0]["slug"], "synthmaster-3-70-off")


if __name__ == "__main__":
    unittest.main()
