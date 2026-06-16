import os
import shutil
import time
import random
import re
import hashlib
import sys
from pathlib import Path

import undetected_chromedriver as uc
from bs4 import BeautifulSoup
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

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
    
    prefs = {
        "profile.managed_default_content_settings.images": 2,
        "profile.managed_default_content_settings.stylesheet": 2,
        "profile.managed_default_content_settings.fonts": 2
    }
    options.add_experimental_option("prefs", prefs)
    options.add_argument("--blink-settings=imagesEnabled=false")
    return options

# 크롤링에 사용할 브라우저 드라이버를 초기화하고 반환한다.
def setup_driver():
    print(" [Quasarzone] 브라우저 설정을 초기화합니다... ( Fast Mode)")
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

# 지정된 조건으로 search DuckDuckGo URL 검색을 수행하고 결과를 수집한다.
def search_duckduckgo_urls(driver, search_query, max_results, global_visited_set):
    full_query = f"{search_query} site:quasarzone.com"
    encoded_query = full_query.replace(" ", "+")
    url = f"https://duckduckgo.com/?q={encoded_query}&t=h_&ia=web"
    print(f"\n [DuckDuckGo] 검색 시작: '{full_query}' (목표: {max_results}개)")

    driver.get(url)

    try:
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "a[data-testid='result-title-a']"))
        )
    except TimeoutException:
        print(" 검색 결과 로딩 실패 (또는 결과 없음)")
        return []

    collected_links = []
    scroll_attempts = 0

    while len(collected_links) < max_results:
        soup = BeautifulSoup(driver.page_source, "html.parser")
        results = soup.select('a[data-testid="result-title-a"]')
        if not results: results = soup.select('article h2 a')

        found_new = False
        for res in results:
            link = res.get("href")
            if link and ("quasarzone.com" in link):
                if "qb_saleinfo" in link or "qb_partnersaleinfo" in link:
                    continue
                
                if link not in global_visited_set:
                    global_visited_set.add(link)
                    collected_links.append(link)
                    found_new = True
                    if len(collected_links) >= max_results:
                        break

        if len(collected_links) >= max_results:
            break

        if not found_new: scroll_attempts += 1
        else: scroll_attempts = 0
        
        if scroll_attempts >= 5: break

        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(random.uniform(1.0, 1.5))

        try:
            more_btn = driver.find_element(By.ID, "more-results")
            if more_btn.is_displayed():
                driver.execute_script("arguments[0].click();", more_btn)
                time.sleep(1.0)
        except Exception:
            pass

    return collected_links[:max_results]

# 입력 데이터에서 필요한 quasarzone 게시글 정보를 추출한다.
def extract_quasarzone_post(driver, url):
    try:
        driver.get(url)

        try:
            WebDriverWait(driver, 8).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "#new_contents"))
            )
        except TimeoutException:
            pass

        time.sleep(0.5)
        try: driver.execute_script("window.stop();")
        except: pass

        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(0.3)

        soup = BeautifulSoup(driver.page_source, "html.parser")

        filter_keywords = ["모바일 뉴스 - 최신 모바일기기", "핫딜 게시판"]
        found_filter = soup.find(
            lambda tag: tag.name in ["span", "h2"]
            and tag.get_text()
            and any(keyword in tag.get_text() for keyword in filter_keywords)
        )
        if found_filter:
            return None, None, None, None, None, []

        for tag in soup.find_all(
            ["script", "style", "iframe", "header", "footer", "nav"]
        ):
            tag.decompose()

        title_elem = soup.select_one("h1.title")
        title = title_elem.get_text(strip=True) if title_elem else soup.title.string

        top_area = soup.select_one(".common-view-area") or soup.select_one(".view-top-area")
        writer = "작성자 미상"
        raw_date = "날짜 미상"
        views = "0"
        if top_area:
            w_elem = top_area.select_one(".user-nick-text")
            if w_elem:
                writer = w_elem.get_text(strip=True)
            d_elem = top_area.select_one("span.date")
            if d_elem:
                raw_date = d_elem.get_text(strip=True)
            v_elem = top_area.select_one("span.count")
            if v_elem:
                views = v_elem.get_text(strip=True)

        formatted_date = format_date_to_yymmdd(raw_date)

        body_elem = soup.select_one("#new_contents")
        body_text = (
            body_elem.get_text(separator="\n", strip=True) if body_elem else "본문 없음"
        )

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
                content_elem = item.select_one(
                    ".note-editor"
                ) or item.select_one(".comment-content") or item.select_one(".txt")
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

        return title, writer, formatted_date, views, body_text, formatted_comments

    except Exception as e:
        print(f"  파싱 에러: {e}")
        return None, None, None, None, None, []

# 처리된 final 데이터를 파일이나 저장소에 기록한다.
def save_final_format(keyword, idx, title, writer, formatted_date, views, body, comments, url, save_dir):
    hash_val = hashlib.md5(f"Quasar_{url}".encode('utf-8')).hexdigest()[:16]
    
    file_path = save_dir / f"Quasar_Duck_{hash_val}.txt"
    
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(f'URL:"{url}"\n')
        f.write(f'Hash:"{hash_val}"\n')
        f.write(f'작성일:{formatted_date}\n')
        f.write(f'제목:{title}\n')
        f.write(f'작성자:{writer}\n')
        f.write(f'조회수:{views}\n\n')
        f.write("[본문 내용]\n")
        f.write(f"{body}\n\n")
        f.write("[댓글 목록]\n")
        
        if comments:
            for c in comments:
                f.write(f"{c}\n")
        else:
            f.write("(댓글 없음)\n")
            
    print(f"    저장 완료: {file_path.name}")

# 스크립트의 주요 실행 흐름을 시작하고 전체 작업을 조율한다.
def main(target_keyword, cutoff_date, save_dir, visited_urls=None):
    driver = setup_driver()
    global_visited = set(visited_urls) if visited_urls else set()
    current_file_idx = 1
    
    suffixes = ["리뷰", "후기", "장단점", "사용기"]
    
    try:
        for suffix in suffixes:
            search_query = f"{target_keyword} {suffix}".strip()
            print(f"\n" + "="*42)
            print(f" 검색 단계: [{search_query}] (최대 10개)")
            print("="*42)
            
            urls = search_duckduckgo_urls(driver, search_query, 5, global_visited)
            if not urls: continue
            
            for i, url in enumerate(urls, 1):
                print(f"[{i}/{len(urls)}] 접속: {url}")
                title, writer, formatted_date, views, body, comments = extract_quasarzone_post(driver, url)
                
                if title and body and title != "(제목 없음)":

                    if formatted_date < cutoff_date:
                        print(f"   ->  [필터링] 출시일 이전 글 ({formatted_date}) 제외")
                        continue

            
                    
                    save_final_format(target_keyword, current_file_idx, title, writer, formatted_date, views, body, comments, url, save_dir)
                    current_file_idx += 1
                else:
                    print("   ->  내용 없음/필터링됨 (PASS)")
                
                time.sleep(random.uniform(0.5, 1.0))

    except KeyboardInterrupt:
        print("\n 사용자 중단")
    finally:
        print(f"\n 전체 작업 종료! 총 {current_file_idx - 1}개 파일 저장됨.")
        try: driver.quit()
        except: pass

# 관련 duck 크롤링 작업을 실행하고 필요한 후속 처리를 수행한다.
def run_duck_crawling(keyword, cutoff_date="00-00-00", save_dir=None, visited_urls=None):
    print(f"--- [Quasar-Duck] '{keyword}' 우회 수집 시작 ---")
    if save_dir is None:
        save_dir = Path(__file__).parent / "data_storage" / keyword / "quasar"
        save_dir.mkdir(parents=True, exist_ok=True)
    main(keyword, cutoff_date, save_dir, visited_urls)

if __name__ == "__main__":
    run_duck_crawling("테스트")