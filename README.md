# basho-bot

Notionの抜書から選んだ引用を、37分ごとにXへ投稿するボットです。

投稿形式: `本文 by 著者名 #PR Amazonリンク`

## 仕組み
- GitHub Actions が10分ごとに `bot.py` を起動します。
- Notionの投稿キューで、前回の投稿から37分以上たっていれば、「投稿OK」で未投稿の引用を1件投稿します。
- 全部投稿し終わったら、いちばん前に投稿したものから順に再投稿します。

## 必要な Secrets（Settings → Secrets and variables → Actions）
| 名前 | 中身 |
|---|---|
| NOTION_TOKEN | Notionインテグレーション「抜書bot」のトークン |
| NOTION_QUEUE_DB_ID | 投稿キューのデータベースID |
| X_API_KEY / X_API_SECRET | Xのコンシューマーキー / シークレット |
| X_ACCESS_TOKEN / X_ACCESS_TOKEN_SECRET | Xのアクセストークン / シークレット |
| AMAZON_TAG | AmazonアソシエイトのトラッキングID（〜-22） |

## 手動実行
Actions → post → Run workflow。`dry_run` にチェックすると、投稿せずに投稿文だけを確認できます。
