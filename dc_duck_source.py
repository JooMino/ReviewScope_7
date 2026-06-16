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
    "dcinside.com": {
        "wait_selector": ".write_div",        
        "title": ".title_subject",            
        "body": ".write_div",                 
        "writer_box": ".gall_writer",         
        "views": ".gall_count",               
        "date": ".gall_date",                 
        "cmt_date": ".date_time",             
        "comment_list_wrapper": ".cmt_list > li" 
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

# 지정된 조건으로 search DuckDuckGo URL 검색을 수행하고 결과를 수집한다.
def search_duckduckgo_urls(driver, search_query, max_results, global_visited_set):
    full_query = f"{search_query} site:dcinside.com"
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
            if link and ("dcinside.com" in link) and ("board/view" in link or "/board/" in link):
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

# is_smart_match 작업에 필요한 핵심 처리를 수행한다.
def is_smart_match(keyword, text):
    if not text: return False
    k, t = keyword.replace(" ", "").lower(), text.replace(" ", "").lower()
    return k in t if len(k) < 3 else any(k[i:i+3] in t for i in range(len(k)-2))

# 입력 데이터에서 필요한 본문 detailed 정보를 추출한다.
def extract_content_detailed(driver, url):
    try:
        driver.get(url)
        config = SITE_CONFIG["dcinside.com"]
        
        try: WebDriverWait(driver, 6).until(EC.presence_of_element_located((By.CSS_SELECTOR, config["wait_selector"])))
        except: return None
        
        time.sleep(0.5)
        try: driver.execute_script("window.stop();")
        except: pass
        
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(0.3)
        
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        
        title = soup.select_one(config["title"])
        title_text = title.get_text(strip=True) if title else ""
        
        body_elem = soup.select_one(config["body"])
        body_text = body_elem.get_text(separator="\n", strip=True) if body_elem else ""

        date_tag = soup.select_one(config["date"])
        raw_date = date_tag.get("title") or date_tag.get_text(strip=True) if date_tag else "날짜불명"
        formatted_date = format_date_to_yymmdd(raw_date)

        post_meta = {"nick": "Unknown", "id_info": ""}
        writer_box = soup.select_one(config["writer_box"])
        if writer_box:
            nick_tag = writer_box.select_one(".nickname")
            ip_tag = writer_box.select_one(".ip")
            uid = writer_box.get("data-uid")

            if nick_tag: 
                raw_nick = nick_tag.get_text(strip=True)
                if ip_tag:
                    ip_txt = ip_tag.get_text(strip=True)
                    if ip_txt in raw_nick: raw_nick = raw_nick.replace(ip_txt, "").strip()
                post_meta["nick"] = raw_nick
            elif writer_box.get("data-nick"):
                post_meta["nick"] = writer_box.get("data-nick")

            if uid: post_meta["id_info"] = uid
            elif ip_tag: post_meta["id_info"] = ip_tag.get_text(strip=True).strip("()")

        views_tag = soup.select_one(config["views"])
        views_text = views_tag.get_text(strip=True) if views_tag else "0"
        
        comments_data = []
        for li in soup.select(config["comment_list_wrapper"]):
            if "no_data" in li.get("class", []): continue

            main_cmt_div = li.select_one(".cmt_info")
            if main_cmt_div:
                nick_el = main_cmt_div.select_one(".nickname")
                nick_str = nick_el.get_text(strip=True) if nick_el else "ㅇㅇ"
                if "댓글돌이" in nick_str: continue

                date_el = main_cmt_div.select_one(config["cmt_date"])
                cmt_date = date_el.get_text(strip=True) if date_el else ""

                txt_el = main_cmt_div.select_one(".usertxt")
                content = txt_el.get_text(separator=" ", strip=True) if txt_el else "이미지/삭제됨"
                
                comments_data.append({"is_reply": False, "nick": nick_str, "content": content, "date": cmt_date})

            for r_li in li.select(".reply_list li"):
                reply_div = r_li.select_one(".reply_info")
                if not reply_div: continue
                nick_el = reply_div.select_one(".nickname")
                nick_str = nick_el.get_text(strip=True) if nick_el else "ㅇㅇ"
                if "댓글돌이" in nick_str: continue
                
                date_el = reply_div.select_one(config["cmt_date"])
                cmt_date = date_el.get_text(strip=True) if date_el else ""

                txt_el = reply_div.select_one(".usertxt") or reply_div.select_one(".cmt_txtbox")
                content = txt_el.get_text(separator=" ", strip=True) if txt_el else "이미지/삭제됨"
                
                comments_data.append({"is_reply": True, "nick": nick_str, "content": content, "date": cmt_date})

        return {"title": title_text, "body": body_text, "author": post_meta, "formatted_date": formatted_date, "views": views_text, "comments": comments_data}
    except Exception:
        return None

# 처리된 final 데이터를 파일이나 저장소에 기록한다.
def save_final_format(keyword, idx, data, url, save_dir):
    hash_val = hashlib.md5(f"DC_{url}".encode('utf-8')).hexdigest()[:16]
    
    file_path = save_dir / f"DC_Duck_{hash_val}.txt"
    
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(f'URL:"{url}"\n')
        f.write(f'Hash:"{hash_val}"\n')
        f.write(f'작성일:{data["formatted_date"]}\n')
        f.write(f'제목:{data["title"]}\n')
        author = data['author']['nick']
        id_info = data['author']['id_info']
        if id_info and (id_info not in author): author += f" ({id_info})"
        
        f.write(f'작성자:{author}\n')
        f.write(f'조회수:{data["views"]}\n\n')
        f.write("[본문 내용]\n")
        f.write(f"{data['body']}\n\n")
        f.write("[댓글 목록]\n")
        
        if data['comments']:
            for cmt in data['comments']:
                if cmt['is_reply']:
                    f.write(f'ㄴ[답글][{cmt["nick"]}]:"{cmt["content"]}"\n')
                else:
                    f.write(f'[댓글][{cmt["nick"]}]:"{cmt["content"]}"\n')
        else:
            f.write("(댓글 없음)\n")
            
    print(f"    저장 완료: {file_path.name}")

# 스크립트의 주요 실행 흐름을 시작하고 전체 작업을 조율한다.
def main(target_keyword, cutoff_date, save_dir, visited_urls=None):
    driver = setup_driver()
    global_visited = set(visited_urls) if visited_urls else set()
    current_file_idx = 1
    
    suffixes = ["리뷰", "후기", "장단점"]
    
    try:
        for suffix in suffixes:
            search_query = f"{target_keyword} {suffix}".strip()
            print(f"\n" + "="*42)
            print(f" 검색 단계: [{search_query}] (최대 10개)")
            print("="*42)
            
            urls = search_duckduckgo_urls(driver, search_query, 5, global_visited)
            if not urls: continue
            
            consecutive_pass_count = 0
            for i, url in enumerate(urls, 1):
                if consecutive_pass_count >= 5:
                    print(f" [중단] 5회 연속 조건 불일치 -> '{suffix}' 단계 종료")
                    break
                
                print(f"[{i}/{len(urls)}] 접속: {url}")
                data = extract_content_detailed(driver, url)
                
                if not data:
                    print("   ->  내용 없음/삭제됨 (PASS)")
                    consecutive_pass_count += 1
                    continue

                if data['formatted_date'] < cutoff_date:
                    print(f"   ->  [필터링] 출시일 이전 글 ({data['formatted_date']}) 제외")
                    continue
                
                if is_smart_match(target_keyword, data['title']) or is_smart_match(target_keyword, data['body']):
                    save_final_format(target_keyword, current_file_idx, data, url, save_dir)
                    current_file_idx += 1
                    consecutive_pass_count = 0 
                else:
                    print(f"   ->  키워드 불일치 (PASS)")
                    consecutive_pass_count += 1
                
                time.sleep(random.uniform(0.3, 0.8))

    except KeyboardInterrupt:
        print("\n 사용자 중단")
    finally:
        print(f"\n 전체 작업 종료! 총 {current_file_idx - 1}개 파일 저장됨.")
        try: driver.quit()
        except: pass

# 관련 duck 크롤링 작업을 실행하고 필요한 후속 처리를 수행한다.
def run_duck_crawling(keyword, cutoff_date="00-00-00", save_dir=None, visited_urls=None):
    print(f"--- [Duck] '{keyword}' 우회 수집 시작 ---")
    if save_dir is None:
        save_dir = Path(__file__).parent / "data_storage" / keyword / "dc"
        save_dir.mkdir(parents=True, exist_ok=True)

    main(keyword, cutoff_date, save_dir, visited_urls)

if __name__ == "__main__":
    run_duck_crawling("테스트")