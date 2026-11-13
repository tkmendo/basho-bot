"""抜書bot: Notionの投稿キューから1件選び、Xに投稿する。

投稿形式: 「本文 by 著者名 #PR Amazonリンク」

必要な環境変数:
  NOTION_TOKEN, NOTION_QUEUE_DB_ID,
  X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_TOKEN_SECRET,
  AMAZON_TAG
任意:
  DRY_RUN=1          投稿せずに投稿文を表示するだけ
  FORCE=1            間隔チェックを無視して投稿する
  INTERVAL_MINUTES   投稿間隔（既定 37）
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

NOTION_VERSION = "2022-06-28"
X_POST_URL = "https://api.x.com/2/tweets"
MAX_WEIGHT = 280  # X の上限（日本語1文字=2、URL=23）
URL_WEIGHT = 23

# Notion 投稿キューのプロパティ名
P_QUOTE = "引用"
P_TITLE = "書名"
P_AUTHOR = "著者"
P_OK = "投稿OK"
P_POSTED_AT = "投稿日時"
P_POST_URL = "ポストURL"


def env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None or value == "":
        sys.exit(f"環境変数 {name} が設定されていません")
    return value


def http_json(method: str, url: str, headers: dict, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        sys.exit(f"{method} {url} が失敗しました: {e.code} {detail}")


# ---------------- Notion ----------------

def notion_headers() -> dict:
    return {
        "Authorization": f"Bearer {env('NOTION_TOKEN')}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def notion_query(filter_: dict, sorts: list, page_size: int = 1) -> list:
    db = env("NOTION_QUEUE_DB_ID")
    body = {"filter": filter_, "sorts": sorts, "page_size": page_size}
    res = http_json("POST", f"https://api.notion.com/v1/databases/{db}/query",
                    notion_headers(), body)
    return res.get("results", [])


def plain(prop: dict) -> str:
    items = prop.get("title") or prop.get("rich_text") or []
    return "".join(i.get("plain_text", "") for i in items).strip()


def last_posted_at() -> datetime | None:
    rows = notion_query({"property": P_POSTED_AT, "date": {"is_not_empty": True}},
                        [{"property": P_POSTED_AT, "direction": "descending"}])
    if not rows:
        return None
    start = rows[0]["properties"][P_POSTED_AT]["date"]["start"]
    return datetime.fromisoformat(start.replace("Z", "+00:00"))


def pick_next() -> dict | None:
    ok = {"property": P_OK, "checkbox": {"equals": True}}
    # 1) 未投稿のものを古い順に
    rows = notion_query(
        {"and": [ok, {"property": P_POSTED_AT, "date": {"is_empty": True}}]},
        [{"timestamp": "created_time", "direction": "ascending"}])
    if rows:
        return rows[0]
    # 2) 全部投稿済みなら、いちばん前に投稿したものを再投稿
    rows = notion_query(ok, [{"property": P_POSTED_AT, "direction": "ascending"}])
    return rows[0] if rows else None


def mark_posted(page_id: str, when: datetime, post_url: str) -> None:
    body = {"properties": {
        P_POSTED_AT: {"date": {"start": when.isoformat()}},
        P_POST_URL: {"url": post_url},
    }}
    http_json("PATCH", f"https://api.notion.com/v1/pages/{page_id}",
              notion_headers(), body)


# ---------------- 投稿文 ----------------

def char_weight(ch: str) -> int:
    # X の twitter-text 準拠の近似: Latin 系の範囲は 1、それ以外は 2
    cp = ord(ch)
    if (0 <= cp <= 4351 or 8192 <= cp <= 8205 or 8208 <= cp <= 8223
            or 8242 <= cp <= 8247):
        return 1
    return 2


def weight(text: str) -> int:
    return sum(char_weight(c) for c in unicodedata.normalize("NFC", text))


def amazon_link(title: str, author: str) -> str:
    query = " ".join(s for s in (title, author) if s)
    params = urllib.parse.urlencode({"k": query, "i": "stripbooks",
                                     "tag": env("AMAZON_TAG")})
    return f"https://www.amazon.co.jp/s?{params}"


def build_text(quote: str, author: str, link: str) -> str:
    suffix = f" by {author} #PR " if author else " #PR "
    budget = MAX_WEIGHT - weight(suffix) - URL_WEIGHT
    if weight(quote) > budget:
        out, used = "", weight("…")
        for c in quote:
            if used + char_weight(c) > budget:
                break
            out += c
            used += char_weight(c)
        quote = out.rstrip() + "…"
    return f"{quote}{suffix}{link}"


# ---------------- X (OAuth 1.0a) ----------------

def _pct(s: str) -> str:
    return urllib.parse.quote(s, safe="~")


def oauth1_header(method: str, url: str) -> str:
    oauth = {
        "oauth_consumer_key": env("X_API_KEY"),
        "oauth_nonce": secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_token": env("X_ACCESS_TOKEN"),
        "oauth_version": "1.0",
    }
    # JSON ボディは署名に含めない
    param_str = "&".join(f"{_pct(k)}={_pct(v)}" for k, v in sorted(oauth.items()))
    base = "&".join([method.upper(), _pct(url), _pct(param_str)])
    key = f"{_pct(env('X_API_SECRET'))}&{_pct(env('X_ACCESS_TOKEN_SECRET'))}"
    sig = base64.b64encode(hmac.new(key.encode(), base.encode(), hashlib.sha1).digest())
    oauth["oauth_signature"] = sig.decode()
    return "OAuth " + ", ".join(f'{_pct(k)}="{_pct(v)}"' for k, v in sorted(oauth.items()))


def post_to_x(text: str) -> str:
    headers = {"Authorization": oauth1_header("POST", X_POST_URL),
               "Content-Type": "application/json"}
    res = http_json("POST", X_POST_URL, headers, {"text": text})
    return res["data"]["id"]


# ---------------- main ----------------

def main() -> None:
    dry_run = os.environ.get("DRY_RUN") == "1"
    force = os.environ.get("FORCE") == "1"
    interval = timedelta(minutes=int(os.environ.get("INTERVAL_MINUTES", "37")))
    now = datetime.now(timezone.utc)

    if not force:
        last = last_posted_at()
        if last and now - last < interval:
            wait = interval - (now - last)
            print(f"前回の投稿から{interval}たっていません（あと約{int(wait.total_seconds() // 60)}分）。")
            return

    row = pick_next()
    if row is None:
        print("投稿OKの引用がありません。")
        return

    props = row["properties"]
    quote, title, author = plain(props[P_QUOTE]), plain(props[P_TITLE]), plain(props[P_AUTHOR])
    link = amazon_link(title, author)
    text = build_text(quote, author, link)
    print(f"--- 投稿文（{weight(text[:-len(link)]) + URL_WEIGHT}/{MAX_WEIGHT}） ---")
    print(text)

    if dry_run:
        print("DRY_RUN のため投稿しません。")
        return

    tweet_id = post_to_x(text)
    url = f"https://x.com/i/web/status/{tweet_id}"
    mark_posted(row["id"], now, url)
    print(f"投稿しました: {url}")


if __name__ == "__main__":
    main()
