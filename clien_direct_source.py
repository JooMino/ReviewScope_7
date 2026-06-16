import os
import shutil
import re
import requests
import hashlib
import undetected_chromedriver as uc
from urllib.parse import quote
from pathlib import Path
from bs4 import BeautifulSoup

BASE = "https://www.clien.net"
EXCLUDE_BOARDS = ["/board/sold/", "/board/news/"]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
}

# 날짜 문자열을 저장에 사용할 YY-MM-DD 형식으로 변환한다.
def format_date_to_yymmdd(date_str):
    """날짜 문자열을 추출하여 YY-MM-DD 포맷으로 통일하는 함수"""
    matches = re.findall(r'\d+', date_str)
    if len(matches) >= 3:
        year = matches[0][-2:]
        month = matches[1].zfill(2)
        day = matches[2].zfill(2)
        return f"{year}-{month}-{day}"
    return "YY-MM-DD"

# undetected_chromedriver 캐시를 정리해 드라이버 실행 오류를 줄인다.
def clear_uc_cache():
    appdata = os.environ.get('APPDATA')
    if appdata:
        cache_dir = os.path.join(appdata, "undetected_chromedriver")
        if os.path.exists(cache_dir):
            try:
                shutil.rmtree(cache_dir)
                print(" [System] 내부 드라이버 캐시를 자동으로 초기화했습니다.")
            except Exception as e:
                print(f" 캐시 초기화 실패 (실행에는 문제없을 수 있습니다): {e}")

# 크롤링에 사용할 크롬 드라이버 실행 옵션을 구성한다.
def get_chrome_options():
    options = uc.ChromeOptions()
    options.add_argument("--disable-popup-blocking")
    options.add_argument("--disable-blink-features=AutomationControlled") 
    options.add_argument("--lang=ko_KR")
    options.page_load_strategy = 'eager'
    options.add_argument("--blink-settings=imagesEnabled=false")
    return options

# 크롤링에 사용할 브라우저 드라이버를 초기화하고 반환한다.
def setup_driver():
    print(" [Clien] 브라우저 설정을 초기화합니다... ( Fast Mode)")
    try:
        options = get_chrome_options()
        driver = uc.Chrome(options=options, use_subprocess=True)
        driver.set_window_size(1920, 1080)
        return driver
    except Exception as e:
        error_msg = str(e)
        print(" 1차 로드 실패: 다른 PC 환경(버전 충돌)을 감지하여 자동 복구를 시도합니다.")
        clear_uc_cache()
        match = re.search(r"Current browser version is (\d+)", error_msg)
        if match:
            actual_version = int(match.group(1))
            print(f" 감지된 실제 크롬 버전({actual_version})으로 강제 재시도를 진행합니다.")
            try:
                options = get_chrome_options()
                driver = uc.Chrome(version_main=actual_version, options=options, use_subprocess=True)
                driver.set_window_size(1920, 1080)
                return driver
            except Exception as retry_err:
                print(f" 재시도 최종 실패: {retry_err}")
                raise retry_err
        else:
            print(" 크롬 버전을 에러 로그에서 찾을 수 없습니다.")
            raise e

# 입력 데이터에서 필요한 게시글 목록 정보를 추출한다.
def extract_post_list(html):
    soup = BeautifulSoup(html, "html.parser")
    items = []
    
    for a in soup.select("div.list_item a.subject_fixed"):
        href = a.get("href")
        if not href: continue
        if href.startswith("/"): href = BASE + href
            
        title = a.get("title") or a.get_text(strip=True)
        
        if any(pattern in href for pattern in EXCLUDE_BOARDS):
            print(f" 제외: {title} (게시판 필터)")
            continue
            
        items.append({"url": href, "title": title})
    return items

# 원본 게시글 본문 데이터를 분석해 사용하기 쉬운 구조로 변환한다.
def parse_post_content(html):
    soup = BeautifulSoup(html, "html.parser")

    title = "(제목 없음)"
    title_container = soup.select_one("div.post_title")
    if title_container:
        text = title_container.get_text(" ", strip=True)
        if text: title = text

    parts = title.rsplit(" ", 1)
    if len(parts) == 2 and parts[1].isdigit():
        title = parts[0]

    raw_date = "(작성일 없음)"
    views = "0"

    date_span = soup.select_one("span.view_count.date")
    if date_span:
        text = date_span.get_text(" ", strip=True)
        if text: raw_date = text
        
        strong = soup.select_one("span.view_count strong")
        if strong:
            v = strong.get_text(strip=True)
            if v: views = v


    formatted_date = format_date_to_yymmdd(raw_date)

    author_tag = soup.select_one("div.post_info div.post_contact span.nickname")
    author = author_tag.get_text(strip=True) if author_tag else "(작성자 없음)"

    content = ""
    for sel in ["article.post_article", "div.post_article", "div.post_content"]:
        body = soup.select_one(sel)
        if body:
            ps = body.select("p")
            if ps:
                content = "\n".join(p.get_text(" ", strip=True) for p in ps if p.get_text(strip=True))
            else:
                content = body.get_text("\n", strip=True)
            if content: break
    
    if not content: content = "(본문 파싱 실패)"

    comments = []
    for row in soup.select("div.comment_row"):
        if "blocked" in row.get("class", []): continue
        

        is_reply = "re" in row.get("class", [])
        
        nick_tag = row.select_one("div.post_contact span.nickname")
        nickname = nick_tag.get_text(strip=True) if nick_tag else "(익명)"
        content_div = row.select_one("div.comment_content")
        
        if content_div:
            text = content_div.get_text(" ", strip=True)
            if text:
                if is_reply:
                    comments.append(f'ㄴ[답글][{nickname}]:"{text}"')
                else:
                    comments.append(f'[댓글][{nickname}]:"{text}"')

    return {
        "title": title, "author": author, "formatted_date": formatted_date,
        "views": views, "content": content, "comments": comments,
    }

# 여러 위치의 게시글 목록 데이터를 모아 하나의 결과로 정리한다.
def collect_post_list(keyword, max_pages=5, max_posts=50):
    collected = []
    encoded_kw = quote(keyword)

    for page_num in range(max_pages):
        url = (
            f"{BASE}/service/search?"
            f"q={encoded_kw}&sort=accuracy&p={page_num}&boardCd=&isBoard=false"
        )
        print(f"\n 페이지 {page_num} 처리: {url}")

        try:
            r = requests.get(url, headers=HEADERS, timeout=10)
            r.raise_for_status()
        except Exception as e:
            print(f" 페이지 로딩 실패: {e}")
            break

        items = extract_post_list(r.text)
        if not items:
            print(" 게시글 없음")
            break

        for item in items:
            if item["url"] not in [p["url"] for p in collected]:
                collected.append(item)
                print(f" URL: {item['url']} | {item['title']}")
            if len(collected) >= max_posts: break
        if len(collected) >= max_posts: break

    return collected

# 외부 페이지나 API에서 필요한 게시글 본문 데이터를 가져온다.
def fetch_post_content(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        r.raise_for_status()
    except Exception as e:
        print(f" 본문 로딩 실패: {e}")
        return {
            "title": "(제목 없음)", "author": "(작성자 없음)",
            "formatted_date": "YY-MM-DD", "views": "0",
            "content": "(본문 없음)", "comments": [],
        }
    return parse_post_content(r.text)

# 스크립트의 주요 실행 흐름을 시작하고 전체 작업을 조율한다.
def main(keyword, cutoff_date, max_pages, max_posts, save_dir):
    print(f"=== 시작: 키워드 '{keyword}' 수집 ===")
    print(f"=== 저장 경로: {save_dir.absolute()} ===")

    posts = collect_post_list(keyword, max_pages, max_posts)
    print(f"\n 총 {len(posts)}개 게시글 URL 수집 완료\n")
    
    collected_urls = set()

    for idx, item in enumerate(posts, start=1):
        url = item["url"]
        print(f" 본문 로딩({idx}): {url}")
        
        data = fetch_post_content(url)
        
        if data["formatted_date"] < cutoff_date:
            print(f" ->  [필터링] 출시일 이전 글 ({data['formatted_date']}) 제외")
            continue
        hash_val = hashlib.md5(f"Clien_{url}".encode('utf-8')).hexdigest()[:16]
        
        trash_dir = save_dir.parent / "trash"
        trash_dir.mkdir(parents=True, exist_ok=True)
        
        if data["comments"]:
            file_name = save_dir / f"Clien_Direct_{hash_val}.txt"
        else:
            file_name = trash_dir / f"Clien_Direct_{hash_val}.txt"
        

        with open(file_name, "w", encoding="utf-8") as f:
            f.write(f'URL:"{url}"\n')
            f.write(f'Hash:"{hash_val}"\n')
            f.write(f'작성일:{data["formatted_date"]}\n')
            f.write(f'제목:{data["title"]}\n')
            f.write(f'작성자:{data["author"]}\n')
            f.write(f'조회수:{data["views"]}\n\n')
            f.write("[본문 내용]\n")
            f.write(data["content"] + "\n\n")
            f.write("[댓글 목록]\n")
            if data["comments"]:
                for c in data["comments"]:
                    f.write(f"{c}\n")
            else:
                f.write("(댓글 없음)\n")

        print(f" 저장 완료: {file_name.name}")
        collected_urls.add(url)

    print("\n 모든 작업 완료!")
    return collected_urls

# 관련 direct 크롤링 작업을 실행하고 필요한 후속 처리를 수행한다.
def run_direct_crawling(keyword, cutoff_date="00-00-00", save_dir=None):
    print(f"--- [Clien-Direct] '{keyword}' 수집 시작 ---")
    if save_dir is None:
        save_dir = Path(__file__).parent / "data_storage" / keyword / "clien"
        save_dir.mkdir(parents=True, exist_ok=True)
        
    return main(keyword, cutoff_date, 5, 30, save_dir)

if __name__ == "__main__":
    run_direct_crawling("테스트")