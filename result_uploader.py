import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent


# post_json 작업에 필요한 핵심 처리를 수행한다.
def post_json(url: str, payload: dict, timeout: int = 30):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return res.status, res.read().decode("utf-8", errors="replace")


# 관련 pipeline 작업을 실행하고 필요한 후속 처리를 수행한다.
def run_pipeline(keyword: str, sites: str, category: str = "", llm_only: bool = False):
    script = BASE_DIR / "pipeline_pro.py"
    if not script.exists():
        raise FileNotFoundError(f"pipeline_pro.py not found: {script}")

    cmd = [sys.executable, str(script), "--keyword", keyword, "--sites", sites]
    if category:
        cmd += ["--category", category]
    if llm_only:
        cmd += ["--llm-only"]

    result = subprocess.run(cmd, cwd=str(BASE_DIR))
    if result.returncode != 0:
        raise RuntimeError(f"pipeline failed with returncode={result.returncode}")


# 처리 결과나 상태 정보를 외부 서버로 전송한다.
def notify_worker_started(keyword: str, sites: str, upload_url: str, worker_name: str, category: str = ""):
    payload = {
        "keyword": keyword,
        "category": category,
        "sites": [site.strip() for site in sites.split(",") if site.strip()],
        "worker": worker_name,
        "status": "STARTED",
    }
    return post_json(upload_url.rstrip("/") + "/worker-started", payload)


# 다음 단계로 넘어가기 전에 필요한 결과 파일 상태가 될 때까지 대기한다.
def wait_for_result_file(keyword: str, timeout: int):
    path = BASE_DIR / "data_storage" / keyword / f"{keyword}_result.json"
    started = time.time()

    while time.time() - started < timeout:
        if path.exists() and path.stat().st_size > 0:
            return path
        time.sleep(2)

    raise TimeoutError(f"result JSON not found before timeout: {path}")


# 필요한 원본 매핑 데이터를 읽어 이후 처리에 사용할 형태로 준비한다.
def load_source_map(keyword: str):
    path = BASE_DIR / "data_storage" / keyword / f"{keyword}_dict.json"
    if not path.exists() or path.stat().st_size <= 0:
        print(f"source map skipped: dict JSON not found: {path}")
        return []

    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        raise ValueError(f"source map JSON must be an object: {path}")

    # 입력 데이터에서 불필요한 요소를 제거하거나 정리한다.
    def clean(value):
        return str(value).strip().strip('"').strip()

    items = [
        {"hash": clean(hash_value), "url": clean(url)}
        for hash_value, url in data.items()
        if clean(hash_value) and clean(url)
    ]
    print(f"loaded source map: {len(items)} items from {path.name}")
    return items


# 처리 결과나 상태 정보를 외부 서버로 전송한다.
def upload_result(keyword: str, upload_url: str, result_path: Path, category: str = ""):
    with result_path.open("r", encoding="utf-8") as f:
        report_content = f.read()

    if not report_content.strip():
        raise ValueError(f"result JSON is empty: {result_path}")

    payload = {
        "keyword": keyword,
        "category": category,
        "type": "result",
        "fileName": result_path.name,
        "reportContent": report_content,
        "sourceMap": load_source_map(keyword),
    }
    return post_json(upload_url.rstrip("/") + "/upload-result", payload)


# 스크립트의 주요 실행 흐름을 시작하고 전체 작업을 조율한다.
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--keyword", required=True)
    parser.add_argument("--sites", default="dc,clien,fmk,quasar")
    parser.add_argument("--category", default="")
    parser.add_argument("--merge-worker-url", required=True, help="Example: http://SERVER_IP:8090")
    parser.add_argument("--result-path", default=None)
    parser.add_argument("--run-pipeline", action="store_true")
    parser.add_argument("--llm-only", action="store_true")
    parser.add_argument("--notify-started", action="store_true")
    parser.add_argument("--worker-name", default="friend-pc")
    parser.add_argument("--wait-timeout", type=int, default=1800)
    args = parser.parse_args()

    keyword = args.keyword.strip()
    if not keyword:
        raise ValueError("keyword is empty")

    if args.notify_started or args.run_pipeline:
        status, text = notify_worker_started(
            keyword,
            args.sites,
            args.merge_worker_url,
            args.worker_name,
            args.category.strip(),
        )
        print(f"notified worker started: status={status}, response={text}")

    if args.run_pipeline:
        run_pipeline(keyword, args.sites, args.category.strip(), args.llm_only)

    result_path = Path(args.result_path) if args.result_path else wait_for_result_file(keyword, args.wait_timeout)
    status, text = upload_result(keyword, args.merge_worker_url, result_path, args.category.strip())
    print(f"uploaded result JSON: status={status}, response={text}")


if __name__ == "__main__":
    try:
        main()
    except (urllib.error.URLError, urllib.error.HTTPError) as e:
        print(f"upload failed: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"result_upload_worker failed: {e}", file=sys.stderr)
        sys.exit(1)
