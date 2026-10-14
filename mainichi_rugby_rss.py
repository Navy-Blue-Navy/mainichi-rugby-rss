import hashlib
import os
import re
import time
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime, parsedate_to_datetime
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
import xml.etree.ElementTree as ET


SOURCE_URL = "https://mainichi.jp/highschool-rugby-for-all/"
OUTPUT_FILE = "mainichi_rugby.xml"

JST = timezone(timedelta(hours=9))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/154.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,image/avif,"
        "image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
}


def clean_text(text):
    return re.sub(r"\s+", " ", text).strip()


def canonicalize_url(url):
    parsed = urlparse(url)

    return (
        f"{parsed.scheme}://"
        f"{parsed.netloc}"
        f"{parsed.path}"
    )


def make_guid(link):
    return hashlib.sha256(
        link.encode("utf-8")
    ).hexdigest()


def fetch_page(url):
    for attempt in range(1, 4):
        try:
            print(f"取得 {attempt}/3: {url}")

            response = requests.get(
                url,
                headers=HEADERS,
                timeout=30,
            )

            print(f"HTTP: {response.status_code}")

            response.raise_for_status()

            response.encoding = (
                response.apparent_encoding
                or "utf-8"
            )

            return response.text

        except requests.RequestException as e:
            print(f"取得失敗: {e}")

            if attempt < 3:
                print("10秒待って再試行します。")
                time.sleep(10)

    return None


def parse_article_text(text):
    text = clean_text(text)

    match = re.search(
        r"\s+"
        r"(20\d{2})/"
        r"(\d{1,2})/"
        r"(\d{1,2})"
        r"\s+"
        r"(\d{1,2}):"
        r"(\d{2})"
        r"(?:\s|$)",
        text,
    )

    if not match:
        return None

    title = clean_text(
        text[:match.start()]
    )

    if not title:
        return None

    try:
        published = datetime(
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3)),
            int(match.group(4)),
            int(match.group(5)),
            0,
            tzinfo=JST,
        )

    except ValueError:
        return None

    return title, published


def is_article_url(url):
    parsed = urlparse(url)

    if parsed.netloc not in (
        "mainichi.jp",
        "www.mainichi.jp",
    ):
        return False

    return bool(
        re.fullmatch(
            r"/articles/"
            r"20\d{6}/"
            r"[A-Za-z0-9]+/"
            r"[A-Za-z0-9]+/"
            r"[A-Za-z0-9]+/"
            r"[A-Za-z0-9]+",
            parsed.path,
        )
    )


def load_existing_items():
    items = {}

    if not os.path.exists(OUTPUT_FILE):
        return items

    try:
        tree = ET.parse(OUTPUT_FILE)
        root = tree.getroot()

        for item in root.findall(
            "./channel/item"
        ):
            link = (
                item.findtext(
                    "link",
                    "",
                ).strip()
            )

            guid = (
                item.findtext(
                    "guid",
                    "",
                ).strip()
            )

            if not guid and link:
                guid = make_guid(link)

            if not guid:
                continue

            items[guid] = {
                "title": item.findtext(
                    "title",
                    "",
                ),
                "link": link,
                "description": item.findtext(
                    "description",
                    "",
                ),
                "pubDate": item.findtext(
                    "pubDate",
                    "",
                ),
                "guid": guid,
            }

    except Exception as e:
        print(f"既存RSS読込失敗: {e}")

    return items


def extract_articles(html):
    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    candidates = []

    for a in soup.find_all(
        "a",
        href=True,
    ):
        text = clean_text(
            a.get_text(
                " ",
                strip=True,
            )
        )

        parsed = parse_article_text(
            text
        )

        if parsed is None:
            continue

        link = canonicalize_url(
            urljoin(
                SOURCE_URL,
                a["href"],
            )
        )

        if not is_article_url(link):
            continue

        title, published = parsed

        candidates.append({
            "title": title,
            "link": link,
            "date": published,
        })

    # この特集ページでは、目的の記事群が
    # 「YYYY/M/D HH:MM」付き記事として連続して並ぶ。
    # unrelated な関連記事等が後ろにもあるため、
    # 日時付き記事の最初の連続ブロックを抽出する。
    #
    # 現在は冒頭にサイト共通記事が2件あるが、
    # それらには完全な YYYY/M/D HH:MM が付いていないため
    # candidates には入らない。
    #
    # 一方、目的記事群の後に表示される関連記事は
    # 「10/14 17:34」のように年が省略されているため、
    # これも candidates には入らない。

    articles = {}

    for article in candidates:
        link = article["link"]

        if link not in articles:
            articles[link] = article

    result = list(
        articles.values()
    )

    result.sort(
        key=lambda x: (
            x["date"],
            x["link"],
        ),
        reverse=True,
    )

    return result


def build_rss(
    existing_items,
    current_articles,
):
    merged = dict(existing_items)

    new_count = 0

    for article in current_articles:
        guid = make_guid(
            article["link"]
        )

        if guid not in merged:
            new_count += 1

        merged[guid] = {
            "title": article["title"],
            "link": article["link"],
            "description": (
                "毎日新聞「みんなの高校ラグビー」"
            ),
            "pubDate": format_datetime(
                article["date"]
            ),
            "guid": guid,
        }

    def get_pubdate(item):
        try:
            dt = parsedate_to_datetime(
                item["pubDate"]
            )

            if dt.tzinfo is None:
                dt = dt.replace(
                    tzinfo=timezone.utc
                )

            return dt

        except Exception:
            return datetime(
                1970,
                1,
                1,
                tzinfo=timezone.utc,
            )

    sorted_items = sorted(
        merged.values(),
        key=get_pubdate,
        reverse=True,
    )

    rss = ET.Element(
        "rss",
        version="2.0",
    )

    channel = ET.SubElement(
        rss,
        "channel",
    )

    ET.SubElement(
        channel,
        "title",
    ).text = (
        "毎日新聞 みんなの高校ラグビー"
    )

    ET.SubElement(
        channel,
        "link",
    ).text = SOURCE_URL

    ET.SubElement(
        channel,
        "description",
    ).text = (
        "毎日新聞「みんなの高校ラグビー」の新着記事"
    )

    ET.SubElement(
        channel,
        "language",
    ).text = "ja"

    ET.SubElement(
        channel,
        "lastBuildDate",
    ).text = format_datetime(
        datetime.now(JST)
    )

    for data in sorted_items:
        item = ET.SubElement(
            channel,
            "item",
        )

        ET.SubElement(
            item,
            "title",
        ).text = data["title"]

        ET.SubElement(
            item,
            "link",
        ).text = data["link"]

        ET.SubElement(
            item,
            "description",
        ).text = data[
            "description"
        ]

        ET.SubElement(
            item,
            "pubDate",
        ).text = data[
            "pubDate"
        ]

        guid_element = ET.SubElement(
            item,
            "guid",
        )

        guid_element.set(
            "isPermaLink",
            "false",
        )

        guid_element.text = data[
            "guid"
        ]

    tree = ET.ElementTree(rss)

    ET.indent(
        tree,
        space="  ",
    )

    tree.write(
        OUTPUT_FILE,
        encoding="utf-8",
        xml_declaration=True,
    )

    return (
        len(sorted_items),
        new_count,
    )


def main():
    existing_items = (
        load_existing_items()
    )

    print(
        f"既存RSS件数: "
        f"{len(existing_items)}"
    )

    html = fetch_page(
        SOURCE_URL
    )

    if html is None:
        if existing_items:
            print(
                "取得に失敗したため、"
                "既存XMLを維持します。"
            )
            return

        raise RuntimeError(
            "毎日新聞「みんなの高校ラグビー」の"
            "取得に失敗しました。"
        )

    print(
        f"HTML文字数: {len(html)}"
    )

    articles = extract_articles(
        html
    )

    print()
    print(
        f"記事取得件数: "
        f"{len(articles)}"
    )

    print()
    print(
        "=== 取得記事 ==="
    )

    for i, article in enumerate(
        articles,
        1,
    ):
        print()
        print(
            f"{i:3d}. "
            f"{article['date'].strftime('%Y-%m-%d %H:%M')} "
            f"{article['title']}"
        )

        print(
            f"     {article['link']}"
        )

    if len(articles) < 5:
        if existing_items:
            print()
            print(
                "記事取得件数が異常に少ないため、"
                "既存XMLを維持します。"
            )
            return

        raise RuntimeError(
            f"記事取得件数が少なすぎます: "
            f"{len(articles)}"
        )

    total_count, new_count = (
        build_rss(
            existing_items,
            articles,
        )
    )

    print()
    print(
        f"今回の新規記事: "
        f"{new_count}"
    )

    print(
        f"RSS保存件数: "
        f"{total_count}"
    )

    print(
        f"保存先: "
        f"{os.path.abspath(OUTPUT_FILE)}"
    )


if __name__ == "__main__":
    main()