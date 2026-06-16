

import sys
import os
import subprocess
import json
import shutil
import datetime
import asyncio
import re
import difflib
import time
import argparse
from urllib.parse import parse_qs, quote, urlparse
from pathlib import Path
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
from bs4 import BeautifulSoup


MENTION_SITES = set()

DEEPSEARCH_CRAWLERS = {
    "모니터": "monitor_crawl.py",
    "마우스": "mouse_crawl.py",
    "키보드": "keyboard_crawl.py",
    "노트북": "notebook_crawl.py",
    "모바일": "mobile_crawl.py",
    "태블릿pc": "tablet_crawl.py",
    "카메라": "camera_crawl.py",
    "pc부품": "pc_parts_crawl.py",
    "헤드폰/이어폰": "audio_crawl.py",
}

CATEGORY_ALIASES = {
    "monitor": "모니터",
    "mouse": "마우스",
    "keyboard": "키보드",
    "notebook": "노트북",
    "laptop": "노트북",
    "mobile": "모바일",
    "phone": "모바일",
    "tablet": "태블릿pc",
    "tabletpc": "태블릿pc",
    "camera": "카메라",
    "pc": "pc부품",
    "pcparts": "pc부품",
    "audio": "헤드폰/이어폰",
    "headphone": "헤드폰/이어폰",
    "headphones": "헤드폰/이어폰",
    "earphone": "헤드폰/이어폰",
    "earphones": "헤드폰/이어폰",
    "음향": "헤드폰/이어폰",
    "헤드폰": "헤드폰/이어폰",
    "이어폰": "헤드폰/이어폰",
}


# 게시글 URL에서 고유 식별자를 뽑아 중복 제거에 사용할 키를 만든다.
def get_post_dedupe_key(url: str):
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path

    if "dcinside.com" in host:
        post_no = parse_qs(parsed.query).get("no", [""])[0]
        return f"dc:{post_no}" if post_no.isdigit() else None

    if "fmkorea.com" in host:
        match = re.search(r"^/(\d+)(?:/|$)", path)
        return f"fmk:{match.group(1)}" if match else None

    if "quasarzone.com" in host:
        match = re.search(r"/views/(\d+)(?:/|$)", path)
        return f"quasar:{match.group(1)}" if match else None

    if "clien.net" in host:
        match = re.search(r"/(\d+)(?:/|$)", path)
        return f"clien:{match.group(1)}" if match else None

    return None






ap = argparse.ArgumentParser()
ap.add_argument("--keyword",  type=str,  default=None,  help="검색 키워드")
ap.add_argument("--sites",    type=str,  default=None,  help="크롤링 사이트 (쉼표 구분, 예: dc,clien,fmk,quasar)")
ap.add_argument("--category", type=str,  default="",    help="딥서치 카테고리")
ap.add_argument("--headless", action="store_true",      help="대화형 입력 없이 자동 실행")
ap.add_argument("--no-wait",  action="store_true",      help="종료 시 Enter 대기 생략")
args = ap.parse_args()


# 필요한 언급량 프로세스나 작업을 시작한다.
def start_mention_process(keyword, site, current_dir):
    script_path = os.path.join(current_dir, "mention_run.py")
    cmd = [sys.executable, script_path, "--keyword", keyword, "--site", site]

    if sys.platform == "win32":
        return subprocess.Popen(cmd, creationflags=subprocess.CREATE_NEW_CONSOLE)
    return subprocess.Popen(cmd)


# 입력된 카테고리 값을 비교와 저장에 적합한 표준 형태로 맞춘다.
def normalize_category(category: str) -> str:
    cleaned = (category or "").strip()
    if not cleaned or cleaned in {"없음", "none", "None", "null", "-"}:
        return ""
    compact = cleaned.replace(" ", "").lower()
    if cleaned in DEEPSEARCH_CRAWLERS:
        return cleaned
    return CATEGORY_ALIASES.get(compact, cleaned)


# 관련 deepsearch 크롤러 작업을 실행하고 필요한 후속 처리를 수행한다.
def run_deepsearch_crawler(keyword: str, cutoff_date: str, category: str, base_dir: Path, keyword_storage: Path) -> None:
    normalized = normalize_category(category)
    if not normalized:
        print("\n딥서치 카테고리가 없어 deepSearch 실행을 건너뜁니다.")
        return

    script_name = DEEPSEARCH_CRAWLERS.get(normalized)
    if not script_name:
        print(f"\n지원하지 않는 카테고리라 deepSearch 실행을 건너뜁니다: {category}")
        return

    deepsearch_dir = base_dir / "deepSearch_source"
    script_path = deepsearch_dir / script_name
    if not script_path.exists():
        print(f"\ndeepSearch 크롤러 파일을 찾지 못했습니다: {script_path}")
        return

    storage_root = keyword_storage / "deepSearch"
    storage_root.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, str(script_path), keyword, cutoff_date, str(storage_root)]

    print(f"\ndeepSearch 실행: category={normalized} / file={script_name}")
    if sys.platform == "win32":
        process = subprocess.Popen(cmd, cwd=str(deepsearch_dir), creationflags=subprocess.CREATE_NEW_CONSOLE)
    else:
        process = subprocess.Popen(cmd, cwd=str(deepsearch_dir))
    process.wait()
    print(f"deepSearch 종료: {script_name}")




# 외부 페이지나 API에서 필요한 danawa HTML 데이터를 가져온다.
async def fetch_danawa_html(keyword: str) -> str:
    encoded_keyword = quote(keyword)
    url = f"https://search.danawa.com/dsearch.php?k1={encoded_keyword}&module=goods&act=dispMain"
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded")
            await page.wait_for_selector("div.prod_info", timeout=7000)
            return await page.content()
        except Exception:
            return ""
        finally:
            await browser.close()


# 입력 데이터에서 필요한 후보 제품 정보를 추출한다.
def extract_candidate_products(html_source: str) -> list:
    if not html_source: return []
    soup = BeautifulSoup(html_source, "html.parser")
    extracted_data = []
    for block in soup.select("div.prod_info"):
        if block.find_parent(class_=re.compile("powershopping")) or block.parent.select_one(".icon__ad"):
            continue

        main_info = block.find_parent("div", class_="prod_main_info")
        if not main_info or not main_info.select_one(".chk_sect"):
            continue

        name_elem = block.select_one("p.prod_name")
        date_elem = block.select_one("span.mt_date")
        if name_elem and date_elem:
            date_match = re.search(r"(\d{2}\.\d{2})", date_elem.get_text())
            if date_match:
                extracted_data.append({"name": name_elem.get_text(strip=True), "date": date_match.group(1)})

        if len(extracted_data) >= 5: break
    return extracted_data


# get_best_match 작업에 필요한 핵심 처리를 수행한다.
def get_best_match(keyword: str, product_list: list) -> dict:
    if not product_list: return None
    best_item, highest_ratio = None, -1.0
    for item in product_list:
        similarity = difflib.SequenceMatcher(None, keyword.lower(), item["name"].lower()).ratio()
        if keyword.lower() in item["name"].lower(): similarity += 0.5
        similarity -= (len(item["name"]) * 0.001)
        if similarity > highest_ratio:
            highest_ratio, best_item = similarity, item
    return best_item


# 날짜와 시간 기준으로 calculate 기준일 날짜 값을 계산하거나 변환한다.
def calculate_cutoff_date(release_date_str: str) -> str:
    try:
        year = int("20" + release_date_str.split('.')[0])
        month = int(release_date_str.split('.')[1])

        if month == 1:
            year -= 1
            month = 12
        else:
            month -= 1

        return f"{str(year)[2:]}-{month:02d}-01"
    except Exception:
        return "00-00-00"




# 스크립트의 주요 실행 흐름을 시작하고 전체 작업을 조율한다.
def main():
    print("=========================================")
    print("       ReviewScope 통합 크롤링 런처      ")
    print("=========================================")


    if args.keyword:
        keyword = args.keyword.strip()
        print(f"\n 키워드 (자동): {keyword}")
    else:
        keyword = input("\n 통합 검색할 키워드를 입력하세요: ").strip()

    if not keyword:
        print("키워드가 입력되지 않았습니다. 종료합니다.")
        return


    cutoff_date = "00-00-00"
    if args.headless or args.keyword:
        print(" 자동 실행 모드: 출시일 필터링을 건너뜁니다.")
    else:
        use_release_date = input("\n 제품 출시일 필터링 기능을 사용하시겠습니까? (y/n): ").strip().lower()

        if use_release_date == 'y':
            print("⏳ 다나와에서 제품 정보를 분석 중입니다...")
            html = asyncio.run(fetch_danawa_html(keyword))

            candidates = extract_candidate_products(html)
            best = get_best_match(keyword, candidates)

            if best:
                cutoff_date = calculate_cutoff_date(best['date'])
                print(f" - 확인된 기기: {best['name']}")
                print(f" - 출시 연월: 20{best['date']}")
                print(f" - 필터링 기준: {cutoff_date} 이후 작성된 글만 수집합니다.")
            else:
                print(" 제품 정보를 찾지 못했습니다. 모든 날짜의 글을 수집합니다.")
        else:
            print(" 출시일 필터링을 건너뜁니다.")


    base_dir = Path(__file__).parent
    keyword_storage = base_dir / "data_storage" / keyword
    current_date = datetime.date.today()

    if keyword_storage.exists():
        existing_date_files = [f for f in keyword_storage.glob("*.txt") if f.name.startswith(f"[{keyword}]_")]
        if existing_date_files:
            for file_path in existing_date_files:
                try:
                    date_str = file_path.stem.rsplit('_', 1)[-1]
                    file_date = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
                    if (current_date - file_date).days <= 10:
                        print(f"\n[안내] 최근 10일 내 검색 결과가 존재합니다. 종료합니다.")
                        if not (args.headless or args.no_wait):
                            input("Enter를 눌러 종료...")
                        sys.exit(0)
                    else:
                        shutil.rmtree(keyword_storage); break
                except ValueError as e:

                    print(f"[경고] 날짜 마커 파일 파싱 실패 ({file_path.name}): {e}")

    keyword_storage.mkdir(parents=True, exist_ok=True)
    (keyword_storage / f"[{keyword}]_{current_date.strftime('%Y-%m-%d')}.txt").touch(exist_ok=True)


    targets = []

    if args.sites:
        mapping = {"dc": "dc", "clien": "clien", "fmk": "fmk", "quasar": "quasar"}
        for s in args.sites.replace(" ", "").split(","):
            if s in mapping:
                targets.append(mapping[s])
        print(f" 사이트 (자동): {', '.join(targets)}")
    else:
        print("\n[대상 사이트 선택] 1.DC  2.Clien  3.FMK  4.Quasar  9.전체")
        choice = input("번호 입력 (예: 1,2): ").strip()

        selected = choice.replace(" ", "").split(",")
        if "9" in selected:
            targets = ["dc", "clien", "fmk", "quasar"]
        else:
            mapping = {"1": "dc", "2": "clien", "3": "fmk", "4": "quasar"}
            for s in selected:
                if s in mapping:
                    targets.append(mapping[s])

    if not targets:
        print(" 선택된 사이트가 없습니다. 종료합니다.")
        return


    print(f"\n 크롤러 실행 중... (기준일: {cutoff_date})")
    current_dir = os.path.dirname(os.path.abspath(__file__))
    processes = []

    for site in targets:
        script_path = os.path.join(current_dir, f"{site}.py")
        if not os.path.exists(script_path): continue

        if sys.platform == "win32":
            p = subprocess.Popen(
                [sys.executable, script_path, keyword, cutoff_date],
                creationflags=subprocess.CREATE_NEW_CONSOLE
            )
        else:
            p = subprocess.Popen([sys.executable, script_path, keyword, cutoff_date])

        processes.append((site, p))

    mention_processes = []
    running = dict(processes)

    while running:
        for site, p in list(running.items()):
            if p.poll() is None:
                continue

            print(f"\n[{site}] 크롤링 완료")
            if site in MENTION_SITES:
                print(f"[{site}] 언급량 집계 창 실행")
                mention_processes.append((site, start_mention_process(keyword, site, current_dir)))

            del running[site]

        time.sleep(1)

    run_deepsearch_crawler(keyword, cutoff_date, args.category, base_dir, keyword_storage)




    print("\n 데이터 통합 및 중복 게시글 필터링 중...")
    merged_data = {}
    seen_posts = set()
    trash_dir = keyword_storage / "trash"
    trash_dir.mkdir(parents=True, exist_ok=True)

    dupe_count = 0

    for txt_file in keyword_storage.rglob("*.txt"):
        if txt_file.name.startswith(f"[{keyword}]_") or "trash" in txt_file.parts: 
            continue

        try:
            with open(txt_file, "r", encoding="utf-8") as f:
                lines = [f.readline().strip() for _ in range(6)]

            if len(lines) >= 6 and lines[0].startswith("URL:") and lines[1].startswith("Hash:"):
                u = lines[0].replace("URL:", "").strip()
                h = lines[1].replace("Hash:", "").strip()

                post_key = get_post_dedupe_key(u)

                if post_key:
                    if post_key in seen_posts:
                        target_path = trash_dir / txt_file.name
                        if target_path.exists():
                            target_path.unlink()
                        shutil.move(str(txt_file), str(target_path))
                        dupe_count += 1
                        continue
                    seen_posts.add(post_key)

                merged_data[h] = u
        except Exception:
            pass

    with open(keyword_storage / f"{keyword}_dict.json", "w", encoding="utf-8") as f:
        json.dump(merged_data, f, ensure_ascii=False, indent=4)

    for site, p in mention_processes:
        p.wait()
        print(f"[{site}] 언급량 집계 프로세스 종료")

    print(f" 데이터 통합 완료 (총 {len(merged_data)}개 수집됨)")
    if dupe_count > 0:
        print(f" 중복으로 판단되어 휴지통으로 이동된 파일: {dupe_count}개")


    crawler_done_marker = keyword_storage / ".crawler_done"
    crawler_done_marker.touch(exist_ok=True)
    print(" 실시간 분석기 통보 마커(.crawler_done) 생성 완료.")

    print("\n[안내] 모든 작업이 완료되었습니다.")

    if not (args.no_wait or args.headless):
        input("Enter를 눌러 창을 닫으세요...")



if __name__ == "__main__":
    main()
