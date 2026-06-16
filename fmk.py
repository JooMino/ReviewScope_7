import time
import os
import sys
from pathlib import Path


sys.path.append(os.path.dirname(os.path.abspath(__file__)))

try:
    import fmk_duck_source
except ImportError as e:
    print(f" 모듈 임포트 오류: {e}")
    print("   -> 폴더 안에 'fmk_duck_source.py'가 있는지 확인하세요.")
    input("엔터 키를 누르면 종료합니다...")
    sys.exit()

# 관련 fmk 작업을 실행하고 필요한 후속 처리를 수행한다.
def run_fmk(keyword, cutoff_date="00-00-00"):
    print(f"\n===== [FMK 모듈] '{keyword}' 작업 시작 =====")
    print(f" 필터링 기준일: {cutoff_date}")

    base_dir = Path(__file__).parent
    save_dir = base_dir / "data_storage" / keyword / "fmk"
    save_dir.mkdir(parents=True, exist_ok=True)
    print(f" FMK 저장 폴더: {save_dir}")

    visited_urls = set()

    print("\n-------------------------------------------")
    print("⏳ FMK Direct 비활성화. 우회(Duck) 실행...")
    print("-------------------------------------------\n")

    try:
        fmk_duck_source.run_duck_crawling(keyword, cutoff_date, save_dir, visited_urls)
    except Exception as e:
        print(f" [Duck] 실행 중 오류 발생: {e}")

    print(f"===== [FMK 모듈] '{keyword}' 작업 완료 =====\n")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        keyword = sys.argv[1]
        cutoff_date = sys.argv[2] if len(sys.argv) > 2 else "00-00-00"
        run_fmk(keyword, cutoff_date)
    else:
        keyword = input("검색할 키워드를 입력하세요: ")
        run_fmk(keyword)
            
    print("\n[안내] 작업이 종료되었습니다.")
    print("⏳ 3초 후 창이 자동으로 닫히고 메인 프로그램으로 돌아갑니다...")
    time.sleep(3)
    sys.exit(0)
