import os
import shutil
import asyncio
import re
import requests
import hashlib
import undetected_chromedriver as uc
from pathlib import Path
from urllib.parse import urlparse, parse_qs, quote

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

BASE_URL = "https://gall.dcinside.com"
SEARCH_BASE = "https://search.dcinside.com"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9",
    "Connection": "keep-alive",
    "DNT": "1",
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
    elif len(matches) >= 2:
        from datetime import datetime
        year = str(datetime.now().year)[-2:]
        month = matches[0].zfill(2)
        day = matches[1].zfill(2)
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
    print(" [DC] 브라우저 설정을 초기화합니다... ( Fast Mode)")
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

# 게시글 본문에서 앱 안내문처럼 분석에 불필요한 문구를 제거한다.
def clean_content(text: str) -> str:
    remove_words = [
        "DC official App", "디시인사이드 공식 앱", "앱에서 보기", "앱으로 보기",
        "모바일에서 더 편하게", "-dc app", "dc app", "- dc App", "- dc official App",
    ]
    for w in remove_words:
        text = text.replace(w, "")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines)

# 입력 데이터에서 필요한 숫자 정보를 추출한다.
def extract_numbers(s: str):
    return re.findall(r"\d+", s)

# 검색 키워드와 다른 모델명이 섞인 게시글인지 판별한다.
def reject_other_models(text: str, keyword: str) -> bool:
    norm_text = re.sub(r"\s+", "", (text or "").lower())
    norm_kw = re.sub(r"\s+", "", (keyword or "").lower())
    if norm_kw and norm_kw not in norm_text:
        return False
    kw_nums = set(extract_numbers(keyword or ""))
    if not kw_nums:
        return True
    text_nums = set(extract_numbers(text or ""))
    for n in kw_nums:
        text_nums.discard(n)
    return len(text_nums) == 0

# 게시글 HTML에서 제목 텍스트를 추출한다.
def extract_title(soup: BeautifulSoup) -> str:
    for sel in ["span.title_subject", "h3.title", ".title_subject"]:
        el = soup.select_one(sel)
        if el: return el.get_text(strip=True)
    return ""

# 게시글 HTML에서 조회수와 추천수 정보를 추출한다.
def extract_view_reco(soup: BeautifulSoup):
    view = soup.select_one("span.gall_count")
    reco = soup.select_one("span.up_num")
    return (view.get_text(strip=True) if view else "0", 
            reco.get_text(strip=True) if reco else "0")

# 게시글의 날짜 문자열을 찾아 표준 날짜 값으로 변환한다.
def extract_date(soup: BeautifulSoup) -> str:
    for sel in ["span.gall_date", "span.date", "span.write_date"]:
        el = soup.select_one(sel)
        if el: 

            return el.get("title") or el.get_text(strip=True)
    return ""

# 게시글 HTML에서 작성자와 식별 정보를 추출한다.
def extract_author_and_ip(soup: BeautifulSoup):
    box = soup.select_one("div.gall_writer.ub-writer")
    if not box: return "", ""
    nick_el = box.select_one("span.nickname")
    nick = nick_el.get_text(strip=True) if nick_el else (box.get("data-nick") or "").strip()
    uid = (box.get("data-uid") or "").strip()
    if uid:
        id_or_ip = uid
    else:
        ip_el = box.select_one("span.ip")
        id_or_ip = ip_el.get_text(strip=True) if ip_el else (box.get("data-ip") or "").strip()
    return nick, id_or_ip

# 외부 페이지나 API에서 필요한 댓글 데이터를 가져온다.
async def fetch_comments(page, url: str):
    results = []
    try:
        await page.goto(url, wait_until="domcontentloaded")
        await page.wait_for_timeout(1500)
        comment_items = await page.query_selector_all('ul.cmt_list > li[id^="comment_li_"]')

        for comment_li in comment_items:
            text_el = await comment_li.query_selector("p.usertxt.ub-word, p.ub-word")
            if not text_el: continue
            raw = (await text_el.inner_text()) or ""
            main_text = clean_content(raw)
            if not main_text: continue

            writer_box = await comment_li.query_selector("span.gall_writer.ub-writer")
            nick, id_or_ip = "", ""
            if writer_box:
                nick = (await writer_box.get_attribute("data-nick")) or ""
                uid = (await writer_box.get_attribute("data-uid")) or ""
                data_ip = (await writer_box.get_attribute("data-ip")) or ""
                id_or_ip = uid if uid else data_ip

            comment_obj = {"author": nick.strip(), "id_or_ip": id_or_ip.strip(), 
                           "content": main_text, "is_reply": False, "replies": []}

            comment_id = await comment_li.get_attribute("id")
            if comment_id and "comment_li_" in comment_id:
                cmt_no = comment_id.replace("comment_li_", "")
                reply_list = await page.query_selector(f'ul.reply_list[id="reply_list_{cmt_no}"]')
                if reply_list:
                    reply_items = await reply_list.query_selector_all("li")
                    for r_li in reply_items:
                        r_text_el = await r_li.query_selector("p.usertxt.ub-word, p.ub-word")
                        if not r_text_el: continue
                        r_text = clean_content((await r_text_el.inner_text()) or "")
                        if not r_text: continue
                        r_writer_box = await r_li.query_selector("span.gall_writer.ub-writer")
                        r_nick, r_id_or_ip = "", ""
                        if r_writer_box:
                            r_nick = (await r_writer_box.get_attribute("data-nick")) or ""
                            r_uid = (await r_writer_box.get_attribute("data-uid")) or ""
                            r_ip = (await r_writer_box.get_attribute("data-ip")) or ""
                            r_id_or_ip = r_uid if r_uid else r_ip
                        comment_obj["replies"].append({"author": r_nick.strip(), "id_or_ip": r_id_or_ip.strip(), 
                                                       "content": r_text, "is_reply": True})
            results.append(comment_obj)
    except Exception as e:
        print("fetch_comments 에러:", e, url)
    return results

# 원본 dc 게시글 데이터를 분석해 사용하기 쉬운 구조로 변환한다.
async def parse_dc_post(page, item, headers):
    url, list_title = item["url"], item["list_title"]
    try:
        r = requests.get(url, headers=headers, timeout=10)
    except: return None, "REQUEST_FAIL"
    soup = BeautifulSoup(r.text, "html.parser")
    title = extract_title(soup) or list_title
    view, reco = extract_view_reco(soup)
    author, author_id_or_ip = extract_author_and_ip(soup)
    raw_date = extract_date(soup)
    formatted_date = format_date_to_yymmdd(raw_date)
    body = soup.select_one(".write_div") or soup.select_one("#writeContents")
    content = clean_content(body.get_text("\n", strip=True)) if body else "(본문 없음)"
    comments = await fetch_comments(page, url)
    return {"url": url, "title": title, "author": author, "author_id_or_ip": author_id_or_ip,
            "content": content, "formatted_date": formatted_date, "view": view, "reco": reco, "comments": comments}, None

# 게시글 URL에서 고유 식별자를 뽑아 중복 제거에 사용할 키를 만든다.
def get_post_key(url: str) -> str:
    try:
        qs = parse_qs(urlparse(url).query)
        gid, no = qs.get("id", [""])[0], qs.get("no", [""])[0]
        return f"{gid}:{no}" if gid and no else url
    except: return url

# 비동기 흐름에서 필요한 goto 작업을 수행한다.
async def safe_goto(page, url, wait_time: int = 2, timeout: int = 60000):
    try:
        await page.goto(url, timeout=timeout)
        await page.wait_for_timeout(wait_time * 1000)
    except Exception as e:
        print(f" safe_goto 경고: {e}")

# 여러 위치의 게시글 목록 데이터를 모아 하나의 결과로 정리한다.
async def collect_post_list(page, encoded_keyword, safe_goto, SEARCH_KEYWORD, max_posts: int = 20):
    posts, seen, prev_keys, page_no = [], set(), set(), 1
    while True:
        list_url = f"{SEARCH_BASE}/post/sort/accuracy/q/{encoded_keyword}" if page_no == 1 else \
                   f"{SEARCH_BASE}/post/p/{page_no}/sort/accuracy/q/{encoded_keyword}"
        print(f"\n 리스트 페이지 이동: {list_url}")
        
        await safe_goto(page, list_url)
        
        soup = BeautifulSoup(await page.content(), "html.parser")
        anchors = soup.select("a[href*='/board/view']")
        if not anchors: break
        current_keys, added = set(), 0
        for a in anchors:
            href = a.get("href")
            if not href: continue
            href = "https:" + href if href.startswith("//") else (BASE_URL + href if href.startswith("/") else href)
            key = get_post_key(href)
            current_keys.add(key)
            if key in seen: continue
            seen.add(key)
            title = a.get_text(" ", strip=True)
            if not reject_other_models(title, SEARCH_KEYWORD): continue
            posts.append({"url": href, "list_title": title})
            added += 1
            if len(posts) >= max_posts: return posts
        if added == 0 or current_keys == prev_keys: break
        prev_keys = current_keys
        page_no += 1
    return posts

# 스크립트의 주요 실행 흐름을 시작하고 전체 작업을 조율한다.
async def main(keyword: str, cutoff_date: str, save_dir: Path):
    print(f"=== 시작: 키워드 '{keyword}' 수집 ===")
    print(f"=== 저장 경로: {save_dir.absolute()} ===")

    collected_urls = set()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        ctx = await browser.new_context()
        page = await ctx.new_page()

        await safe_goto(page, "https://gall.dcinside.com")
        try:
            await page.fill("#preSWord", keyword)
            await page.click("#searchSubmit")
            await page.wait_for_timeout(2000)
            await page.click("ul.gnb_list a:has-text('게시물')")
            await page.wait_for_timeout(2000)
        except Exception: pass

        first_url = page.url
        encoded_keyword = first_url.split("q/")[1].split("?")[0] if "/q/" in first_url else quote(keyword)

        posts = await collect_post_list(page, encoded_keyword, safe_goto, keyword, max_posts=30)
        
        for i, item in enumerate(posts, start=1):
            data, err = await parse_dc_post(page, item, HEADERS)
            if err or not data: continue
            


            if data['formatted_date'] < cutoff_date:
                print(f" ->  [필터링] 출시일 이전 글 ({data['formatted_date']}) 수집 제외")
                continue

            url = data['url']
            hash_val = hashlib.md5(f"DC_{url}".encode('utf-8')).hexdigest()[:16]
            
            trash_dir = save_dir.parent / "trash"
            trash_dir.mkdir(parents=True, exist_ok=True)
            
            file_name = f"DC_Direct_{hash_val}.txt"
            if data['comments']:
                file_path = save_dir / file_name
            else:
                file_path = trash_dir / file_name

            with open(file_path, "w", encoding="utf-8") as f:
                f.write(f'URL:"{url}"\n')
                f.write(f'Hash:"{hash_val}"\n')
                f.write(f'작성일:{data["formatted_date"]}\n')
                f.write(f'제목:{data["title"]}\n')
                
                author_id_or_ip = data['author_id_or_ip'].strip('()')
                if author_id_or_ip and '.' not in author_id_or_ip:
                    author_str = f"{data['author']} ({author_id_or_ip})"
                elif author_id_or_ip:
                    author_str = f"({author_id_or_ip})"
                else:
                    author_str = data['author']
                
                f.write(f'작성자:{author_str}\n')
                f.write(f'조회수:{data["view"]}\n\n')
                f.write("[본문 내용]\n")
                f.write(f"{data['content']}\n\n")
                f.write("[댓글 목록]\n")
                
                if data['comments']:
                    for c in data['comments']:
                        c_disp = f"{c['author']}"
                        f.write(f'[댓글][{c_disp}]:"{c["content"]}"\n')
                        for r in c.get("replies", []):
                            r_disp = f"{r['author']}"
                            f.write(f'ㄴ[답글][{r_disp}]:"{r["content"]}"\n')
                else:
                    f.write("(댓글 없음)\n")
            
            print(f"[{i}/{len(posts)}] 저장 완료: {file_name}")
            collected_urls.add(url)

        await browser.close()
        print("\n 모든 작업 완료!")
        return collected_urls

# 관련 direct 크롤링 작업을 실행하고 필요한 후속 처리를 수행한다.
def run_direct_crawling(keyword, cutoff_date="00-00-00", save_dir=None):
    print(f"--- [Direct] '{keyword}' 수집 시작 ---")
    if save_dir is None:
        save_dir = Path(__file__).parent / "data_storage" / keyword / "dc"
        save_dir.mkdir(parents=True, exist_ok=True)
        
    return asyncio.run(main(keyword, cutoff_date, save_dir))

if __name__ == "__main__":
    run_direct_crawling("테스트")