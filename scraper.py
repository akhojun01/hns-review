import hashlib
import json
import re
import sys
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from html import unescape
from pathlib import Path

from google_play_scraper import Sort, app as play_app, reviews


KST = timezone(timedelta(hours=9))
START_DATE = '2026-01-01'
APPS_FILE = Path('apps.json')
OUTPUT_FILE = Path('reviews_data.json')


def review_row(app, os, store_id, rating, author, date_str, version, content, reply=''):
    key = f"{app['id']}:{os}:{store_id}".encode()
    return {
        'id': int(hashlib.sha256(key).hexdigest()[:12], 16),
        'appId': app['id'],
        'os': os,
        'rating': int(rating),
        'author': author,
        'date': date_str,
        'version': version,
        'content': content,
        'devReply': reply,
        'teamStatus': '답변완료' if reply else '미확인',
    }


def android_pages(app):
    continuation = None
    while True:
        items, next_page = reviews(
            app['androidPkg'], lang='ko', country='kr', sort=Sort.NEWEST,
            count=200, continuation_token=continuation,
        )
        if not items:
            return 'feed_end'
        yield [
            review_row(
                app, 'Android', item['reviewId'], item['score'],
                item.get('userName') or 'Google Play 사용자',
                item['at'].date().isoformat(),
                item.get('reviewCreatedVersion') or '-', item.get('content') or '',
                item.get('replyContent') or '',
            )
            for item in items
        ]
        if next_page is None or not next_page.token:
            return 'feed_end'
        if continuation and next_page.token == continuation.token:
            return 'repeated_page'
        continuation = next_page


def ios_pages(app):
    root_url = 'https://itunes.apple.com/kr/rss/customerreviews/'
    for page_number in range(1, 11):
        page_path = '' if page_number == 1 else f'page={page_number}/'
        url = f"{root_url}{page_path}id={app['iosId']}/sortBy=mostRecent/json"
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
        if not rows:
            return 'feed_end'
        yield rows
    return 'page_limit'


def collect_source(app, os, pages, archive, previous_scan, now):
    cutoff = START_DATE
    last_success = previous_scan.get('lastSuccess')
    if last_success:
        overlap = date.fromisoformat(last_success) - timedelta(days=3)
        cutoff = max(cutoff, overlap.isoformat())

    seen = set()
    scanned_dates = []
    finished = 'failed'
    label = f"{app['name']} {os}"
    cursor = pages(app)
    try:
        while True:
            page = next(cursor)
            unseen = [row for row in page if row['id'] not in seen]
            if not unseen:
                finished = 'repeated_page'
                break
            for row in unseen:
                seen.add(row['id'])
                if row['date']:
                    scanned_dates.append(row['date'])
                if row['date'] >= START_DATE:
                    archive[row['id']] = row
            if scanned_dates and min(scanned_dates) < cutoff:
                finished = 'cutoff'
                break
    except StopIteration as end:
        finished = end.value
    except Exception as error:
        print(f'::warning::{label} 수집 실패 ({type(error).__name__}): {error}', file=sys.stderr)
    finally:
        cursor.close()

    if not seen and archive and finished == 'feed_end':
        finished = 'failed'
        print(f'::warning::{label} 응답에 리뷰가 없어 기존 데이터를 유지합니다.', file=sys.stderr)

    earliest = min((row['date'] for row in archive.values()), default='')
    print(f'{label}: 확인 {len(seen)}건, 누적 {len(archive)}건, 최초 {earliest or "-"}, 종료 사유 {finished}', flush=True)
    return {
        'appId': app['id'],
        'os': os,
        'count': len(archive),
        'earliestDate': earliest,
        'backfillOldestSeen': previous_scan.get('backfillOldestSeen') or min(scanned_dates, default=''),
        'backfillEnd': previous_scan.get('backfillEnd') or finished,
        'lastSuccess': last_success if finished == 'failed' else now.date().isoformat(),
        'status': finished,
    }


def google_listing(package):
    listing = play_app(package, lang='ko', country='kr')
    return {'title': listing.get('title') or '', 'rating': listing.get('score')}


def apple_listing(app_id):
    request = urllib.request.Request(
        f'https://apps.apple.com/kr/app/id{app_id}',
        headers={'User-Agent': 'Mozilla/5.0', 'Accept-Language': 'ko-KR,ko;q=0.9'},
    )
    with urllib.request.urlopen(request, timeout=12) as response:
        page = response.read().decode('utf-8')

    def page_text(pattern):
        match = re.search(pattern, page, re.DOTALL | re.IGNORECASE)
        return ' '.join(unescape(re.sub(r'<[^>]+>', '', match[1])).split()) if match else ''

    title = page_text(r'<h1\b[^>]*>(.*?)</h1>')
    subtitle = page_text(r'<p\b[^>]*class="[^"]*\bsubtitle\b[^"]*"[^>]*>(.*?)</p>')
    rating = page_text(r'<div\b[^>]*data-testid="amp-rating__average-rating"[^>]*>(.*?)</div>')
    if not title:
        raise RuntimeError('App Store 상품 정보를 확인할 수 없습니다.')
    if rating:
        return {'title': title, 'subtitle': subtitle, 'rating': float(rating.replace(',', '.'))}

    lookup = f'https://itunes.apple.com/lookup?id={app_id}&country=kr&entity=software'
    with urllib.request.urlopen(lookup, timeout=12) as response:
        results = json.load(response)['results']
    result = next((item for item in results if str(item.get('trackId')) == str(app_id)), {})
    return {'title': result.get('trackName') or title, 'subtitle': subtitle,
            'rating': result.get('averageUserRating') or None}


def read_store_listings(app, previous_app):
    listings = {}
    for os, key, get_listing in (
        ('Android', 'androidPkg', google_listing), ('iOS', 'iosId', apple_listing),
    ):
        try:
            listing = get_listing(app[key])
            if not listing['title']:
                raise RuntimeError('스토어 상품명을 확인할 수 없습니다.')
            listings[os] = listing
            print(f"{app['name']} {os} 스토어 평점: {listing.get('rating')}", flush=True)
        except Exception as error:
            if previous_app.get(key) == app[key]:
                previous = previous_app.get('storeListings', {}).get(os)
                if previous:
                    listings[os] = previous
            print(f"::warning::{app['name']} {os} 스토어 상품 정보 갱신 실패: {error}", file=sys.stderr)
    return listings


def main():
    apps = json.loads(APPS_FILE.read_text(encoding='utf-8'))
    app_ids = [app['id'] for app in apps]
    if not apps or len(app_ids) != len(set(app_ids)):
        raise ValueError('apps.json에 중복되지 않는 ID를 가진 앱을 하나 이상 등록해 주세요.')

    old = json.loads(OUTPUT_FILE.read_text(encoding='utf-8')) if OUTPUT_FILE.exists() else {}
    old_apps = {app['id']: app for app in old.get('apps', [])}
    old_sources = {(src['appId'], src['os']): src for src in old.get('sources', [])}
    old_reviews = defaultdict(dict)
    for row in old.get('reviews', []):
        if row['date'] >= START_DATE:
            row.pop('teamMemo', None)
            old_reviews[(row['appId'], row['os'])][row['id']] = row

    now = datetime.now(KST)
    sources = []
    combined = []
    errors = []
    for app in apps:
        for os, app_key, pages in (
            ('Android', 'androidPkg', android_pages), ('iOS', 'iosId', ios_pages),
        ):
            key = (app['id'], os)
            same_app = old_apps.get(app['id'], {}).get(app_key) == app[app_key]
            archive = old_reviews[key] if same_app else {}
            previous = old_sources.get(key, {}) if same_app else {}
            if not previous.get('lastSuccess'):
                previous = {}
            source = collect_source(app, os, pages, archive, previous, now)
            if source['status'] == 'failed':
                errors.append(f"{app['name']} {os}")
            sources.append(source)
            combined.extend(archive.values())

    if len(errors) == len(sources):
        raise RuntimeError('스토어 리뷰 수집에 실패해 마지막 게시 데이터를 유지합니다.')

    for app in apps:
        app['storeListings'] = read_store_listings(app, old_apps.get(app['id'], {}))

    output = {
        'updatedAt': now.strftime('%Y-%m-%d %H:%M:%S KST'),
        'startDate': START_DATE,
        'totalCount': len(combined),
        'apps': apps,
        'reviews': sorted(combined, key=lambda row: (row['date'], row['id']), reverse=True),
        'sources': sources,
        'errors': errors,
    }
    temp_file = OUTPUT_FILE.with_suffix('.tmp')
    temp_file.write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temp_file.replace(OUTPUT_FILE)
    print(f"게시할 누적 리뷰 {output['totalCount']}건")


if __name__ == '__main__':
    main()
