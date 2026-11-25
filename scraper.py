import hashlib
import json
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from google_play_scraper import Sort, reviews


KST = timezone(timedelta(hours=9))
APPS_FILE = Path('apps.json')
OUTPUT_FILE = Path('reviews_data.json')


def review_row(app, os, store_id, rating, author, date, version, content, reply=''):
    key = f"{app['id']}:{os}:{store_id}".encode()
    return {
        'id': int(hashlib.sha256(key).hexdigest()[:12], 16),
        'appId': app['id'],
        'os': os,
        'rating': int(rating),
        'author': author,
        'date': date,
        'version': version,
        'content': content,
        'devReply': reply,
        'teamStatus': '답변완료' if reply else '미확인',
        'teamMemo': '',
    }


def fetch_android(app):
    items, _ = reviews(
        app['androidPkg'], lang='ko', country='kr', sort=Sort.NEWEST, count=30,
    )
    return [
        review_row(
            app, 'Android', item['reviewId'], item['score'],
            item.get('userName') or 'Google Play 사용자',
            item['at'].date().isoformat(),
            item.get('reviewCreatedVersion') or '-', item.get('content') or '',
            item.get('replyContent') or '',
        )
        for item in items
    ]


def fetch_ios(app):
    url = (
        'https://itunes.apple.com/kr/rss/customerreviews/'
        f"id={app['iosId']}/sortBy=mostRecent/json"
    )
    request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(request, timeout=15) as response:
        entries = json.load(response)['feed'].get('entry', [])

    rows = []
    for item in entries:
        if 'im:rating' not in item:
            continue
        title = item.get('title', {}).get('label', '')
        content = item.get('content', {}).get('label', '')
        rows.append(review_row(
            app, 'iOS', item['id']['label'], item['im:rating']['label'],
            item.get('author', {}).get('name', {}).get('label', 'App Store 사용자'),
            item.get('updated', {}).get('label', '')[:10],
            item.get('im:version', {}).get('label', '-'),
            f'[{title}] {content}' if title else content,
        ))
    return rows


def main():
    apps = json.loads(APPS_FILE.read_text(encoding='utf-8'))
    app_ids = [app['id'] for app in apps]
    if not apps or len(app_ids) != len(set(app_ids)):
        raise ValueError('apps.json에 중복되지 않는 ID를 가진 앱을 하나 이상 등록해 주세요.')

    previous = defaultdict(list)
    old_apps = {}
    if OUTPUT_FILE.exists():
        old = json.loads(OUTPUT_FILE.read_text(encoding='utf-8'))
        old_apps = {app['id']: app for app in old.get('apps', [])}
        for row in old['reviews']:
            previous[(row['appId'], row['os'])].append(row)

    collected = {}
    failures = []
    fresh_count = 0
    for app in apps:
        for os, app_key, fetch in (
            ('Android', 'androidPkg', fetch_android), ('iOS', 'iosId', fetch_ios),
        ):
            source = f"{app['name']} {os}"
            try:
                rows = fetch(app)
                fresh_count += len(rows)
                print(f'{source}: {len(rows)}건 수집', flush=True)
            except Exception as error:
                failures.append(source)
                old_app = old_apps.get(app['id'], {})
                rows = previous[(app['id'], os)] if old_app.get(app_key) == app[app_key] else []
                print(f'::warning::{source} 수집 실패 ({type(error).__name__}): {error}', file=sys.stderr)
            for row in rows:
                collected[row['id']] = row

    if not fresh_count:
        raise RuntimeError('이번 실행에서 게시할 스토어 리뷰를 확인하지 못했습니다.')

    output = {
        'updatedAt': datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S KST'),
        'totalCount': len(collected),
        'apps': apps,
        'reviews': sorted(collected.values(), key=lambda row: row['date'], reverse=True),
        'errors': failures,
    }
    OUTPUT_FILE.write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f"게시할 리뷰 {output['totalCount']}건")


if __name__ == '__main__':
    main()
