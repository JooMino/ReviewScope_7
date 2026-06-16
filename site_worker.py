

import sys
import os
import subprocess
import time
import requests
import re
from sshtunnel import SSHTunnelForwarder
from dotenv import load_dotenv
from urllib.parse import parse_qs, urlparse
load_dotenv()



SSH_KEY_PATH    = os.getenv(r"SSH_KEY_PATH")
REMOTE_HOST     = "138.2.124.59"
REMOTE_USER     = "ubuntu"
POLL_INTERVAL   = 3
LOCAL_PORT      = 8080
LOCAL_MERGE_PORT = int(os.getenv("LOCAL_MERGE_PORT", "8090"))
REMOTE_MERGE_PORT = int(os.getenv("MERGE_WORKER_PORT", "8090"))
MERGE_WORKER_URL = os.getenv("MERGE_WORKER_URL", f"http://127.0.0.1:{LOCAL_MERGE_PORT}")
MERGE_WORKER_NAME = os.getenv("MERGE_WORKER_NAME", "site_worker")
DEFAULT_CATEGORY = os.getenv("DEFAULT_CATEGORY", "unknown")




USE_H200_REPORT = False


from pathlib import Path
import json
from mention_count.merge_mention import find_cutoff_month, to_int_count





# 처리 결과나 상태 정보를 외부 서버로 전송한다.
def send_source_map_to_server(source_map: dict, server_url: str, keyword: str, category=""):
    try:
        url = f"{server_url}/api/crawl/source-map?keyword={keyword}"
        payload = {
            "category": category or DEFAULT_CATEGORY,
            "items": [
                {"hash": h, "url": u}
                for h, u in source_map.items()
            ]
        }
        res = requests.post(url, json=payload, timeout=10)
        if res.status_code == 200:
            print("source map 서버 전송 성공")
            print(f"응답: {res.text}")
            return True
        else:
            print(f"서버 응답 오류: {res.status_code}, {res.text}")
            return False
    except Exception as e:
        print(f"전송 실패: {e}")
        return False


# 처리 결과나 상태 정보를 외부 서버로 전송한다.
def notify_merge_worker_started(keyword, sites, category=""):
    try:
        res = requests.post(
            f"{MERGE_WORKER_URL.rstrip('/')}/worker-started",
            json={
                "keyword": keyword,
                "sites": sites,
                "mentionSites": ["dc"],
                "worker": MERGE_WORKER_NAME,
                "status": "STARTED",
                "category": category,
            },
            timeout=10,
        )
        print(f"/worker-started 응답: {res.status_code}, {res.text}")
        return res.status_code == 200
    except Exception as e:
        print(f"/worker-started 전송 실패: {e}")
        return False


# 처리 결과나 상태 정보를 외부 서버로 전송한다.
def upload_source_map_to_merge_worker(keyword, source_map: dict, category=""):
    if not source_map:
        print("[Worker] source_map is empty")
        return True

    try:
        res = requests.post(
            f"{MERGE_WORKER_URL.rstrip('/')}/upload-source-map",
            json={
                "keyword": keyword,
                "category": category,
                "items": [
                    {"hash": h, "url": u}
                    for h, u in source_map.items()
                ],
            },
            timeout=30,
        )
        print(f"/upload-source-map 응답: {res.status_code}, {res.text}")
        return res.status_code == 200
    except Exception as e:
        print(f"/upload-source-map 전송 실패: {e}")
        return False


# 처리 결과나 상태 정보를 외부 서버로 전송한다.
def upload_result_to_merge_worker(keyword, report_content, category=""):
    try:
        res = requests.post(
            f"{MERGE_WORKER_URL.rstrip('/')}/upload-result",
            json={
                "keyword": keyword,
                "type": "result",
                "fileName": f"{keyword}_result.json",
                "reportContent": report_content if report_content is not None else "",
                "category": category,
            },
            timeout=30,
        )
        print(f"/upload-result 응답: {res.status_code}, {res.text}")
        return res.status_code == 200
    except Exception as e:
        print(f"/upload-result 전송 실패: {e}")
        return False


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


# 여러 위치의 해시 URL 매핑 데이터를 모아 하나의 결과로 정리한다.
def collect_hash_url_map(keyword: str, base_dir: str):
    keyword_storage = Path(base_dir) / "data_storage" / keyword
    merged_data = {}
    seen_post_keys = set()
    total_scanned_files = 0
    total_duplicate_urls = 0

    print("[Worker] txt 스캔 -> hash/url 추출 시작")

    for txt_file in keyword_storage.rglob("*.txt"):

        if txt_file.name.startswith(f"[{keyword}]_") or "trash" in txt_file.parts:
            continue
        try:
            with open(txt_file, "r", encoding="utf-8") as f:
                line1 = f.readline().strip()
                line2 = f.readline().strip()

            if line1.startswith("URL:") and line2.startswith("Hash:"):
                url_val  = line1.replace("URL:", "").strip().strip('"')
                hash_val = line2.replace("Hash:", "").strip().strip('"')
                if hash_val and url_val:
                    post_key = get_post_dedupe_key(url_val)
                    if post_key:
                        if post_key in seen_post_keys:
                            total_duplicate_urls += 1
                            continue
                        seen_post_keys.add(post_key)
                    merged_data[hash_val] = url_val
                    total_scanned_files += 1
        except Exception as e:
            print(f"[Worker] 파일 읽기 오류 ({txt_file.name}): {e}")

    print(f"총 스캔 파일 수: {total_scanned_files}")
    print(f"중복 URL 제외 수: {total_duplicate_urls}")
    print(f"최종 source_map 개수: {len(merged_data)}")
    return merged_data





# 관련 site launcher 작업을 실행하고 필요한 후속 처리를 수행한다.
def run_site_launcher(keyword, sites, category=""):
    current_dir     = os.path.dirname(os.path.abspath(__file__))
    sites_arg       = ",".join(sites)
    pipeline_script = os.path.join(current_dir, "pipeline_pro.py")

    json_report_file = os.path.join(current_dir, "data_storage", keyword, f"{keyword}_result.json")
    json_h200_file   = os.path.join(current_dir, "data_storage", keyword, f"{keyword}_result_h200.json")

    print(f"\n--- 파이프라인 실행 요청: {keyword} ({sites_arg}) ---")
    print(f"일반 JSON 대상 경로: {json_report_file}")

    if USE_H200_REPORT:
        print(f"H200 JSON 대상 경로: {json_h200_file}")
    else:
        print("H200 JSON 대기 생략 (USE_H200_REPORT=False)")

    if not os.path.exists(pipeline_script):
        print(f"pipeline_pro.py 를 찾을 수 없습니다: {pipeline_script}")
        return False

    try:
        cmd = [
            sys.executable,
            pipeline_script,
            "--keyword", keyword,
            "--sites",   sites_arg,
        ]
        if category:
            cmd += ["--category", category]

        proc = subprocess.Popen(cmd)

        print(f"파이프라인 감시 중... (PID: {proc.pid})")

        max_wait          = 1800
        start_time        = time.time()
        start_time        = time.time()
        pipeline_finished = False

        # 필요한 ready 조건을 확인하고 실행 가능 상태를 보장한다.
        def check_ready():
            """USE_H200_REPORT 설정에 따라 필요한 파일 존재 여부 반환"""
            if USE_H200_REPORT:
                return os.path.exists(json_report_file) and os.path.exists(json_h200_file)
            return os.path.exists(json_report_file)

        while time.time() - start_time < max_wait:

            if check_ready():
                print(f"\nJSON 리포트 파일 검출 완료!")
                time.sleep(1.5)
                return "json"


            if not pipeline_finished and proc.poll() is not None:
                print(f"\npipeline_pro.py 종료 감지. JSON 파일 최종 확인 중...")
                pipeline_finished = True
                time.sleep(2)

                if check_ready():
                    print("JSON 파일 확인 완료!")
                    return "json"


                if proc.returncode != 0:
                    print(f"파이프라인 비정상 종료 (returncode={proc.returncode})")
                else:
                    print("파이프라인 정상 종료됐으나 JSON 파일 없음 (경로 확인 필요)")
                return False

            print(".", end="", flush=True)
            time.sleep(3)

        print("\n시간 초과: 최대 대기 시간을 초과했습니다.")
        proc.terminate()
        return False

    except Exception as e:
        print(f"run_site_launcher 예외: {e}")
        return False





# 필요한 JSON 보고서 데이터를 읽어 이후 처리에 사용할 형태로 준비한다.
def read_json_report(keyword, suffix="_result.json"):
    json_report_file = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "data_storage", keyword, f"{keyword}{suffix}"
    )
    print(f"JSON 읽기 시도 ({suffix}): {json_report_file}")
    exists = os.path.exists(json_report_file)
    print(f"파일 존재 여부: {exists}")

    if not exists:
        return None, f"JSON file not found: {json_report_file}"
    try:
        with open(json_report_file, "r", encoding="utf-8") as f:
            report_content = f.read()
        if not report_content or not report_content.strip():
            return None, f"JSON report is empty: {suffix}"
        return report_content, None
    except Exception as e:
        return None, f"JSON read failed: {e}"





# 처리 결과나 상태 정보를 외부 서버로 전송한다.
def send_done(server_url, keyword, report_content, report_h200_content, status, error_message=None, category=""):
    try:

        safe_report      = report_content      if report_content      is not None else ""
        safe_report_h200 = report_h200_content if report_h200_content is not None else ""

        print("\n/api/crawl/done 통합 전송")
        print(f"keyword = {keyword} | status = {status}")
        print(f"기본 리포트 길이 = {len(safe_report)} | H200 리포트 길이 = {len(safe_report_h200)}")

        done_res = requests.post(
            f"{server_url}/api/crawl/done",
            json={
                "keyword":           keyword,
                "category":          category or DEFAULT_CATEGORY,
                "results":           [],
                "reportContent":     safe_report,
                "reportContentH200": safe_report_h200,
                "status":            status,
                "errorMessage":      error_message
            },
            timeout=15,
        )
        print(f"/done 응답 코드: {done_res.status_code}")
        print(f"/done 응답 본문: {done_res.text}")
        return True
    except Exception as e:
        print(f"/api/crawl/done 전송 실패: {e}")
        return False





# 나뉘어 있는 언급량 JSON 데이터를 병합해 최종 결과를 만든다.
def merge_mentions_json(keyword):
    base_dir    = os.path.dirname(os.path.abspath(__file__))
    report_path = os.path.join(base_dir, "data_storage", keyword, f"{keyword}_result.json")
    mention_files = {
        "dc":    os.path.join(base_dir, "data_storage", keyword, f"{keyword}_dc_counts.json"),
        "clien": os.path.join(base_dir, "data_storage", keyword, f"{keyword}_clien_counts.json"),
    }
    try:
        with open(report_path, "r", encoding="utf-8") as f:
            report_data = json.load(f)

        merged_counts = {}
        for site, path in mention_files.items():
            if not os.path.exists(path):
                continue
            with open(path, "r", encoding="utf-8") as f:
                mention_data = json.load(f)
            monthly = {
                month: to_int_count(count)
                for month, count in mention_data.get("monthly_counts", {}).items()
            }
            for month, count in monthly.items():
                merged_counts[month] = merged_counts.get(month, 0) + count

        included_months = {
            month
            for month, count in merged_counts.items()
            if count > 0
        }
        excluded_months = {
            month: count
            for month, count in merged_counts.items()
            if count <= 0
        }
        merged_counts   = dict(sorted({m: c for m, c in merged_counts.items() if m in included_months}.items()))
        total_count     = sum(merged_counts.values())

        report_data["monthly_counts"]  = merged_counts
        report_data["total_mentions"]  = total_count
        report_data.pop("site_mentions", None)
        report_data.pop("site_total_mentions", None)
        report_data["mention_filter"] = {
            "type":            "nonzero_months",
            "included_months": sorted(included_months),
            "excluded_months": dict(sorted(excluded_months.items()))
        }

        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report_data, f, ensure_ascii=False, indent=2)

        print("언급량 병합 완료")
        return True
    except Exception as e:
        print(f"언급량 병합 실패: {e}")
        return False





# 스크립트의 주요 실행 흐름을 시작하고 전체 작업을 조율한다.
def main():
    server_url = f"http://localhost:{LOCAL_PORT}"

    while True:
        try:
            print(f"\nSSH 터널 연결 시도... ({REMOTE_HOST})")
            with SSHTunnelForwarder(
                (REMOTE_HOST, 22),
                ssh_username=REMOTE_USER,
                ssh_pkey=SSH_KEY_PATH,
                remote_bind_addresses=[
                    ("127.0.0.1", 8080),
                    ("127.0.0.1", REMOTE_MERGE_PORT),
                ],
                local_bind_addresses=[
                    ("127.0.0.1", LOCAL_PORT),
                    ("127.0.0.1", LOCAL_MERGE_PORT),
                ],
                set_keepalive=10.0
            ) as tunnel:

                print(f"=== 연결 성공: Local {LOCAL_PORT} -> Remote 8080 ===")

                print(f"=== SSH tunnel connected: 127.0.0.1:{LOCAL_MERGE_PORT} -> remote {REMOTE_MERGE_PORT} ===")

                while True:
                    try:
                        res = requests.get(f"{server_url}/api/crawl/next", timeout=5)

                        if res.status_code == 204:
                            print("대기중...")
                            time.sleep(POLL_INTERVAL)
                            continue

                        if res.status_code != 200:
                            print(f"/api/crawl/next 이상 응답: {res.status_code}")
                            time.sleep(POLL_INTERVAL)
                            continue

                        job = res.json()
                        if not job:
                            print("대기중... (job 없음)")
                            time.sleep(POLL_INTERVAL)
                            continue

                        keyword = job["keyword"]
                        sites   = job.get("sites", ["dc", "clien", "fmk", "quasar"])
                        category = (job.get("category") or DEFAULT_CATEGORY).strip()

                        print(f"\n작업 수신: keyword={keyword}, sites={sites}")

                        try:
                            exists_res = requests.get(
                                f"{server_url}/api/crawl/exists",
                                params={"keyword": keyword},
                                timeout=5,
                            )
                            exists_res.raise_for_status()
                            exists = exists_res.json().get("exists", False)
                            print(f"DB 최근 리포트 존재 여부: {exists}")
                        except Exception as e:
                            print(f"/api/crawl/exists 확인 실패: {e}")
                            exists = False

                        if exists:
                            print("DB에 최근 리포트 있음 -> SKIPPED")
                            base_dir   = os.path.dirname(os.path.abspath(__file__))
                            source_map = collect_hash_url_map(keyword, base_dir)
                            if source_map:
                                send_source_map_to_server(source_map, server_url, keyword, category)
                            send_done(
                                server_url=server_url, keyword=keyword,
                                report_content="", report_h200_content="",
                                status="SKIPPED",
                                category=category
                            )
                            continue


                        notify_merge_worker_started(keyword, sites, category)
                        result_type = run_site_launcher(keyword, sites, category)

                        if result_type == "json":




                            report_content, error1 = read_json_report(keyword, "_result.json")
                            if error1:
                                print(f"JSON 읽기 실패: {error1}")
                                send_done(
                                    server_url=server_url, keyword=keyword,
                                    report_content=None, report_h200_content=None,
                                    status="FAIL", error_message=error1,
                                    category=category
                                )
                                continue


                            report_h200_content = None
                            if USE_H200_REPORT:
                                report_h200_content, error2 = read_json_report(keyword, "_result_h200.json")
                                if error2:
                                    print(f"H200 JSON 읽기 실패: {error2}")
                                    send_done(
                                        server_url=server_url, keyword=keyword,
                                        report_content=None, report_h200_content=None,
                                        status="FAIL", error_message=error2,
                                        category=category
                                    )
                                    continue

                            base_dir   = os.path.dirname(os.path.abspath(__file__))
                            source_map = collect_hash_url_map(keyword, base_dir)
                            send_source_map_to_server(source_map, server_url, keyword, category)

                            if not upload_result_to_merge_worker(keyword, report_content, category):
                                send_done(
                                    server_url=server_url, keyword=keyword,
                                    report_content=None,
                                    report_h200_content=None,
                                    status="FAIL",
                                    error_message="Failed to upload result JSON to merge worker",
                                    category=category
                                )
                            continue

                        print("JSON 생성 실패")
                        send_done(
                            server_url=server_url, keyword=keyword,
                            report_content=None, report_h200_content=None,
                            status="FAIL",
                            error_message="JSON report files not created completely",
                            category=category
                        )

                    except requests.exceptions.RequestException as e:
                        print(f"서버 통신 에러: {e}")
                        print("터널 재연결 루프로 이동합니다.")
                        break

                    except Exception as e:
                        print(f"작업 처리 중 예외: {e}")
                        time.sleep(5)

        except Exception as e:
            print(f"SSH 연결 실패: {e}")
            print("5초 후 재접속합니다...")
            time.sleep(5)



if __name__ == "__main__":
    main()
