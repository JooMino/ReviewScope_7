

import csv
import json
import time
import sys
import argparse
import re
import os
from pathlib import Path
from google import genai
from google.genai import types
from dotenv import load_dotenv
load_dotenv()




GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL   = "gemini-2.5-flash"
MAX_RETRIES    = 3





RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "positive": {
            "type": "object",
            "properties": {
                "synthesis": {"type": "string"},
                "key_points": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "point":  {"type": "string"},
                            "dates":  {"type": "string"},
                            "files":  {"type": "string"},
                        },
                        "required": ["point", "dates", "files"]
                    }
                }
            },
            "required": ["synthesis", "key_points"]
        },
        "negative": {
            "type": "object",
            "properties": {
                "synthesis": {"type": "string"},
                "key_points": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "point":  {"type": "string"},
                            "dates":  {"type": "string"},
                            "files":  {"type": "string"},
                        },
                        "required": ["point", "dates", "files"]
                    }
                }
            },
            "required": ["synthesis", "key_points"]
        },
        "other_products": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "product_name": {"type": "string"},
                    "mention_count": {"type": "string"},
                    "context":      {"type": "string"},
                    "dates":        {"type": "string"},
                },
                "required": ["product_name", "mention_count", "context", "dates"]
            }
        },













    },
    "required": ["positive", "negative", "other_products"]

}

SYSTEM_PROMPT = """
너는 한국 커뮤니티 게시글 종합 분석 전문가다.
아래 CSV 데이터의 모든 게시글을 읽고, 검색 대상 제품 '{keyword}'에 대한 의견을
항목별로 나열하지 말고, 아래 기준에 따라 반드시 종합하여 서술하라.
예시1: '{keyword}'가 '아이폰 16 프로'일때, '{keyword}' 맥스는 다른 모델이므로 아이폰 16 프로와 아이폰 16 프로 맥스의 평가를 반드시 구분할 것, 다른 시리즈(13,14,15,16,17 등)에도 마찬가지임.
예시2: '{keyword}'가 '갤럭시탭 s8'일때, '{keyword}' 플러스나 '{keyword}' 울트라나 '{keyword}' FE는 다른 모델이므로 평가를 반드시 구분할 것, 다른 시리즈(9,10,11 등)에도 마찬가지임.

[분석 기준]

1. positive (긍정 종합)
   - synthesis: 전체 긍정 의견을 하나의 문단으로 종합 서술
    예시1: '{keyword}'가 '아이폰 16 프로'일때, '{keyword}' 맥스는 다른 모델이므로 아이폰 16 프로와 아이폰 16 프로 맥스의 평가를 반드시 구분할 것, 다른 시리즈(13,14,15,16,17 등)에도 마찬가지임.
    예시2: '{keyword}'가 '갤럭시탭 s8'일때, '{keyword}' 플러스나 '{keyword}' 울트라나 '{keyword}' FE는 다른 모델이므로 평가를 반드시 구분할 것, 다른 시리즈(9,10,11 등)에도 마찬가지임.
    → 요약이 길어지지 않도록 최대한 5줄로 마칠 것
   - key_points: 반복적으로 등장한 긍정 포인트를 개별 항목으로 추출
     → point는 반드시 아래 규칙을 따를 것:
        ① slang_list의 은어를 표준어로 바꾸지 말고 원문 그대로 문장 안에 사용
        ② 은어를 별도 항목으로 나열하지 말고 서술문 안에 자연스럽게 녹일 것
     → dates: 해당 포인트가 언급된 날짜들 (쉼표 구분)
     → files: 해당 포인트의 근거가 되는 파일명을 CSV의 file 컬럼에서 그대로 골라 3~4개만 쉼표로 나열 (없으면 빈 문자열)
     → files: 같은 사이트 url 뒷부분의 글 번호가 같다면 같은 주소이므로 제외함

2. negative (부정 종합)
   - synthesis: 전체 부정 의견을 하나의 문단으로 종합 서술
        예시1: '{keyword}'가 '아이폰 16 프로'일때, '{keyword}' 맥스는 다른 모델이므로 아이폰 16 프로와 아이폰 16 프로 맥스의 평가를 반드시 구분할 것, 다른 시리즈(13,14,15,16,17 등)에도 마찬가지임.
        예시2: '{keyword}'가 '갤럭시탭 s8'일때, '{keyword}' 플러스나 '{keyword}' 울트라나 '{keyword}' FE는 다른 모델이므로 평가를 반드시 구분할 것, 다른 시리즈(9,10,11 등)에도 마찬가지임.
    → 요약이 길어지지 않도록 최대한 5줄로 마칠 것
   - key_points: 반복적으로 등장한 부정 포인트를 개별 항목으로 추출
     → point는 반드시 아래 규칙을 따를 것:
        ① slang_list의 은어를 표준어로 바꾸지 말고 원문 그대로 문장 안에 사용
        ② 은어를 별도 항목으로 나열하지 말고 서술문 안에 자연스럽게 녹일 것
     → dates: 해당 포인트가 언급된 날짜들 (쉼표 구분)
     → files: 해당 포인트의 근거가 되는 파일명을 CSV의 file 컬럼에서 그대로 골라 3~4개만 쉼표로 나열 (없으면 빈 문자열)
     → files: 같은 사이트 url 뒷부분의 글 번호가 같다면 같은 주소이므로 제외함

3. other_products (타 제품 언급)
   - '{keyword}' 이외에 비교/언급된 타 제품명 목록
   - '{keyword}' 자체는 절대 포함하지 말 것(타 제품과 함께 비교되는 것은 허용)
   - '{keyword}' 이외에 비교/언급된 타 제품 중 언급 빈도가 높은 상위 5개만 추출
   - 제품당 '{keyword}'가 왜 언급되었는지 간단하게 적을 것
   - 유사한 제품(같은 브랜드 라인업)은 가장 많이 언급된 1개로 합칠 것

[공통 규칙]
- date/dates는 반드시 CSV의 date 컬럼에 실제로 존재하는 값만 사용할 것.
- CSV에 없는 날짜를 절대 추가하거나 생성하지 말 것.
- file은 CSV의 file 컬럼 값을 그대로 사용
- 익명 보장을 위해 작성자나 댓글 작성자의 닉네임이나 ip는 표기하지 말것
"""









# 필요한 CSV 데이터를 읽어 이후 처리에 사용할 형태로 준비한다.
def load_csv(csv_path: Path) -> str:
    """CSV를 Gemini가 읽기 좋은 텍스트 블록으로 변환 (헤더 유무 자동 감지)"""
    FIELDNAMES = ["file", "chars", "date", "model", "summary", "slang_list"]

    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        first_line = f.readline().strip()
        f.seek(0)


        if "file" in first_line.lower():
            reader = csv.DictReader(f)
        else:
            reader = csv.DictReader(f, fieldnames=FIELDNAMES)

        rows = []
        for row in reader:
            rows.append(
                f"[파일: {row['file']}]\n"
                f"날짜: {row['date']}\n"
                f"모델: {row['model']}\n"
                f"요약: {row['summary']}\n"
                f"은어: {row['slang_list']}\n"
            )

    print(f"   → {len(rows)}행 로드 완료")
    return "\n---\n".join(rows)





# call_gemini 작업에 필요한 핵심 처리를 수행한다.
def call_gemini(client, csv_text: str, keyword: str) -> dict | None:
    prompt = (
        f"{SYSTEM_PROMPT.format(keyword=keyword)}\n\n"
        f"[CSV 데이터]\n{csv_text}\n\n"
        "위 데이터를 분석하여 JSON으로 출력해."
    )

    print(f"   → 프롬프트 크기: 약 {len(prompt)} 글자")

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=RESPONSE_SCHEMA,
                    temperature=0.1,
                    max_output_tokens=32768,
                    thinking_config=types.ThinkingConfig(
                        thinking_budget=4096
                    ),
                    safety_settings=[
                        types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_HARASSMENT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
                        types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH, threshold=types.HarmBlockThreshold.BLOCK_NONE),
                        types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
                        types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
                    ]
                ),
            )
            

            if not response.text:
                print(f"\n 응답이 비어있습니다. (finish_reason: {response.candidates[0].finish_reason if response.candidates else '알수없음'})")
                raise Exception("Empty response text")
                
            text = response.text.strip()
            text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text, flags=re.MULTILINE).strip()
            
            try:

                return json.loads(text, strict=False)
            except json.JSONDecodeError as je:
                print(f"\n JSON 파싱 실패 (시도 {attempt}): {je}")
                print(f"--- [Gemini 응답 원본 (일부)] ---\n{text[:500]}...\n-----------------------")
                if response.candidates and response.candidates[0].finish_reason:
                    print(f"   중단 사유(finish_reason): {response.candidates[0].finish_reason}")
                raise Exception("잘못된 JSON 형식 반환됨")

        except Exception as e:
            err_str = str(e)
            

            print(f"\n [오류 상세] (시도 {attempt}/{MAX_RETRIES})")
            print(f"에러 타입: {type(e).__name__}")
            print(f"에러 내용: {err_str}")
            

            if hasattr(e, 'code'):
                print(f"HTTP 상태 코드: {e.code}")
            if hasattr(e, 'message'):
                print(f"API 메시지: {e.message}")
            if hasattr(e, 'status'):
                print(f"API 상태: {e.status}")


            if "429" in err_str and "daily" in err_str.lower():
                print(" 일일 한도(RPD) 초과. 내일 재실행하세요.")
                os._exit(1)
            

            if "token" in err_str.lower():
                print(" 토큰 한도 관련 오류가 감지되었습니다. (너무 많은 입력 또는 출력 한도 초과)")

            wait = 2 ** attempt
            print(f"   → {wait}초 대기 후 재시도합니다...\n")
            time.sleep(wait)

    return None




# 필요한 verify 제품 이름 조건을 확인하고 실행 가능 상태를 보장한다.
def verify_product_name(client, nickname: str, keyword: str) -> dict:
    prompt = (
        f"한국 커뮤니티에서 '{nickname}'이라고 불리는 제품의 정확한 제품명과 제조사를 알려줘.\n"
        f"참고로 이 제품은 '{keyword}' 관련 커뮤니티 글에서 언급된 제품이야.\n"
        f"'{nickname}' 자체가 무엇인지만 정확히 식별해. '{keyword}'와 동일한 제품일 필요는 없어.\n"
        f"JSON 형식으로만 답해: "
        f'{{ "official_name": "정식 제품명", "brand": "제조사", "confirmed": true }}'
    )


    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    tools=[types.Tool(google_search=types.GoogleSearch())],
                    temperature=0.0,
                ),
            )
            text = response.text.strip()
            text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text, flags=re.MULTILINE).strip()
            

            match = re.search(r'\{.*\}', text, flags=re.DOTALL)
            if match:
                text = match.group(0)


            obj  = json.loads(text)
            return obj

        except Exception as e:
            err_str = str(e)


            if "503" in err_str or "UNAVAILABLE" in err_str:
                wait = 5 * (2 ** attempt)
                print(f"\n   ⏳ 503 과부하 (시도 {attempt}/{MAX_RETRIES}) → {wait}초 대기 후 재시도")
                time.sleep(wait)
                continue


            if "429" in err_str:
                print(f"\n   ⏳ 429 한도 초과 → 60초 대기")
                time.sleep(60)
                continue


            if "daily" in err_str.lower():
                print("\n 일일 한도(RPD) 초과. 내일 재실행하세요.")
                os._exit(1)


            print(f"\n  알 수 없는 오류: {e}")
            break

    return {"official_name": nickname, "brand": "알수없음", "confirmed": False}


# enrich_other_products 작업에 필요한 핵심 처리를 수행한다.
def enrich_other_products(client, other_products: list, keyword: str, delay: float = 6.0) -> list:
    """
    other_products 배열의 각 product_name을 Google Search로 검증하여
    official_name, brand 필드를 추가
    """

    seen      = {}
    enriched  = []

    for item in other_products:
        nickname = item.get("product_name", "")

        if nickname not in seen:
            print(f" 검색 중: '{nickname}' ...", end=" ", flush=True)
            time.sleep(delay)
            result = verify_product_name(client, nickname, keyword)
            seen[nickname] = result
            print(f"→ {result.get('brand', '?')} / {result.get('official_name', '?')} "
                  f"{'' if result.get('confirmed') else ''}")
        else:
            result = seen[nickname]

        enriched.append({
            **item,
            "official_name": result.get("official_name", nickname),
            "brand":         result.get("brand",         "알수없음"),
            "confirmed":     result.get("confirmed",     False),
        })

    return enriched





# 스크립트의 주요 실행 흐름을 시작하고 전체 작업을 조율한다.
def main():
    p = argparse.ArgumentParser()
    p.add_argument("--keyword", required=True, help="검색 키워드")
    p.add_argument("--csv",                    help="분석할 CSV 파일 경로 (미지정 시 자동 탐색)")
    p.add_argument("--out",                    help="출력 폴더 (미지정 시 data_storage/{keyword}/)")
    args = p.parse_args()

    keyword = args.keyword.strip()


    if args.csv:
        csv_path = Path(args.csv)
    else:
        base = Path("data_storage") / keyword
        candidates = sorted(base.glob("*.csv"))
        if not candidates:
            print(f" CSV 파일을 찾을 수 없습니다: {base}/*.csv")
            os._exit(1)
        csv_path = candidates[0]
        print(f" CSV 자동 탐색: {csv_path}")


    out_dir = Path(args.out) if args.out else Path("data_storage") / keyword
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f" 키워드: [{keyword}]")
    print(f" CSV 로딩: {csv_path}")
    csv_text = load_csv(csv_path)
    print(f"   → {len(csv_text)}자 변환 완료\n")

    print(" Gemini 분석 중...")
    client = genai.Client(api_key=GEMINI_API_KEY)
    result = call_gemini(client, csv_text, keyword)

    if not result:
        print(" 분석 실패")
        return

    print("\n 타 제품명 Google Search 검증 중...")
    result["other_products"] = enrich_other_products(
        client, result.get("other_products", []), keyword
    )


    merged = {
        "keyword":        keyword,
        "positive":       result.get("positive",       {}),
        "negative":       result.get("negative",       {}),
        "other_products": result.get("other_products", []),

    }
    result_path = out_dir / f"{keyword}_result.json"
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    



    pos_count = len(merged["positive"].get("key_points", []))
    neg_count = len(merged["negative"].get("key_points", []))
    print(f"\n result.json 저장 완료")
    print(f"   긍정 포인트: {pos_count}개 | 부정 포인트: {neg_count}개")
    if merged["positive"].get("key_points"):
        sample = merged["positive"]["key_points"][0]
        print(f"    긍정 샘플 files: {sample.get('files', '없음')}")
    print(f"   타 제품: {len(merged['other_products'])}개")
    if merged["negative"].get("key_points"):
        sample = merged["negative"]["key_points"][0]
        print(f"    부정 샘플 files: {sample.get('files', '없음')}")

    print(f"\n 출력 폴더: {out_dir.resolve()}")


if __name__ == "__main__":
    main()
