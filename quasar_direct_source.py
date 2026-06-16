import asyncio
import hashlib
import sys
import re
from urllib.parse import quote
from pathlib import Path

from playwright.async_api import async_playwright, Browser
from bs4 import BeautifulSoup




BASE = "https://quasarzone.com"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9",
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

# 게시글 본문에서 앱 안내문처럼 분석에 불필요한 문구를 제거한다.
def clean_content(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines)

# 입력 데이터에서 필요한 게시글 목록 정보를 추출한다.
def extract_post_list(html: str):
    soup = BeautifulSoup(html, "html.parser")
    items = []
    for cont in soup.select("div.cont-wrap"):
        a = cont.select_one("p.title a")
        if not a:
            continue
        href = a.get("href")
        if not href:
            continue
        if href.startswith("/"):
            href = BASE + href
        
        if "qb_saleinfo" in href or "qb_partnersaleinfo" in href:
            continue
        
        title = a.get_text(" ", strip=True)

        label_el = cont.select_one("p.label")
        label = label_el.get_text(strip=True) if label_el else ""
        if label in ("장터", "핫딜"):
            continue

        items.append({"url": href, "title": title, "label": label})
    return items

# 게시글 HTML에서 댓글 목록을 추출해 저장 가능한 구조로 만든다.
def extract_comments(html: str):
    soup = BeautifulSoup(html, "html.parser")
    formatted_comments = []
    
    reply_area = soup.select_one(".reply-list")
    if reply_area:
        cmt_items = reply_area.select("li")
        for item in cmt_items:
            if not item.has_attr("id") or not item["id"].startswith("comment"):
                continue
            
            nick_elem = item.select_one(".user-nick-text")
            if not nick_elem:
                continue
            nickname = nick_elem.get_text(strip=True)
            
            content_elem = item.select_one(".note-editor") or item.select_one(".comment-content") or item.select_one(".txt")
            if not content_elem:
                continue
            
            full_text = content_elem.get_text(separator=" ", strip=True)
            class_list = item.get("class", [])
            is_reply = "reply" in class_list
            
            if is_reply:
                reply_target_tag = content_elem.select_one(".re-reply-id")
                if reply_target_tag:
                    target_name = reply_target_tag.get_text(strip=True)
                    full_text = full_text.replace(target_name, "", 1).strip()
                cmt_str = f'ㄴ[답글][{nickname}]:"{full_text}"'
            else:
                cmt_str = f'[댓글][{nickname}]:"{full_text}"'
            
            formatted_comments.append(cmt_str)
            
    return formatted_comments



# 비동기 흐름에서 필요한 goto 작업을 수행한다.
async def safe_goto(
    browser: Browser, page, url: str, retry: int = 3, timeout: int = 15000
) -> bool:
    for _ in range(retry):
        try:
            await asyncio.wait_for(page.goto(url, timeout=0), timeout=timeout / 1000)
            return True
        except Exception:
            try:
                await page.close()
            except Exception:
                pass
            ctx = await browser.new_context()
            page = await ctx.new_page()
            await page.set_extra_http_headers(HEADERS)
            await asyncio.sleep(1)
    return False

# 여러 위치의 게시글 목록 데이터를 모아 하나의 결과로 정리한다.
async def collect_post_list(
    browser: Browser, page, keyword: str, max_pages: int = 5, max_posts: int = 50
):
    collected = []
    page_num = 1
    encoded_kw = quote(keyword)

    while len(collected) < max_posts and page_num <= max_pages:
        list_url = (
            f"{BASE}/groupSearches?keyword={encoded_kw}&kind=subject&page={page_num}"
        )
        print(f" -> 목록 페이지 진입: {page_num}")

        ok = await safe_goto(browser, page, list_url)
        if not ok:
            break

        try:
            await page.wait_for_selector("div.tit-cont-wrap", timeout=6000)
        except Exception:
            break

        html = await page.content()
        items = extract_post_list(html)
        if not items:
            break

        for item in items:
            if item["url"] not in collected:
                collected.append(item["url"])
                if len(collected) >= max_posts:
                    break

        page_num += 1

    return collected

# 외부 페이지나 API에서 필요한 본문 parallel 데이터를 가져온다.
async def fetch_content_parallel(browser: Browser, urls, max_workers: int = 4):
    semaphore = asyncio.Semaphore(max_workers)
    results = {}

    # 외부 페이지나 API에서 필요한 one 데이터를 가져온다.
    async def fetch_one(idx, url):
        async with semaphore:
            page = await browser.new_page()
            try:
                await page.set_extra_http_headers(HEADERS)
                await page.goto(url, timeout=30000, wait_until="domcontentloaded")
                
                try:
                    await page.wait_for_selector(".reply-list", timeout=3000)
                except Exception:
                    pass

                full_html = await page.content()
                soup = BeautifulSoup(full_html, "html.parser")

                body_elem = soup.select_one("#new_contents")
                body_text = (
                    clean_content(body_elem.get_text("\n", strip=True))
                    if body_elem
                    else "(본문 없음)"
                )

                title = "(제목 없음)"
                t_el = soup.select_one("h1.title")
                if t_el:
                    title = t_el.get_text(strip=True)

                author = "(작성자 없음)"
                raw_date = "(작성일 없음)"
                view_count = "0"

                util_area = soup.select_one("div.util-area") or soup.select_one(
                    ".common-view-area"
                )
                if util_area:
                    a_el = util_area.select_one("div.user-nick-text")
                    if a_el:
                        author = a_el.get_text(strip=True)
                    d_el = util_area.select_one("span.date")
                    if d_el:
                        raw_date = d_el.get_text(strip=True)
                    v_el = util_area.select_one("span.count")
                    if v_el:
                        view_count = v_el.get_text(strip=True)

                formatted_date = format_date_to_yymmdd(raw_date)
                comments = extract_comments(full_html)

                results[idx] = {
                    "url": url,
                    "title": title,
                    "author": author,
                    "formatted_date": formatted_date,
                    "view_count": view_count,
                    "content": body_text,
                    "comments": comments,
                }
            except Exception:
                results[idx] = None
            finally:
                await page.close()

    tasks = [fetch_one(i, url) for i, url in enumerate(urls, start=1)]
    await asyncio.gather(*tasks)
    return results



# 비동기 흐름에서 필요한 작업을 수행한다.
async def main_async(keyword: str, cutoff_date: str, save_dir: Path, max_pages: int = 5, max_posts: int = 50):
    print(f"=== 시작: 키워드 '{keyword}' 수집 ===")
    print(f"=== 저장 경로: {save_dir.absolute()} ===")

    collected_urls = set()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        await page.set_extra_http_headers(HEADERS)

        urls = await collect_post_list(browser, page, keyword, max_pages, max_posts)
        print(f" -> 총 {len(urls)}개 URL 확보. 본문 수집 시작...\n")

        if not urls:
            await browser.close()
            return collected_urls

        posts = await fetch_content_parallel(browser, urls, max_workers=4)

        for idx, item in posts.items():
            if not item:continue
            

            if item.get("formatted_date", "00-00-00") < cutoff_date:
                print(f" ->  [필터링] 출시일 이전 글 ({item['formatted_date']}) 제외")
                continue


            url = item['url']
            hash_val = hashlib.md5(f"Quasar_{url}".encode('utf-8')).hexdigest()[:16]
            
            trash_dir = save_dir.parent / "trash"
            trash_dir.mkdir(parents=True, exist_ok=True)
            
            file_name = f"Quasar_Direct_{hash_val}.txt"
            if item["comments"]:
                file_path = save_dir / file_name
            else:
                file_path = trash_dir / file_name

            with open(file_path, "w", encoding="utf-8") as f:
                f.write(f'URL:"{url}"\n')
                f.write(f'Hash:"{hash_val}"\n')
                f.write(f'작성일:{item.get("formatted_date", "")}\n')
                f.write(f'제목:{item.get("title", "")}\n')
                f.write(f'작성자:{item.get("author", "")}\n')
                f.write(f'조회수:{item.get("view_count", "0")}\n\n')

                f.write("[본문 내용]\n")
                f.write(item["content"] + "\n\n")

                f.write("[댓글 목록]\n")
                if item["comments"]:
                    for c in item["comments"]:
                        f.write(f"{c}\n")
                else:
                    f.write("(댓글 없음)\n")

            print(f"[{idx}/{len(urls)}] 저장 완료: {file_name}")
            collected_urls.add(url)

        await browser.close()
        print("\n 모든 작업 완료!")
        return collected_urls

# 관련 direct 크롤링 작업을 실행하고 필요한 후속 처리를 수행한다.
def run_direct_crawling(keyword, cutoff_date="00-00-00", save_dir=None):
    print(f"--- [Quasar-Direct] '{keyword}' 수집 시작 ---")
    if save_dir is None:
        save_dir = Path(__file__).parent / "data_storage" / keyword / "quasar"
        save_dir.mkdir(parents=True, exist_ok=True)
        
    return asyncio.run(main_async(keyword, cutoff_date, save_dir, max_pages=5, max_posts=30))


if __name__ == "__main__":
    run_direct_crawling("테스트")