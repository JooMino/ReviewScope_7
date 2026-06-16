import os
import shutil
import time
import random
import re
import hashlib
from pathlib import Path

import undetected_chromedriver as uc
from bs4 import BeautifulSoup
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

SITE_CONFIG = {
    "fmkorea.com": {
        "wait_selector": ".rd_body",
        "title": ".top_area .np_18px",
        "writer_info": ".btm_area .side .member_plate",
        "date": ".top_area .date",
        "info_bar": ".btm_area .side.fr",
        "body": ".rd_body",
        "comment_wrapper": "ul.fdb_lst_ul li",
        "comment_writer": ".meta a",
        "comment_text": ".comment-content",
    }
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
    print(" [FMK] 브라우저 설정을 초기화합니다... ( Fast Mode)")
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
    full_query = f"{search_query} site:fmkorea.com"
    print(f"\n [DuckDuckGo] 검색 시작: '{full_query}' (목표: {max_results}개)")
    
    driver.get(f"https://duckduckgo.com/?q={full_query}&t=h_&ia=web")
    
    try:
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.ID, "react-layout")))
    except:
        print(" 검색 결과 로딩 실패 (또는 결과 없음)")
        return []

    collected_links = []
    scroll_attempts = 0
    
    while len(collected_links) < max_results:
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        results = soup.select('a[data-testid="result-title-a"]')
        if not results: results = soup.select('article h2 a') 

        found_new = False
        for res in results:
            link = res.get('href')
            if link and ("fmkorea.com" in link):
                if re.search(r"/\d+", link) or "document_srl" in link:
                    if link not in global_visited_set:
                        global_visited_set.add(link)
                        collected_links.append(link)
                        found_new = True
                        if len(collected_links) >= max_results: break
        
        if len(collected_links) >= max_results: break
            
        if not found_new: scroll_attempts += 1
        else: scroll_attempts = 0
        
        if scroll_attempts >= 5: break

        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(random.uniform(0.6, 1.1)) 
        
        try:
            more_btn = driver.find_element(By.ID, "more-results")
            if more_btn.is_displayed():
                driver.execute_script("arguments[0].click();", more_btn)
                time.sleep(1.0)
        except: pass
            
    return collected_links[:max_results]

# 입력 데이터에서 필요한 게시글 데이터 정보를 추출한다.
def extract_post_data(driver, url):
    try:
        driver.get(url)
        config = SITE_CONFIG["fmkorea.com"]

        try:
            WebDriverWait(driver, 6).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, config["wait_selector"]))
            )
        except Exception:
            return None

        time.sleep(0.5)
        try: driver.execute_script("window.stop();")
        except: pass

        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(0.3)

        soup = BeautifulSoup(driver.page_source, "html.parser")

        title_elem = soup.select_one(config["title"]) or soup.select_one(".np_18px")
        title_text = title_elem.get_text(strip=True) if title_elem else "제목없음"

        writer_text = "Unknown"
        writer_elem = soup.select_one(config["writer_info"])
        if writer_elem:
            writer_text = writer_elem.get_text(strip=True)
        else:
            fallback = soup.select_one(".btm_area .side .nick")
            if fallback:
                writer_text = fallback.get_text(strip=True)

        date_elem = soup.select_one(config["date"])
        raw_date = date_elem.get_text(strip=True) if date_elem else "(작성일 없음)"
        formatted_date = format_date_to_yymmdd(raw_date)

        views_text = "0"
        info_bar = soup.select_one(config["info_bar"])
        if info_bar:
            raw_txt = info_bar.get_text(separator=" ", strip=True)
            match = re.search(r"조회\s*수?\s*([\d,]+)", raw_txt)
            if match:
                views_text = match.group(1).replace(",", "")

        body_elem = soup.select_one(config["body"])
        body_text = (
            body_elem.get_text(separator="\n", strip=True) if body_elem else ""
        )

        comments_list = []
        cmt_rows = soup.select(config["comment_wrapper"])
        for row in cmt_rows:
            classes = row.get("class", [])
            is_reply = "re" in classes or row.select_one(".fa-share")
            c_writer_elem = row.select_one(config["comment_writer"])
            c_nick = (
                c_writer_elem.get_text(strip=True) if c_writer_elem else "ㅇㅇ"
            )
            c_txt_elem = row.select_one(config["comment_text"])
            if c_txt_elem:
                c_content = c_txt_elem.get_text(separator=" ", strip=True)
                c_content = re.sub(r"\s+", " ", c_content).strip()
                

                if is_reply:
                    full_comment = f'ㄴ[답글][{c_nick}]:"{c_content}"'
                else:
                    full_comment = f'[댓글][{c_nick}]:"{c_content}"'
                comments_list.append(full_comment)

        return {
            "title": title_text,
            "author": writer_text,
            "formatted_date": formatted_date,
            "views": views_text,
            "body": body_text,
            "comments": comments_list,
        }

    except Exception:
        return None

# 처리된 final 데이터를 파일이나 저장소에 기록한다.
def save_final_format(keyword, idx, data, url, save_dir):
    hash_val = hashlib.md5(f"FMK_{url}".encode('utf-8')).hexdigest()[:16]
    
    file_path = save_dir / f"FMK_Duck_{hash_val}.txt"
    
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(f'URL:"{url}"\n')
        f.write(f'Hash:"{hash_val}"\n')
        f.write(f'작성일:{data["formatted_date"]}\n')
        f.write(f'제목:{data["title"]}\n')
        f.write(f'작성자:{data["author"]}\n')
        f.write(f'조회수:{data["views"]}\n\n')
        f.write("[본문 내용]\n")
        f.write(f"{data['body']}\n\n")
        f.write("[댓글 목록]\n")
        
        if data['comments']:
            for cmt in data['comments']:
                f.write(f"{cmt}\n")
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
                data = extract_post_data(driver, url)
                
                if data:

                    if data["formatted_date"] < cutoff_date:
                        print(f"   ->  [필터링] 출시일 이전 글 ({data['formatted_date']}) 제외")
                        continue

                    save_final_format(target_keyword, current_file_idx, data, url, save_dir)
                    current_file_idx += 1
                else:
                    print("   ->  내용 없음/삭제됨 (PASS)")
                
                time.sleep(random.uniform(0.3, 0.8))

    except KeyboardInterrupt:
        print("\n 사용자 중단")
    finally:
        print(f"\n 전체 작업 종료! 총 {current_file_idx - 1}개 파일 저장됨.")
        try: driver.quit()
        except: pass

# 관련 duck 크롤링 작업을 실행하고 필요한 후속 처리를 수행한다.
def run_duck_crawling(keyword, cutoff_date="00-00-00", save_dir=None, visited_urls=None):
    print(f"--- [FMK-Duck] '{keyword}' 우회 수집 시작 ---")
    if save_dir is None:
        save_dir = Path(__file__).parent / "data_storage" / keyword / "fmk"
        save_dir.mkdir(parents=True, exist_ok=True)
    

    main(keyword, cutoff_date, save_dir, visited_urls)

if __name__ == "__main__":
    run_duck_crawling("테스트")