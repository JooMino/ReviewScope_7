import argparse
import json
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlencode, urlparse


def safe_keyword(value: str):
    keyword = (value or "").strip()
    if not keyword:
        raise ValueError("keyword is empty")
    if any(ch in keyword for ch in ['/', '\\', ':', '*', '?', '"', '<', '>', '|']):
        raise ValueError(f"keyword contains unsupported filename character: {keyword}")
    return keyword


def to_int_count(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def read_json(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json_atomic(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp_path.replace(path)


def write_text_atomic(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        f.write(text)
    tmp_path.replace(path)


def merge_mentions_into_report(report_data: dict, mentions_data: dict):
    monthly_counts = {}

    if isinstance(mentions_data.get("site_counts"), dict):
        for item in mentions_data["site_counts"].values():
            for month, count in item.get("monthly_counts", {}).items():
                monthly_counts[month] = monthly_counts.get(month, 0) + to_int_count(count)
    else:
        for month, count in mentions_data.get("monthly_counts", {}).items():
            monthly_counts[month] = monthly_counts.get(month, 0) + to_int_count(count)

    included_months = {
        month
        for month, count in monthly_counts.items()
        if count > 0
    }
    excluded_months = {
        month: count
        for month, count in monthly_counts.items()
        if count <= 0
    }
    monthly_counts = {
        month: count
        for month, count in monthly_counts.items()
        if month in included_months
    }
    monthly_counts = dict(sorted(monthly_counts.items()))

    merged_report = dict(report_data)
    if not merged_report.get("category") and mentions_data.get("category"):
        merged_report["category"] = mentions_data["category"]
    merged_report["monthly_counts"] = monthly_counts
    merged_report["total_mentions"] = sum(monthly_counts.values())
    merged_report.pop("site_mentions", None)
    merged_report.pop("site_total_mentions", None)
    merged_report["mention_filter"] = {
        "type": "nonzero_months",
        "included_months": sorted(included_months),
        "excluded_months": dict(sorted(excluded_months.items())),
    }
    return merged_report


def post_done(done_url: str, keyword: str, merged_report: dict, timeout: int = 30):
    payload = {
        "keyword": keyword,
        "category": merged_report.get("category", ""),
        "results": [],
        "reportContent": json.dumps(merged_report, ensure_ascii=False),
        "reportContentH200": "",
        "status": "SUCCESS",
        "errorMessage": None,
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        done_url,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return res.status, res.read().decode("utf-8", errors="replace")


def derive_source_map_url(done_url: str):
    parsed = urlparse(done_url)
    path = parsed.path
    if path.endswith("/done"):
        path = path[:-len("/done")] + "/source-map"
    else:
        path = path.rstrip("/") + "/source-map"
    return parsed._replace(path=path, query="", fragment="").geturl()


def normalize_source_map(value):
    def clean(item):
        return str(item).strip().strip('"').strip()

    if not value:
        return []
    if isinstance(value, dict):
        return [
            {"hash": clean(hash_value), "url": clean(url)}
            for hash_value, url in value.items()
            if clean(hash_value) and clean(url)
        ]
    if isinstance(value, list):
        items = []
        for item in value:
            if not isinstance(item, dict):
                continue
            hash_value = clean(item.get("hash") or "")
            url = clean(item.get("url") or "")
            if hash_value and url:
                items.append({"hash": hash_value, "url": url})
        return items
    raise ValueError("sourceMap must be a list or object")


def post_source_map(source_map_url: str, keyword: str, items: list, timeout: int = 30):
    if not items:
        print(f"[SOURCE MAP] skipped keyword={keyword}, items=0")
        return None, "skipped: empty source map"

    separator = "&" if urlparse(source_map_url).query else "?"
    url = f"{source_map_url}{separator}{urlencode({'keyword': keyword})}"
    payload = {"items": items}
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return res.status, res.read().decode("utf-8", errors="replace")


class MergeState:
    def __init__(self, upload_dir: Path, done_url: str, source_map_url: str, scan_interval: int, mention_sites):
        self.upload_dir = upload_dir
        self.done_url = done_url
        self.source_map_url = source_map_url
        self.scan_interval = scan_interval
        self.mention_sites = mention_sites
        self.base_dir = Path(__file__).resolve().parent
        self.pending_dir = upload_dir / "pending"
        self.done_dir = upload_dir / "done"
        self.error_dir = upload_dir / "error"
        self.lock = threading.Lock()
        self.mention_jobs = set()
        for path in [self.pending_dir, self.done_dir, self.error_dir]:
            path.mkdir(parents=True, exist_ok=True)

    def keyword_dir(self, keyword: str):
        return self.pending_dir / safe_keyword(keyword)

    def save_started(self, payload: dict):
        keyword = safe_keyword(payload.get("keyword"))
        keyword_dir = self.keyword_dir(keyword)
        keyword_dir.mkdir(parents=True, exist_ok=True)
        write_json_atomic(keyword_dir / "started.json", payload)

        sites = payload.get("mentionSites") or self.mention_sites
        if isinstance(sites, str):
            sites = [site.strip() for site in sites.split(",") if site.strip()]
        sites = [str(site).strip() for site in sites if str(site).strip()]
        if not sites:
            sites = ["dc"]

        self.start_mentions_job(keyword, sites)
        return keyword

    def save_upload(self, kind: str, payload: dict):
        keyword = safe_keyword(payload.get("keyword"))
        keyword_dir = self.keyword_dir(keyword)
        keyword_dir.mkdir(parents=True, exist_ok=True)

        if kind == "result":
            content = payload.get("reportContent")
            if not isinstance(content, str) or not content.strip():
                raise ValueError("reportContent is empty")
            report_data = json.loads(content)
            if not isinstance(report_data, dict):
                raise ValueError("reportContent must be a JSON object")

            category = (payload.get("category") or "").strip()
            if not category:
                started_path = keyword_dir / "started.json"
                if started_path.exists():
                    category = (read_json(started_path).get("category") or "").strip()
            if category:
                report_data["category"] = category

            write_json_atomic(keyword_dir / "result.json", report_data)
            source_map = normalize_source_map(payload.get("sourceMap") or payload.get("source_map"))
            if source_map:
                write_json_atomic(keyword_dir / "source_map.json", {"items": source_map})
                print(f"[SOURCE MAP] received keyword={keyword}, items={len(source_map)}")
            else:
                print(f"[SOURCE MAP] missing or empty keyword={keyword}")
        elif kind == "mentions":
            if not isinstance(payload.get("monthly_counts"), dict) and not isinstance(payload.get("site_counts"), dict):
                raise ValueError("mentions payload needs monthly_counts or site_counts")
            write_json_atomic(keyword_dir / "mentions.json", payload)
        else:
            raise ValueError(f"unsupported upload kind: {kind}")

        return keyword

    def start_mentions_job(self, keyword: str, sites):
        mentions_path = self.keyword_dir(keyword) / "mentions.json"
        if mentions_path.exists():
            return

        with self.lock:
            if keyword in self.mention_jobs:
                return
            self.mention_jobs.add(keyword)

        thread = threading.Thread(
            target=self.run_mentions_job,
            args=(keyword, sites),
            daemon=True,
        )
        thread.start()

    def run_mentions_job(self, keyword: str, sites):
        try:
            mention_run = self.base_dir / "mention_run.py"
            if not mention_run.exists():
                raise FileNotFoundError(f"mention_run.py not found on ubuntu server: {mention_run}")

            for site in sites:
                print(f"[MENTIONS] start keyword={keyword}, site={site}")
                result = subprocess.run(
                    [sys.executable, str(mention_run), "--keyword", keyword, "--site", site],
                    cwd=str(self.base_dir),
                )
                if result.returncode != 0:
                    raise RuntimeError(f"mention counter failed: site={site}, returncode={result.returncode}")

            payload = self.build_mentions_payload(keyword, sites)
            write_json_atomic(self.keyword_dir(keyword) / "mentions.json", payload)
            print(f"[MENTIONS] done keyword={keyword}, sites={sites}")
            self.scan_once()
        except Exception as e:
            keyword_dir = self.keyword_dir(keyword)
            keyword_dir.mkdir(parents=True, exist_ok=True)
            write_text_atomic(keyword_dir / "mentions_error.txt", str(e))
            print(f"[MENTIONS ERROR] keyword={keyword}, error={e}", file=sys.stderr)
        finally:
            with self.lock:
                self.mention_jobs.discard(keyword)

    def build_mentions_payload(self, keyword: str, sites):
        site_counts = {}
        merged_counts = {}
        data_dir = self.base_dir / "data_storage" / keyword

        for site in sites:
            path = data_dir / f"{keyword}_{site}_counts.json"
            if not path.exists():
                print(f"[MENTIONS] count file missing: {path}")
                continue

            data = read_json(path)
            monthly = {
                month: to_int_count(count)
                for month, count in data.get("monthly_counts", {}).items()
            }
            site_counts[site] = {
                "site": site,
                "fileName": path.name,
                "monthly_counts": monthly,
                "total_count": sum(monthly.values()),
            }

            for month, count in monthly.items():
                merged_counts[month] = merged_counts.get(month, 0) + count

        if not site_counts:
            raise FileNotFoundError(f"no mention count files created for keyword={keyword}")

        return {
            "keyword": keyword,
            "type": "mentions",
            "site_counts": site_counts,
            "monthly_counts": dict(sorted(merged_counts.items())),
            "total_count": sum(merged_counts.values()),
        }

    def scan_once(self):
        with self.lock:
            for keyword_dir in self.pending_dir.iterdir():
                if not keyword_dir.is_dir():
                    continue

                result_path = keyword_dir / "result.json"
                mentions_path = keyword_dir / "mentions.json"
                if not result_path.exists() or not mentions_path.exists():
                    continue

                keyword = keyword_dir.name
                try:
                    report_data = read_json(result_path)
                    mentions_data = read_json(mentions_path)
                    merged_report = merge_mentions_into_report(report_data, mentions_data)
                    source_map_path = keyword_dir / "source_map.json"
                    source_map_items = []
                    if source_map_path.exists():
                        source_map_items = normalize_source_map(read_json(source_map_path).get("items"))
                    source_status, source_text = post_source_map(self.source_map_url, keyword, source_map_items)
                    status, text = post_done(self.done_url, keyword, merged_report)

                    finished_dir = self.done_dir / keyword
                    if finished_dir.exists():
                        shutil.rmtree(finished_dir)
                    shutil.move(str(keyword_dir), str(finished_dir))
                    write_json_atomic(finished_dir / "merged.json", merged_report)
                    write_text_atomic(finished_dir / "source_map_response.txt", f"status={source_status}\n{source_text}")
                    write_text_atomic(finished_dir / "done_response.txt", f"status={status}\n{text}")
                    print(f"[DONE] keyword={keyword}, source_map_status={source_status}, done_status={status}")
                except Exception as e:
                    failed_dir = self.error_dir / f"{keyword}_{int(time.time())}"
                    shutil.move(str(keyword_dir), str(failed_dir))
                    write_text_atomic(failed_dir / "error.txt", str(e))
                    print(f"[ERROR] keyword={keyword}, error={e}", file=sys.stderr)

    def run_loop(self):
        while True:
            self.scan_once()
            time.sleep(self.scan_interval)


class UploadHandler(BaseHTTPRequestHandler):
    state: MergeState = None

    def _send_json(self, status: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if urlparse(self.path).path == "/health":
            self._send_json(200, {"ok": True})
            return
        self._send_json(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        if path not in ["/worker-started", "/upload-result", "/upload-mentions"]:
            self._send_json(404, {"ok": False, "error": "not found"})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8")
            payload = json.loads(body)

            if path == "/worker-started":
                keyword = self.state.save_started(payload)
                self._send_json(200, {"ok": True, "keyword": keyword, "kind": "started"})
                return

            kind = "result" if path == "/upload-result" else "mentions"
            keyword = self.state.save_upload(kind, payload)
            self.state.scan_once()
            self._send_json(200, {"ok": True, "keyword": keyword, "kind": kind})
        except Exception as e:
            self._send_json(400, {"ok": False, "error": str(e)})

    def log_message(self, fmt, *args):
        print(f"[HTTP] {self.address_string()} - {fmt % args}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--upload-dir", default="./merge_uploads")
    parser.add_argument("--done-url", default="http://127.0.0.1:8080/api/crawl/done")
    parser.add_argument("--source-map-url", default=None, help="Default: done-url with /done replaced by /source-map.")
    parser.add_argument("--scan-interval", type=int, default=3)
    parser.add_argument("--mention-sites", default="dc", help="Comma separated mention counters to run on this server.")
    args = parser.parse_args()

    mention_sites = [site.strip() for site in args.mention_sites.split(",") if site.strip()]
    if not mention_sites:
        mention_sites = ["dc"]

    state = MergeState(
        upload_dir=Path(args.upload_dir).resolve(),
        done_url=args.done_url,
        source_map_url=args.source_map_url or derive_source_map_url(args.done_url),
        scan_interval=args.scan_interval,
        mention_sites=mention_sites,
    )
    UploadHandler.state = state

    loop_thread = threading.Thread(target=state.run_loop, daemon=True)
    loop_thread.start()

    server = ThreadingHTTPServer((args.host, args.port), UploadHandler)
    print(f"merge worker listening on http://{args.host}:{args.port}")
    print(f"upload dir: {state.upload_dir}")
    print(f"done url: {args.done_url}")
    print(f"source map url: {state.source_map_url}")
    print(f"mention sites: {mention_sites}")
    server.serve_forever()


if __name__ == "__main__":
    main()
