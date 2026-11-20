"""投稿キューの「ISBN」が空の行に、国立国会図書館サーチでISBNを調べて書き込む。

必要な環境変数: NOTION_TOKEN, NOTION_QUEUE_DB_ID
任意: ONLY_OK=1  「投稿OK」の行だけ処理する
"""

from __future__ import annotations

import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

import bot

NS = {"dc": "http://purl.org/dc/elements/1.1/",
      "dcterms": "http://purl.org/dc/terms/",
      "xsi": "http://www.w3.org/2001/XMLSchema-instance"}
XSI_TYPE = "{http://www.w3.org/2001/XMLSchema-instance}type"
NDL_URL = "https://ndlsearch.ndl.go.jp/api/opensearch"


def clean_title(title: str) -> str:
    title = re.sub(r"（要確認）|\(要確認\)", "", title)
    title = re.sub(r"[（(][^）)]*(文庫|新書|選書)[^）)]*[）)]", "", title)
    return title.strip()


def surname(author: str) -> str:
    author = re.sub(r"（[^）]*）|\([^)]*\)|（要確認）", "", author)
    first = re.split(r"[、,，/／]", author)[0].strip()
    parts = re.split(r"[・\s=＝]+", first)
    return parts[-1] if parts and parts[-1] else first


def ndl_search(title: str, creator: str = "") -> list[tuple[str, str]]:
    params = {"title": title, "cnt": "20", "mediatype": "books"}
    if creator:
        params["creator"] = creator
    url = f"{NDL_URL}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "basho-bot (personal reading notes)"})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                root = ET.fromstring(resp.read())
            break
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == 4:
                raise
            time.sleep(15 * (attempt + 1))  # 混雑時は待って再試行
    found = []
    for item in root.iter("item"):
        issued = item.findtext("dcterms:issued", default="", namespaces=NS)
        for ident in item.findall("dc:identifier", NS):
            if ident.get(XSI_TYPE) == "dcndl:ISBN" and ident.text:
                isbn = ident.text.replace("-", "").strip()
                if isbn.startswith("9784") or (len(isbn) == 10 and isbn.startswith("4")):
                    found.append((issued, isbn))
    return found


def best_isbn(title: str, author: str) -> str | None:
    t = clean_title(title)
    if not t:
        return None
    attempts = [(t, surname(author))] if author else []
    short = re.split(r"[\s　:：]", t)[0]
    if short != t:
        attempts.append((short, surname(author) if author else ""))
    attempts.append((t, ""))
    for title_q, creator_q in attempts:
        try:
            hits = ndl_search(title_q, creator_q)
        except Exception as e:  # noqa: BLE001
            print(f"  NDL検索エラー: {e}")
            hits = []
        time.sleep(3)
        if hits:
            hits.sort(reverse=True)  # 新しい版を優先
            return hits[0][1]
    return None


def all_rows(only_ok: bool) -> list[dict]:
    db = bot.env("NOTION_QUEUE_DB_ID")
    flt: dict = {"property": "ISBN", "rich_text": {"is_empty": True}}
    if only_ok:
        flt = {"and": [flt, {"property": bot.P_OK, "checkbox": {"equals": True}}]}
    rows, cursor = [], None
    while True:
        body = {"filter": flt, "page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        res = bot.http_json("POST", f"https://api.notion.com/v1/databases/{db}/query",
                            bot.notion_headers(), body)
        rows += res["results"]
        if not res.get("has_more"):
            return rows
        cursor = res["next_cursor"]


def main() -> None:
    rows = all_rows(os.environ.get("ONLY_OK") == "1")
    print(f"ISBNが空の行: {len(rows)}件")
    cache: dict[tuple[str, str], str | None] = {}
    filled = 0
    for row in rows:
        p = row["properties"]
        title, author = bot.plain(p[bot.P_TITLE]), bot.plain(p[bot.P_AUTHOR])
        key = (title, author)
        if key not in cache:
            cache[key] = best_isbn(title, author)
        isbn = cache[key]
        print(f"{'○' if isbn else '×'} {title} / {author} -> {isbn or '見つからず'}")
        if isbn:
            bot.http_json("PATCH", f"https://api.notion.com/v1/pages/{row['id']}", bot.notion_headers(),
                          {"properties": {"ISBN": {"rich_text": [{"text": {"content": isbn}}]}}})
            filled += 1
            time.sleep(0.35)
    print(f"書き込み: {filled}件 / 対象 {len(rows)}件（{len(cache)}冊）")


if __name__ == "__main__":
    sys.exit(main())
