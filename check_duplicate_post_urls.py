import re
from collections import defaultdict
from pathlib import Path
from urllib.parse import parse_qs, urlparse


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


# 필요한 URL 해시 데이터를 읽어 이후 처리에 사용할 형태로 준비한다.
def read_url_and_hash(txt_file: Path):
    try:
        with open(txt_file, "r", encoding="utf-8") as f:
            line1 = f.readline().strip()
            line2 = f.readline().strip()
    except Exception as e:
        return None, None, f"read error: {e}"

    if not line1.startswith("URL:") or not line2.startswith("Hash:"):
        return None, None, "missing URL/Hash header"

    url_val = line1.replace("URL:", "", 1).strip().strip('"')
    hash_val = line2.replace("Hash:", "", 1).strip().strip('"')
    return url_val, hash_val, None


# 여러 위치의 중복 게시글 URL 데이터를 모아 하나의 결과로 정리한다.
def collect_duplicate_post_urls(base_dir: Path):
    data_storage = base_dir / "data_storage"
    grouped = defaultdict(list)
    scanned = 0
    skipped = 0

    for txt_file in data_storage.rglob("*.txt"):
        if "trash" in txt_file.parts or txt_file.name.startswith("["):
            skipped += 1
            continue

        url_val, hash_val, error = read_url_and_hash(txt_file)
        if error or not url_val:
            skipped += 1
            continue

        scanned += 1
        post_key = get_post_dedupe_key(url_val)
        if not post_key:
            continue

        grouped[post_key].append(
            {
                "file": txt_file,
                "hash": hash_val,
                "url": url_val,
            }
        )

    duplicates = {
        post_key: items
        for post_key, items in grouped.items()
        if len(items) >= 2
    }
    return scanned, skipped, duplicates


# 스크립트의 주요 실행 흐름을 시작하고 전체 작업을 조율한다.
def main():
    base_dir = Path(__file__).resolve().parent
    scanned, skipped, duplicates = collect_duplicate_post_urls(base_dir)

    print(f"scanned txt files: {scanned}")
    print(f"skipped txt files: {skipped}")
    print(f"duplicate post groups: {len(duplicates)}")
    print()

    if not duplicates:
        print("No duplicate post URLs found.")
        return

    for post_key in sorted(duplicates):
        items = duplicates[post_key]
        print(f"[{post_key}] {len(items)} files")
        for index, item in enumerate(items, start=1):
            print(f"  {index}. file: {item['file']}")
            print(f"     hash: {item['hash']}")
            print(f"     url : {item['url']}")
        print()


if __name__ == "__main__":
    main()
