import json
import urllib.request
from datetime import datetime
from google_play_scraper import Sort, reviews

# 7대 홈쇼핑사 스토어 식별자 설정
APPS = [
    {"id": 1, "name": "GS홈쇼핑", "androidPkg": "gsshop.mobile.v2", "iosId": "417408265"},
    {"id": 2, "name": "CJ오쇼핑", "androidPkg": "com.cjoshoping.cjmall", "iosId": "426243952"},
    {"id": 3, "name": "현대홈쇼핑", "androidPkg": "com.hmallapp", "iosId": "446257206"},
    {"id": 4, "name": "롯데홈쇼핑", "androidPkg": "com.lotte.lotteimall", "iosId": "438067397"},
    {"id": 5, "name": "홈앤쇼핑", "androidPkg": "com.hnsmall", "iosId": "482441403"},
    {"id": 6, "name": "NS홈쇼핑", "androidPkg": "com.nsmall", "iosId": "491234510"},
    {"id": 7, "name": "공영홈쇼핑", "androidPkg": "kr.co.gongyoung.shop", "iosId": "1021234987"},
]


def fetch_android_reviews(app, count=30):
    """Google Play Store에서 최신 한국어 리뷰를 수집합니다."""
    collected = []
    try:
        result, _ = reviews(
            app["androidPkg"],
            lang="ko",
            country="kr",
            sort=Sort.NEWEST,
            count=count,
        )
        for idx, item in enumerate(result):
            dt = item.get("at")
            date_str = dt.strftime("%Y-%m-%d") if dt else datetime.now().strftime("%Y-%m-%d")
            collected.append({
                "id": int(f"{app['id']}1{idx:03d}"),
                "appId": app["id"],
                "os": "Android",
                "rating": int(item.get("score", 5)),
                "author": item.get("userName") or "Google Play 사용자",
                "date": date_str,
                "version": item.get("reviewCreatedVersion") or "-",
                "content": item.get("content") or "",
                "devReply": item.get("replyContent") or "",
                "teamStatus": "답변완료" if item.get("replyContent") else "미확인",
                "teamMemo": "",
            })
    except Exception as e:
        print(f"[Android 수집 오류] {app['name']}: {e}")
    return collected


def fetch_ios_reviews(app):
    """Apple App Store 공식 RSS 피드에서 최신 한국 리뷰를 수집합니다."""
    collected = []
    url = f"https://itunes.apple.com/kr/rss/customerreviews/id={app['iosId']}/sortBy=mostRecent/json"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as response:
            data = json.loads(response.read().decode("utf-8"))
            entries = data.get("feed", {}).get("entry", [])
            review_entries = [e for e in entries if "im:rating" in e]

            for idx, entry in enumerate(review_entries):
                raw_date = entry.get("updated", {}).get("label", "")
                title = entry.get("title", {}).get("label", "")
                content = entry.get("content", {}).get("label", "")
                full_text = f"[{title}] {content}" if title else content

                collected.append({
                    "id": int(f"{app['id']}2{idx:03d}"),
                    "appId": app["id"],
                    "os": "iOS",
                    "rating": int(entry.get("im:rating", {}).get("label", 5)),
                    "author": entry.get("author", {}).get("name", {}).get("label", "App Store 사용자"),
                    "date": raw_date[:10] if raw_date else datetime.now().strftime("%Y-%m-%d"),
                    "version": entry.get("im:version", {}).get("label", "-"),
                    "content": full_text,
                    "devReply": "",
                    "teamStatus": "미확인",
                    "teamMemo": "",
                })
    except Exception as e:
        print(f"[iOS 수집 오류] {app['name']}: {e}")
    return collected


def main():
    all_reviews = []
    for app in APPS:
        print(f"수집 중: {app['name']}...")
        all_reviews.extend(fetch_android_reviews(app, count=30))
        all_reviews.extend(fetch_ios_reviews(app))

    # 최신순 정렬
    all_reviews.sort(key=lambda x: x["date"], reverse=True)

    output = {
        "updatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "totalCount": len(all_reviews),
        "reviews": all_reviews,
    }

    with open("reviews_data.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"완료! 총 {len(all_reviews)}건의 실제 리뷰가 reviews_data.json에 저장되었습니다.")


if __name__ == "__main__":
    main()