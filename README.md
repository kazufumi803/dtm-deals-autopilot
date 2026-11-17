# DTM Plugin & Synth Deals Auto-Publisher

海外のDTMプラグイン・ソフト音源の「セール情報」および「期間限定無料配布（Freebie）情報」をRSSから自動収集し、**Gemini API (`gemini-2.5-flash`)** で日本語の解説記事（Markdown）とX（旧Twitter）の告知文を生成、**Astro静的サイトの更新**と**Xへの自動投稿**までをGitHub Actionsで完全自動化するシステムです。

---

## ディレクトリ構成

```text
├── .github/
│   └── workflows/
│       └── auto_publish.yml      # 定期実行（JST 07:00 / 19:00）＆手動実行ワークフロー
├── data/
│   ├── posted_ids.json           # 処理済み記事IDリスト（最新500件を自動保持）
│   └── pending_tweets.json       # デプロイ待機後のX投稿キュー
├── scripts/
│   ├── requirements.txt          # Python依存パッケージ定義
│   ├── fetcher.py                # Reddit / KVR Audio RSS取得＆未処理抽出（最大2件/回）
│   ├── affiliate.py              # Plugin Boutique / Amazon アフィリエイトタグ自動付与
│   ├── generator.py              # Gemini 2.5 Flash Structured Outputs による記事＆SNS文生成
│   ├── publisher.py              # Tweepy (X API v2) 自動投稿＆例外保護
│   ├── main.py                   # 統合パイプラインスクリプト
│   └── test_pipeline.py          # 単体・結合テストスイート
└── site/                         # Astro + Tailwind CSS 静的ブログサイト（ダークモードUI）
    ├── astro.config.mjs
    ├── tailwind.config.mjs
    ├── package.json
    └── src/
        ├── content/
        │   ├── config.ts         # Deals Content Collection スキーマ定義
        │   └── deals/            # 自動生成されたMarkdown記事格納ディレクトリ
        ├── layouts/
        │   └── BaseLayout.astro
        └── pages/
            ├── index.astro
            └── deals/
                └── [...slug].astro
```

---

## 必要な環境変数（GitHub Actions Secrets）

GitHubリポジトリの `Settings > Secrets and variables > actions` に以下のシークレットを設定してください。

| 変数名 | 必須 | 説明 |
| :--- | :---: | :--- |
| `GEMINI_API_KEY` | ✅ | Google AI Studio で発行した Gemini API キー |
| `SITE_URL` | ✅ | 公開サイトのベースURL（例: `https://deals.example.com`） |
| `AFFILIATE_TAG_PB` | 任意 | Plugin Boutique 用アフィリエイトID（未設定時は元URLをそのまま使用） |
| `AFFILIATE_TAG_AMAZON` | 任意 | Amazonアソシエイトタグ（未設定時は元URLをそのまま使用） |
| `X_API_KEY` | 任意 | X API Consumer Key |
| `X_API_SECRET` | 任意 | X API Consumer Secret |
| `X_ACCESS_TOKEN` | 任意 | X API Access Token |
| `X_ACCESS_TOKEN_SECRET` | 任意 | X API Access Token Secret |

---

## ローカルでのテスト・ビルド手順

### 1. Pythonパイプラインのテスト実行

```bash
pip install -r scripts/requirements.txt
python -m unittest discover -s scripts -p "test_*.py" -v
```

### 2. パイプラインの手動実行

```bash
# 通常実行（未処理RSSから最新2件を取得・記事生成・投稿）
python scripts/main.py

# サイトデプロイ待ちキューに保存する場合
python scripts/main.py --defer-x

# キューに保存された未送信ポストをXへ送信する場合
python scripts/main.py --post-pending
```

### 3. Astroサイトのローカルビルド＆プレビュー

```bash
cd site
npm install
npm run build
npm run dev
```
