

import asyncio, csv, json, re, requests, time, argparse, subprocess, sys, paramiko
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv
import os

load_dotenv()




ap = argparse.ArgumentParser()
ap.add_argument("--idle",      default=120, type=int, help="유휴 종료 대기(초)")
ap.add_argument("--workers",   default=2,   type=int, help="총 워커 수 (TOTAL_WORKERS)")
ap.add_argument("--worker-id", default=0,   type=int, help="이 인스턴스의 워커 ID")
args = ap.parse_args()





WORKER_ID       = args.worker_id
TOTAL_WORKERS   = args.workers
IDLE_TIMEOUT    = args.idle
OLLAMA_BASE_URL = "http://127.0.0.1:11434"
MODEL_NAME      = "exaone3.5:7.8b"
MAX_WORKERS     = 2
MAX_CHARS       = 6000
NUM_PREDICT     = 1800
MAX_RETRIES     = 2

BASE_DIR        = Path(__file__).parent
WATCH_DIR       = BASE_DIR / "data_storage"
GEMINI_SCRIPT   = BASE_DIR / "gemini_analyzer.py"


SERVER_IP         = os.getenv("SERVER_IP")
SERVER_PORT       = int(os.getenv("SERVER_PORT", 22))
SERVER_USER       = os.getenv("SERVER_USER")
SERVER_PASS       = os.getenv("SERVER_PASS")
REMOTE_TARGET_DIR = "/home/user001/rs_7/data_storage"


_transport = None
_sftp      = None

SYSTEM_PROMPT = """
당신은 한국 인터넷 커뮤니티의 게시글과 댓글을 분석하는 최고 수준의 AI 데이터 분석가다.
입력된 글이 아무리 짧거나 일상적인 대화라도, 절대 분석을 거부하지 말고 반드시 아래 기준에 따라 분석 결과를 제공해라.

[분석 기준]
1. **model**: 본문에서 다루는 핵심적인 제품명을 구체적으로 적으시오.
2. **date**: 작성일자를 YYYY-MM-DD 형식으로 변환. (없으면 "알수없음")
3. **slang_list**: 본문 전체에서 커뮤니티 은어, 신조어, 줄임말만 골라서 배열로 추출.
4. **summary**: 아래 두 규칙을 반드시 지켜 요약하라.
   - 규칙 A: slang_list에 있는 단어는 표준어로 바꾸지 말고 원문 그대로 사용할 것.
   - 규칙 B: slang_list의 단어가 요약문에 자연스럽게 등장하도록 작성할 것.
5. **is_question**: 본문이나 댓글의 전반적인 맥락이 무언가를 물어보거나 정보를 구하는 '질문글'인지 판단하시오. 질문글이 맞으면 true, 단순 정보 전달/리뷰/일상 잡담이면 false로 판별하시오. (반드시 JSON의 boolean 값인 true 또는 false로만 작성할 것)

항상 아래 형식의 JSON만 출력하고, 절대 다른 말을 붙이지 마세요:
{"model": "제품명(없으면 없음)", "date": "YYYY-MM-DD(없으면 알수없음)", "slang_list": ["은어1", "은어2"], "summary": "slang_list 단어를 포함한 요약", "is_question": true}
"""




# 입력 데이터에서 불필요한 remove URL 요소를 제거하거나 정리한다.
def remove_urls(text):
    text = re.sub(r'\[https?://[^\]]+\]', '[링크]', text)
    return re.sub(r'https?://[^\s\]\)\"\']+', '[링크]', text)


# 입력 데이터에서 불필요한 trim 텍스트 요소를 제거하거나 정리한다.
def trim_text(text, max_chars):
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text
    half = max_chars // 2
    return text[:half] + "\n...(중략)...\n" + text[-half:]


# fix_json_string 작업에 필요한 핵심 처리를 수행한다.
def fix_json_string(raw):
    if not raw:
        return "{}"

    raw = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw.strip(), flags=re.MULTILINE).strip()
    

    raw = raw.replace('\n', '\\n').replace('\r', '\\r')
    return raw

# 입력 데이터에서 필요한 파일 index 정보를 추출한다.
def extract_file_index(file_path):
    m = re.search(r"_(\d+)\.txt$", file_path.name)
    return int(m.group(1)) if m else -1


# get_relative_path 작업에 필요한 핵심 처리를 수행한다.
def get_relative_path(file_path, keyword_folder):
    try:
        return str(file_path.relative_to(keyword_folder))
    except ValueError:
        return file_path.name


# 입력값을 정해진 fmt seconds 형식으로 변환한다.
def fmt_seconds(seconds):
    if seconds is None:
        return "알수없음"
    h, rem = divmod(int(seconds), 3600)
    m, s   = divmod(rem, 60)
    return f"{h}시간 {m}분 {s}초" if h > 0 else f"{m}분 {s}초"


# 필요한 ts 데이터를 읽어 이후 처리에 사용할 형태로 준비한다.
def read_ts(kw_dir, filename):
    f = kw_dir / filename
    try:
        return float(f.read_text(encoding="utf-8")) if f.exists() else None
    except Exception:
        return None


# 서버 연결과 원격 파일 처리를 위한 SFTP 작업을 수행한다.
def get_sftp():
    global _transport, _sftp
    try:
        if _transport and _transport.is_active():
            return _sftp
    except Exception:
        pass
    try:
        _transport = paramiko.Transport((SERVER_IP, SERVER_PORT))
        _transport.connect(username=SERVER_USER, password=SERVER_PASS)
        _sftp = paramiko.SFTPClient.from_transport(_transport)
        return _sftp
    except Exception as e:
        print(f" 학교 서버 SFTP 터널 개통 실패: {e}")
        return None


# 사용 중인 close SFTP 연결이나 프로세스를 종료한다.
def close_sftp():
    global _transport, _sftp
    try:
        if _sftp: _sftp.close()
        if _transport: _transport.close()
    except Exception: pass
    finally:
        _sftp = None
        _transport = None


# 후속 처리에 필요한 원격 디렉터리 if not 값이나 구조를 생성한다.
def create_remote_dir_if_not_exists(sftp_client, remote_path):
    dirs = []
    dir_path = remote_path
    while len(dir_path) > 1:
        try:
            sftp_client.stat(dir_path)
            break
        except IOError:
            dirs.append(dir_path)
            dir_path = os.path.dirname(dir_path)
    while dirs:
        dir_path = dirs.pop()
        try: sftp_client.mkdir(dir_path)
        except Exception: pass


# 처리 결과나 상태 정보를 외부 서버로 전송한다.
def upload_selected_question_file(local_file_path, keyword_folder_name):

    rel_path = os.path.relpath(local_file_path, WATCH_DIR).replace("\\", "/")
    remote_file_path = f"{REMOTE_TARGET_DIR}/{rel_path}"
    remote_dir_path  = os.path.dirname(remote_file_path)

    sftp_client = get_sftp()
    if not sftp_client:
        print(f" [전송 스킵] H200 서버가 오프라인이거나 세션이 만료되었습니다: {local_file_path.name}")
        return

    try:
        create_remote_dir_if_not_exists(sftp_client, remote_dir_path)
        sftp_client.put(str(local_file_path), remote_file_path)
        print(f" [선별 전송 성공]  H200 서버로 전달 완료: {rel_path}")
    except Exception as e:
        print(f" [전송 오류 발생] 재접속 후 세션 복구 시도... ({e})")
        close_sftp()
        try:
            sftp_client = get_sftp()
            create_remote_dir_if_not_exists(sftp_client, remote_dir_path)
            sftp_client.put(str(local_file_path), remote_file_path)
            print(f" [재전송 성공] 세션 복구 및 업로드 완료: {rel_path}")
        except Exception as e2:
            print(f" [최종 전송 실패] 파일 드롭됨: {e2}")




# call_ollama_sync 작업에 필요한 핵심 처리를 수행한다.
def call_ollama_sync(raw_text):
    payload = {
        "model":  MODEL_NAME,
        "stream": False,
        "format": "json",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT.strip()},
            {"role": "user",   "content": f"[분석할 글]\n{trim_text(remove_urls(raw_text), MAX_CHARS)}\n\n위 JSON 형식으로만 답변해."}
        ],
        "options": {
            "num_predict":    NUM_PREDICT,
            "num_batch":      512,
            "temperature":    0.3,
            "top_p":          0.9,
            "repeat_penalty": 1.02,
            "num_ctx":        8192,
        }
    }
    try:
        resp = requests.post(f"{OLLAMA_BASE_URL}/api/chat", json=payload, timeout=600)
        resp.raise_for_status()
        return resp.json().get("message", {}).get("content", "")
    except Exception as e:
        print(f" [Worker-{WORKER_ID}] Ollama 오류: {e}")
        return None




# analyze_single_file 작업에 필요한 핵심 처리를 수행한다.
def analyze_single_file(file_path, csv_path, keyword_folder):
    start = time.time()
    try:
        text_content = file_path.read_text(encoding="utf-8")
        lines = text_content.split("\n")
        cleaned = "\n".join(l for l in lines if not l.startswith("URL:") and not l.startswith("Hash:"))

        for attempt in range(1, MAX_RETRIES + 1):
            raw = call_ollama_sync(cleaned)
            if not raw:
                print(f" [Worker-{WORKER_ID}] LLM 무응답 (시도 {attempt}/{MAX_RETRIES}): {file_path.name}")
                continue
            try:
                obj = json.loads(fix_json_string(raw))

                model_raw = obj.get("model", "없음")
                if isinstance(model_raw, list):
                    model_raw = ", ".join(str(x) for x in model_raw if x)

                date_raw = obj.get("date", "알수없음")
                if isinstance(date_raw, list):
                    date_raw = date_raw[0] if date_raw else "알수없음"

                summary = obj.get("summary", "내용 부족").replace("\n", " ").strip()
                if not summary or len(summary) <= 5:
                    summary = "내용 부족"

                slangs = set()
                for item in obj.get("slang_list", []):
                    if isinstance(item, str) and item.strip():
                        slangs.add(item.strip())
                    elif isinstance(item, list):
                        for sub in item:
                            if isinstance(sub, str) and sub.strip():
                                slangs.add(sub.strip())


                is_question = obj.get("is_question", False)
                if isinstance(is_question, str):
                    is_question = True if is_question.lower() == "true" else False


                if is_question:
                    upload_selected_question_file(file_path, keyword_folder.name)

                rel = get_relative_path(file_path, keyword_folder)
                with open(csv_path, "a", encoding="utf-8-sig", newline="") as f:
                    csv.writer(f).writerow([rel, len(text_content), date_raw,
                                            model_raw or "없음", summary, ", ".join(slangs), is_question])

                print(f" [Worker-{WORKER_ID}] 완료: {rel} | {time.time()-start:.1f}초 (질문글 여부: {is_question})")
                return True

            except json.JSONDecodeError as e:
                print(f" [Worker-{WORKER_ID}] JSON 파싱 에러 (시도 {attempt}/{MAX_RETRIES}): {e}")
                continue

        print(f" [Worker-{WORKER_ID}] 최종 실패 ({MAX_RETRIES}회 소진): {file_path.name}")
        return False
    except Exception as e:
        print(f" [Worker-{WORKER_ID}] 처리 오류 ({file_path.name}): {e}")
        return False




# 관련 Gemini 키워드 작업을 실행하고 필요한 후속 처리를 수행한다.
def run_gemini_for_keywords(keyword_list):
    if not GEMINI_SCRIPT.exists():
        print(f"  Gemini 스크립트 없음: {GEMINI_SCRIPT}")
        return

    for kw in keyword_list:
        kw_dir = WATCH_DIR / kw

        (kw_dir / ".llm_end_time").write_text(str(time.time()), encoding="utf-8")

        print(f"\nGemini 분석 시작: '{kw}'")
        gemini_start = time.time()
        result = subprocess.run(
            [sys.executable, str(GEMINI_SCRIPT), "--keyword", kw],
            cwd=str(BASE_DIR),
        )
        gemini_end = time.time()

        if result.returncode == 0:
            print(f" Gemini 완료: '{kw}'")
        else:
            print(f" Gemini 실패 (exit {result.returncode}): '{kw}'")

        start_ts     = read_ts(kw_dir, ".start_time")       
        crawl_end_ts = read_ts(kw_dir, ".crawl_end_time")   
        llm_start_ts = read_ts(kw_dir, ".llm_start_time")   
        llm_end_ts   = read_ts(kw_dir, ".llm_end_time")     

        crawl_sec   = (crawl_end_ts - start_ts)     if (start_ts and crawl_end_ts)           else None
        llm_sec     = (llm_end_ts   - llm_start_ts) if (llm_start_ts and llm_end_ts)        else None
        gemini_sec  = gemini_end - gemini_start
        total_sec   = (gemini_end   - start_ts)     if start_ts                              else None

        print(f"\n{'='*52}")
        print(f"   [{kw}] 구간별 소요 시간")
        print(f"  {'─'*48}")
        print(f"    크롤링       : {fmt_seconds(crawl_sec)}")
        print(f"   LLM 분석     : {fmt_seconds(llm_sec)}")
        print(f"   Gemini 분석  : {fmt_seconds(gemini_sec)}")
        print(f"  {'─'*48}")
        print(f"    전체 총합    : {fmt_seconds(total_sec)}")
        print(f"{'='*52}")

        result_json_path = kw_dir / f"{kw}_result.json"

        if result_json_path.exists():
            try:
                with open(result_json_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                

                data["elapsed_time"] = {
                    "crawling":     fmt_seconds(crawl_sec),
                    "llm_analysis": fmt_seconds(llm_sec),
                    "gemini":       fmt_seconds(gemini_sec),
                    "total":        fmt_seconds(total_sec),
                }
                
                with open(result_json_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                print(f" [Worker] 소요시간이 {kw}_result.json 에 성공적으로 저장되었습니다.")
            except Exception as e:
                print(f" [Worker] result.json elapsed_time 업데이트 실패: {e}")




# 스크립트의 주요 실행 흐름을 시작하고 전체 작업을 조율한다.
async def main():
    WATCH_DIR.mkdir(parents=True, exist_ok=True)

    print("\n" + "="*60)
    print(f"    실시간 스마트 분석기 & 필터 업로더 (Worker-{WORKER_ID})")
    print(f"    모델: {MODEL_NAME} | 워커: {WORKER_ID}/{TOTAL_WORKERS}")
    print("="*60)


    test_sftp = get_sftp()
    if test_sftp:
        print(" 학교 서버(H200) SFTP 실시간 스마트 전송 모드가 정상 활성화되었습니다.")
    
    IDLE_CYCLES = max(1, IDLE_TIMEOUT // 2)
    ignored     = set()
    kw_states   = {}
    loop        = asyncio.get_event_loop()
    executor    = ThreadPoolExecutor(max_workers=MAX_WORKERS)

    try:
        while True:
            kw_folders = [f for f in WATCH_DIR.iterdir() if f.is_dir()]
            found_new  = False

            for kw_folder in kw_folders:
                kw = kw_folder.name

                if kw.lower() == "trash": continue
                if kw in ignored: continue

                csv_path  = kw_folder / f"{kw}_result.csv"
                done_file = kw_folder / f"{kw}.done"

                if done_file.exists():
                    print(f"  [Worker-{WORKER_ID}] '{kw}' 이미 완료. 스킵.")
                    ignored.add(kw)
                    continue

                if kw not in kw_states:
                    kw_states[kw] = {"seen_files": set(), "idle_count": 0, "csv_initialized": False}
                    print(f" [Worker-{WORKER_ID}] 감시 시작: '{kw}'")

                state = kw_states[kw]

                if WORKER_ID == 0 and not state["csv_initialized"]:
                    if not csv_path.exists():
                        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
                            csv.writer(f).writerow(["file", "chars", "date", "model", "summary", "slang_list", "is_question"])
                    state["csv_initialized"] = True

                if csv_path.exists():
                    state["csv_initialized"] = True

                current_files = set()
                for site_folder in kw_folder.iterdir():
                    if site_folder.is_dir():
                        if site_folder.name.lower() == "trash":
                            continue
                        current_files.update(site_folder.glob("*.txt"))

                new_files = current_files - state["seen_files"]
                if new_files:
                    found_new = True
                    state["idle_count"] = 0

                    for fp in sorted(new_files):
                        state["seen_files"].add(fp)

                        llm_start_file = kw_folder / ".llm_start_time"
                        if not llm_start_file.exists():
                            llm_start_file.write_text(str(time.time()), encoding="utf-8")

                        idx = extract_file_index(fp)
                        if idx != -1 and idx % TOTAL_WORKERS != WORKER_ID:
                            continue

                        print(f" [Worker-{WORKER_ID}] 분석 시작: {get_relative_path(fp, kw_folder)}")
                        await loop.run_in_executor(executor, analyze_single_file, fp, csv_path, kw_folder)


            if not found_new and kw_states:
                for kw, s in list(kw_states.items()):
                    if kw in ignored:
                        continue

                    s["idle_count"] += 1
                    crawler_is_done = (WATCH_DIR / kw / ".crawler_done").exists()

                    if crawler_is_done or s["idle_count"] >= IDLE_CYCLES:
                        if crawler_is_done:
                            print(f"\n 크롤링 완료 마커 감지! '{kw}' 대기시간 생략 및 즉시 Gemini 시작...")
                        else:
                            print(f"\n '{kw}' 대기 시간 종료. .done 생성 후 Gemini 시작...")

                        done = WATCH_DIR / kw / f"{kw}.done"
                        if not done.exists():
                            done.touch()
                        run_gemini_for_keywords([kw])
                        ignored.add(kw)

            if kw_states and all(kw in ignored for kw in kw_states):
                print(f" [Worker-{WORKER_ID}] 모든 작업 완료. 종료합니다.")
                break

            await asyncio.sleep(2)

    finally:
        close_sftp()
        executor.shutdown(wait=False)
        print(f" [Worker-{WORKER_ID}] 프로세스 종료됨")


        sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())