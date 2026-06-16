import time
import os
import sys
from pathlib import Path


sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import dc_direct_source
import dc_duck_source

# 관련 dc 작업을 실행하고 필요한 후속 처리를 수행한다.
def run_dc(keyword, cutoff_date="00-00-00"):
    print(f"\n===== [DC 모듈] '{keyword}' 작업 시작 =====")
    print(f" 필터링 기준일: {cutoff_date}")

    base_dir = Path(__file__).parent
    save_dir = base_dir / "data_storage" / keyword / "dc"
    save_dir.mkdir(parents=True, exist_ok=True)
    print(f" DC 저장 폴더: {save_dir}")

    visited_urls = set()

    try:

        visited_urls = dc_direct_source.run_direct_crawling(keyword, cutoff_date, save_dir)
        if visited_urls is None: visited_urls = set()
        print(f" Direct 수집 URL 갯수: {len(visited_urls)}개")
    except Exception as e:
        print(f" [Direct] 실행 중 오류 발생: {e}")

    print("\n-------------------------------------------")
    print("⏳ Direct 종료. 5초 후 우회(Duck) 크롤링 시작...")
    print("-------------------------------------------\n")
    time.sleep(5) 

    try:

        dc_duck_source.run_duck_crawling(keyword, cutoff_date, save_dir, visited_urls)
    except Exception as e:
        print(f" [Duck] 실행 중 오류 발생: {e}")

    print(f"===== [DC 모듈] '{keyword}' 작업 완료 =====\n")

if __name__ == "__main__":

    if len(sys.argv) > 1:
        keyword = sys.argv[1]

        cutoff_date = sys.argv[2] if len(sys.argv) > 2 else "00-00-00"
        run_dc(keyword, cutoff_date)
    else:

        keyword = input("검색할 키워드를 입력하세요: ").strip()
        run_dc(keyword)
        
    print("\n[안내] 작업이 종료되었습니다.")
    print("⏳ 3초 후 창이 자동으로 닫히고 메인 런처로 돌아갑니다...")
    time.sleep(3)
    sys.exit(0)