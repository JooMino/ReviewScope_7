

import sys
import subprocess
import time
import argparse
from pathlib import Path


BASE_DIR = Path(__file__).parent




# 사용 중인 kill existing processes 연결이나 프로세스를 종료한다.
def kill_existing_processes():
    print("\n 기존 Ollama 프로세스 정리 중...")
    for proc in ["ollama.exe", "ollama_llama_server.exe"]:
        try:
            subprocess.run(["taskkill", "/IM", proc, "/F"], capture_output=True)
        except:
            pass
    time.sleep(1)
    print(" 정리 완료\n")


# 필요한 Ollama running 조건을 확인하고 실행 가능 상태를 보장한다.
def check_ollama_running() -> bool:
    import urllib.request
    try:
        urllib.request.urlopen("http://127.0.0.1:11434", timeout=2)
        return True
    except:
        return False


# 필요한 Ollama 조건을 확인하고 실행 가능 상태를 보장한다.
def ensure_ollama():
    """Ollama가 꺼져 있으면 새 창에서 켜고 대기"""
    if check_ollama_running():
        print(" Ollama 이미 실행 중")
        return
    print(" Ollama 시작 중...")
    subprocess.Popen(
        ["cmd", "/k", "ollama serve"],
        creationflags=subprocess.CREATE_NEW_CONSOLE
    )

    for _ in range(15):
        time.sleep(1)
        if check_ollama_running():
            print(" Ollama 준비 완료\n")
            return
    print(" Ollama 시작 실패! 수동으로 실행 후 다시 시도하세요.")
    sys.exit(1)





# 관련 mode 1 작업을 실행하고 필요한 후속 처리를 수행한다.
def run_mode_1(keyword=None):
    """LLM 분석기만 실행 (pro_analyzer_test.py 단독)"""
    script = BASE_DIR / "pro_analyzer_test.py"
    if not script.exists():
        print(f" 파일 없음: {script}")
        return

    ensure_ollama()

    if not keyword:
        keyword = input(" 감시할 키워드를 입력하세요: ").strip()
    if not keyword:
        print(" 키워드 없음")
        return

    print(f"\n[{keyword}] LLM 분석기 실행 중...")
    subprocess.Popen(
        ["powershell", "-NoExit", "-Command",
         f"cd '{BASE_DIR}'; python pro_analyzer_test.py --keyword \"{keyword}\""],
        creationflags=subprocess.CREATE_NEW_CONSOLE
    )
    print(" LLM 분석기 창 열림")


# 관련 mode 2 작업을 실행하고 필요한 후속 처리를 수행한다.
def run_mode_2(keyword=None, sites=None):
    """크롤러만 실행 (main.py 단독)"""
    script = BASE_DIR / "main.py"
    if not script.exists():
        print(f" 파일 없음: {script}")
        return

    if not keyword:
        keyword = input(" 검색 키워드를 입력하세요: ").strip()
    if not keyword:
        print(" 키워드 없음")
        return

    sites_str = f'--sites "{sites}"' if sites else ""
    cmd = f"cd '{BASE_DIR}'; python main.py --keyword \"{keyword}\" {sites_str} --no-wait"

    print(f"\n  [{keyword}] 크롤러 실행 중...")
    subprocess.Popen(
        ["powershell", "-NoExit", "-Command", cmd],
        creationflags=subprocess.CREATE_NEW_CONSOLE
    )
    print(" 크롤러 창 열림")


# 관련 mode 3 작업을 실행하고 필요한 후속 처리를 수행한다.
def run_mode_3(keyword=None):
    """Gemini 최종 분석기만 실행 (gemini_analyzer.py 단독)"""
    script = BASE_DIR / "gemini_analyzer.py"
    if not script.exists():
        print(f" 파일 없음: {script}")
        return

    if not keyword:
        keyword = input(" 분석할 키워드를 입력하세요: ").strip()
    if not keyword:
        print(" 키워드 없음")
        return

    print(f"\n [{keyword}] Gemini 분석기 실행 중...")
    subprocess.Popen(
        ["powershell", "-NoExit", "-Command",
         f"cd '{BASE_DIR}'; python gemini_analyzer.py --keyword \"{keyword}\""],
        creationflags=subprocess.CREATE_NEW_CONSOLE
    )
    print(" Gemini 분석기 창 열림")


# 관련 mode 4 작업을 실행하고 필요한 후속 처리를 수행한다.
def run_mode_4(keyword=None, sites=None, headless=False):
    """
    전체 파이프라인 실행 (pipeline_pro.py 위임)
    크롤링 → LLM → Gemini 까지 pipeline_pro.py 하나가 처리
    """
    script = BASE_DIR / "pipeline_pro.py"
    if not script.exists():
        print(f" 파일 없음: {script}")
        return

    cmd_parts = [sys.executable, str(script)]
    if keyword:
        cmd_parts += ["--keyword", keyword]
    if sites:
        cmd_parts += ["--sites", sites]

    print(f"\n 전체 파이프라인 실행: [{keyword or '(키워드 입력 대기)'}]")

    if headless:

        result = subprocess.run(cmd_parts, cwd=str(BASE_DIR))
        if result.returncode == 0:
            print(" 파이프라인 완료")
        else:
            print(f" 파이프라인 실패 (returncode={result.returncode})")
            sys.exit(result.returncode)
    else:

        ps_cmd = " ".join([f'"{p}"' if " " in p else p for p in cmd_parts])
        subprocess.Popen(
            ["powershell", "-NoExit", "-Command",
             f"cd '{BASE_DIR}'; {ps_cmd}"],
            creationflags=subprocess.CREATE_NEW_CONSOLE
        )
        print(" 파이프라인 창 열림")




# 스크립트의 주요 실행 흐름을 시작하고 전체 작업을 조율한다.
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--choice",   type=str, choices=["1","2","3","4"])
    ap.add_argument("--keyword",  type=str)
    ap.add_argument("--sites",    type=str, default="dc")
    ap.add_argument("--headless", action="store_true")
    args = ap.parse_args()

    print("=" * 60)
    print("  ReviewScope 올인원 런처")
    print("=" * 60)

    if args.choice:
        choice = args.choice
        print(f" 자동 모드: 옵션 {choice}")
    else:
        print("\n[실행할 작업 선택]")
        print("  1. LLM 분석기만 실행")
        print("  2. 크롤러만 실행")
        print("  3. Gemini 최종 분석기만 실행")
        print("  4. 전체 파이프라인 실행 (권장)")
        print("=" * 60)
        choice = input("\n번호를 입력하세요 (1/2/3/4): ").strip()

    if   choice == "1":
        run_mode_1(keyword=args.keyword)

    elif choice == "2":
        run_mode_2(keyword=args.keyword, sites=args.sites)

    elif choice == "3":
        run_mode_3(keyword=args.keyword)

    elif choice == "4":
        run_mode_4(keyword=args.keyword, sites=args.sites, headless=args.headless)

    else:
        print(" 잘못된 선택")
        return

    if not args.headless:
        input("\n종료하려면 Enter...")


if __name__ == "__main__":
    main()
