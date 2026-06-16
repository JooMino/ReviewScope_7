
"""
전체 파이프라인 실행기 (단일 터미널 통합 버전)
"""
import sys
import os
import subprocess
import argparse
import time
import urllib.request
from pathlib import Path

BASE_DIR     = Path(__file__).parent
LLM_SERVER   = BASE_DIR / "pro_analyzer_test.py"
CRAWLER_MAIN = BASE_DIR / "main.py"




# 필요한 Ollama running 조건을 확인하고 실행 가능 상태를 보장한다.
def check_ollama_running() -> bool:
    try:
        urllib.request.urlopen("http://127.0.0.1:11434", timeout=2)
        return True
    except Exception:
        return False


# 필요한 Ollama 조건을 확인하고 실행 가능 상태를 보장한다.
def ensure_ollama():
    """Ollama가 꺼져 있으면 새 창에서 자동으로 켜고 준비될 때까지 대기"""
    print("[STEP 0] Ollama 상태 확인...")
    if check_ollama_running():
        print("   Ollama 서버 이미 실행 중.")
        return

    print("   Ollama가 꺼져 있습니다. 자동으로 시작합니다...")
    subprocess.Popen(
        ["cmd", "/k", "ollama serve"],
        creationflags=subprocess.CREATE_NEW_CONSOLE
    )

    for i in range(20):
        time.sleep(1)
        if check_ollama_running():
            print("   Ollama 준비 완료.\n")
            return
        print(f"   대기 중... ({i + 1}/20초)")

    print("\nOllama 자동 시작에 실패했습니다. 수동으로 실행 후 다시 시도하세요.")
    sys.exit(1)




# 필요한 LLM 워커 프로세스나 작업을 시작한다.
def start_llm_worker(idle_time, workers):
    """LLM 분석기를 새 창에서 실행하고 프로세스 객체를 반환"""
    cmd = [
        sys.executable, str(LLM_SERVER),
        "--idle",    str(idle_time),
        "--workers", str(workers)
    ]

    return subprocess.Popen(cmd, cwd=str(BASE_DIR), creationflags=subprocess.CREATE_NEW_CONSOLE)




# 스크립트의 주요 실행 흐름을 시작하고 전체 작업을 조율한다.
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keyword", default=None, type=str)
    ap.add_argument("--sites",   default="dc", type=str)
    ap.add_argument("--category", default="",   type=str)
    ap.add_argument("--workers", default=1,    type=int)
    ap.add_argument("--idle",    default=600,  type=int)
    ap.add_argument("--llm-only", action="store_true")
    args = ap.parse_args()

    if args.keyword:
        keyword = args.keyword.strip()
        sites   = args.sites
    else:
        keyword = input("\n 검색 키워드를 입력하세요: ").strip()
        if not keyword: return
        sites = "dc"

    print("\n" + "=" * 60)
    print(f" ReviewScope 파이프라인 (통합 모드)")
    print(f"  키워드: [{keyword}]")
    print("=" * 60 + "\n")


    ensure_ollama()


    done_marker    = BASE_DIR / "data_storage" / keyword / f"{keyword}.done"
    crawler_marker = BASE_DIR / "data_storage" / keyword / ".crawler_done"

    if done_marker.exists():    done_marker.unlink()
    if crawler_marker.exists(): crawler_marker.unlink()
    if args.llm_only:
        target_folder = BASE_DIR / "data_storage" / keyword
        if not target_folder.exists():
            print(f"LLM-only mode requires existing crawled data: {target_folder}")
            sys.exit(1)
        for name in [
            f"{keyword}_result.csv",
            f"{keyword}_result.json",
            ".llm_start_time",
            ".llm_end_time",
        ]:
            stale_file = target_folder / name
            if stale_file.exists():
                stale_file.unlink()


    print("[STEP 1] LLM 분석기 새 창 실행...")
    llm_proc = start_llm_worker(args.idle, args.workers)
    print(f"   LLM 분석기 시작됨 (PID: {llm_proc.pid})")
    time.sleep(3)


    if args.llm_only:
        print("[STEP 2] LLM-only mode: crawler skipped, existing txt files will be analyzed.")
    else:
        print("[STEP 2] 크롤러 시작...")
        crawler_cmd = [
            sys.executable, str(CRAWLER_MAIN),
            "--keyword", keyword,
            "--sites",   sites,
            "--no-wait",
        ]
        if args.category.strip():
            crawler_cmd += ["--category", args.category.strip()]

        crawler_proc = subprocess.run(crawler_cmd, cwd=str(BASE_DIR))

        if crawler_proc.returncode != 0:
            print(f"\n크롤러 비정상 종료. 파이프라인을 중단합니다.")
            llm_proc.terminate()
            sys.exit(1)

        print("\n[STEP 2] 크롤링 완료!")


    target_folder = BASE_DIR / "data_storage" / keyword
    target_folder.mkdir(parents=True, exist_ok=True)
    (target_folder / ".crawler_done").touch()

    print("[STEP 3] LLM 남은 작업 처리 및 Gemini 실행 중...\n")


    llm_proc.wait()

    print("\n" + "=" * 60)
    print(f"   파이프라인 완료: [{keyword}]")
    print(f"   결과 위치: data_storage/{keyword}/")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
