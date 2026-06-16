import argparse
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from result_uploader import (
    notify_worker_started,
    run_pipeline,
    upload_result,
    wait_for_result_file,
)


try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

try:
    from sshtunnel import SSHTunnelForwarder
except Exception:
    SSHTunnelForwarder = None


SSH_KEY_PATH = os.getenv(r"SSH_KEY_PATH")
REMOTE_HOST = os.getenv("REMOTE_HOST", "138.2.124.59")
REMOTE_USER = os.getenv("REMOTE_USER", "ubuntu")
MERGE_WORKER_PORT = int(os.getenv("MERGE_WORKER_PORT", "8090"))

DEFAULT_MERGE_WORKER_URL = os.getenv(
    "MERGE_WORKER_URL",
    f"http://{REMOTE_HOST}:{MERGE_WORKER_PORT}",
)
DEFAULT_SITES = os.getenv("DEFAULT_SITES", "dc")
ALL_SITES = "dc,clien,fmk,quasar"
DEFAULT_KEYWORDS_FILE = Path(__file__).resolve().with_name("keywords.txt")
LOCAL_TUNNEL_HOST = "127.0.0.1"
VALID_CATEGORIES = [
    "모니터",
    "마우스",
    "키보드",
    "노트북",
    "모바일",
    "태블릿pc",
    "카메라",
    "pc부품",
    "음향"
]


# prompt_category 작업에 필요한 핵심 처리를 수행한다.
def prompt_category(default_category: str = ""):
    print("\n카테고리 선택:")
    print("  0. 없음")
    for index, category in enumerate(VALID_CATEGORIES, start=1):
        print(f"  {index}. {category}")

    raw_value = input(f"번호 또는 카테고리명 [{default_category or '없음'}]: ").strip()
    if not raw_value:
        return default_category
    if raw_value == "0":
        return ""
    if raw_value.isdigit():
        index = int(raw_value)
        if 1 <= index <= len(VALID_CATEGORIES):
            return VALID_CATEGORIES[index - 1]
        print("지원하지 않는 번호라 카테고리 없이 진행합니다.")
        return ""
    return raw_value


# 원본 job line 데이터를 분석해 사용하기 쉬운 구조로 변환한다.
def parse_job_line(raw_value: str, default_category: str = ""):
    raw_value = raw_value.strip()
    if "," in raw_value:
        keyword, category = raw_value.split(",", 1)
        return {
            "keyword": keyword.strip(),
            "category": category.strip() or default_category,
        }
    return {"keyword": raw_value, "category": default_category}


# 필요한 jobs 데이터를 읽어 이후 처리에 사용할 형태로 준비한다.
def load_jobs(args):
    raw_jobs = []

    if args.keywords:
        raw_jobs.extend(args.keywords)

    if args.keywords_file:
        with open(args.keywords_file, "r", encoding="utf-8") as f:
            raw_jobs.extend(line.strip() for line in f)

    cleaned = []
    seen = set()
    for raw_job in raw_jobs:
        raw_job = raw_job.strip()
        if not raw_job or raw_job.startswith("#"):
            continue
        job = parse_job_line(raw_job, args.category.strip())
        keyword = job["keyword"]
        category = job["category"]
        if not keyword:
            continue
        key = (keyword, category)
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(job)

    return cleaned


# 나뉘어 있는 워커 데이터를 병합해 최종 결과를 만든다.
def check_merge_worker(merge_worker_url: str):
    url = merge_worker_url.rstrip("/") + "/health"
    with urllib.request.urlopen(url, timeout=5) as res:
        body = res.read().decode("utf-8", errors="replace")
        print(f"[Batch Upload] merge worker health: {res.status} {body}")


# 필요한 ssh 터널 프로세스나 작업을 시작한다.
def start_ssh_tunnel(local_port: int, remote_port: int):
    if SSHTunnelForwarder is None:
        raise RuntimeError("sshtunnel 패키지를 찾을 수 없습니다. pip install sshtunnel 후 다시 실행하세요.")
    if not REMOTE_HOST:
        raise ValueError("REMOTE_HOST is empty")
    if not SSH_KEY_PATH:
        raise ValueError("SSH_KEY_PATH is empty")

    key_path = SSH_KEY_PATH.strip().strip('"').strip("'")
    print(
        f"[Batch Upload] SSH tunnel start: 127.0.0.1:{local_port} "
        f"-> {REMOTE_USER}@{REMOTE_HOST}:127.0.0.1:{remote_port}"
    )

    tunnel = SSHTunnelForwarder(
        (REMOTE_HOST, 22),
        ssh_username=REMOTE_USER,
        ssh_pkey=key_path,
        remote_bind_address=("127.0.0.1", remote_port),
        local_bind_address=(LOCAL_TUNNEL_HOST, local_port),
    )
    tunnel.start()

    tunnel_url = f"http://{LOCAL_TUNNEL_HOST}:{local_port}"
    last_error = ""
    for _ in range(10):
        try:
            check_merge_worker(tunnel_url)
            return tunnel
        except Exception as exc:
            last_error = str(exc)
            time.sleep(1)

    tunnel.stop()
    raise RuntimeError(f"SSH tunnel opened, but merge worker health check failed: {last_error}")


# process_keyword 작업에 필요한 핵심 처리를 수행한다.
def process_keyword(keyword: str, category: str, sites: str, merge_worker_url: str, worker_name: str, wait_timeout: int, llm_only: bool = False):
    print("\n" + "=" * 70)
    print(f"[Batch Upload] keyword={keyword} | category={category or '-'} | sites={sites}")
    print("=" * 70)

    status, text = notify_worker_started(keyword, sites, merge_worker_url, worker_name, category)
    print(f"[Batch Upload] started notified: status={status}, response={text}")

    run_pipeline(keyword, sites, category, llm_only)

    result_path = wait_for_result_file(keyword, wait_timeout)
    status, text = upload_result(keyword, merge_worker_url, result_path, category)
    print(f"[Batch Upload] result uploaded: status={status}, response={text}")
    return True


# 스크립트의 주요 실행 흐름을 시작하고 전체 작업을 조율한다.
def main():
    parser = argparse.ArgumentParser(
        description="Run ReviewScope result pipeline for keyword list and upload result JSONs to ubuntu merge worker."
    )
    parser.add_argument("keywords", nargs="*", help="Keywords to process in order.")
    parser.add_argument("--keywords-file", help="UTF-8 text file with one keyword or keyword,category per line.")
    parser.add_argument("--sites", default=DEFAULT_SITES, help="Comma-separated site list.")
    parser.add_argument("--category", default="", help="Category for all keywords unless a line uses keyword,category.")
    parser.add_argument("--merge-worker-url", help="Example: http://138.2.124.59:8090")
    parser.add_argument("--worker-name", default="friend-pc")
    parser.add_argument("--pause", type=float, default=2.0, help="Seconds to wait between keywords.")
    parser.add_argument("--wait-timeout", type=int, default=1800)
    parser.add_argument("--skip-health-check", action="store_true")
    parser.add_argument("--ssh-tunnel", action="store_true", help="Open SSH local tunnel to the merge worker.")
    parser.add_argument("--no-ssh-tunnel", action="store_true", help="Connect to MERGE_WORKER_URL directly.")
    parser.add_argument("--llm-only", action="store_true", help="Skip crawling and analyze existing data_storage files before upload.")
    args = parser.parse_args()

    if not args.keywords and not args.keywords_file:
        print("=" * 70)
        print(" ReviewScope result upload sender")
        print("=" * 70)
        print("  1. 단일 키워드")
        print("  2. 리스트 txt 파일")
        print("=" * 70)
        choice = input("실행 방식 선택 (1/2): ").strip()

        print("\n분석 방식:")
        print("  1. 전체 실행 (크롤링 + LLM + 업로드)")
        print("  2. LLM만 실행 (기존 크롤링 txt 사용 + 업로드)")
        mode_choice = input("분석 방식 선택 (1/2) [1]: ").strip()
        if mode_choice == "2":
            args.llm_only = True
        elif mode_choice not in ("", "1"):
            print("잘못된 선택입니다.")
            return

        if choice == "1":
            keyword = input("키워드: ").strip()
            if keyword:
                args.keywords = [keyword]
            args.category = prompt_category(args.category.strip())
        elif choice == "2":
            print(f"리스트 파일을 사용합니다: {DEFAULT_KEYWORDS_FILE}")
            print("파일 형식:")
            print("  헤사세,헤드폰/이어폰")
            print("  mr4,스피커")
            print("  갤럭시 s25u,모바일")
            print("  VXE R1 SE+,마우스")
            if not DEFAULT_KEYWORDS_FILE.exists() or not DEFAULT_KEYWORDS_FILE.is_file():
                print(f"파일을 찾을 수 없습니다: {DEFAULT_KEYWORDS_FILE}")
                return
            args.keywords_file = str(DEFAULT_KEYWORDS_FILE)
            args.sites = ALL_SITES
        else:
            print("잘못된 선택입니다.")
            return

        if not args.merge_worker_url and DEFAULT_MERGE_WORKER_URL:
            args.merge_worker_url = DEFAULT_MERGE_WORKER_URL

        if not args.merge_worker_url:
            args.merge_worker_url = input("Merge worker URL (ex: http://138.2.124.59:8090): ").strip()

        if choice == "1":
            sites = input(f"Sites [{args.sites}]: ").strip()
            if sites:
                args.sites = sites
        else:
            print(f"Sites: {args.sites}")

    jobs = load_jobs(args)
    if not jobs:
        print("No keywords provided.")
        return

    if not args.merge_worker_url and DEFAULT_MERGE_WORKER_URL:
        args.merge_worker_url = DEFAULT_MERGE_WORKER_URL

    if not args.no_ssh_tunnel and SSH_KEY_PATH:
        args.ssh_tunnel = True

    tunnel_proc = None
    if args.ssh_tunnel:
        tunnel_proc = start_ssh_tunnel(MERGE_WORKER_PORT, MERGE_WORKER_PORT)
        args.merge_worker_url = f"http://{LOCAL_TUNNEL_HOST}:{MERGE_WORKER_PORT}"

    if not args.merge_worker_url:
        print("Merge worker URL is required.")
        return

    try:
        if not args.skip_health_check:
            check_merge_worker(args.merge_worker_url)

        ok_count = 0
        fail_count = 0

        for index, job in enumerate(jobs, start=1):
            keyword = job["keyword"]
            category = job["category"]
            print(f"\n[Batch Upload] {index}/{len(jobs)}")
            try:
                if process_keyword(
                    keyword=keyword,
                    category=category,
                    sites=args.sites,
                    merge_worker_url=args.merge_worker_url,
                    worker_name=args.worker_name,
                    wait_timeout=args.wait_timeout,
                    llm_only=args.llm_only,
                ):
                    ok_count += 1
                else:
                    fail_count += 1
            except Exception as e:
                fail_count += 1
                print(f"[Batch Upload] failed for {keyword}: {e}")

            if index < len(jobs):
                time.sleep(args.pause)

        print("\n" + "=" * 70)
        print(f"[Batch Upload] done | success={ok_count} | fail={fail_count}")
        print("=" * 70)
    finally:
        if tunnel_proc:
            tunnel_proc.stop()
            print("[Batch Upload] SSH tunnel closed.")


if __name__ == "__main__":
    try:
        main()
    except (urllib.error.URLError, urllib.error.HTTPError) as e:
        print(f"[Batch Upload] merge worker connection failed: {e}")
