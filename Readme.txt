site_worker.py

서버의 작업 큐에서 키워드를 받아 자동으로 크롤링, 분석, 결과 전송까지 수행하는 워커이다.
서버에서 작업을 내려주는 자동 처리 방식에서 사용한다.

실행:
python site_worker.py


server_upload_worker.py

사용자가 직접 입력한 키워드나 keywords.txt 파일을 처리한 뒤, 결과 JSON을 merge worker 서버로 업로드하는 스크립트이다.
서버 작업 큐를 기다리지 않고, 직접 지정한 키워드를 실행할 때 사용한다.

단일 키워드 실행:
python server_upload_worker.py "검색어" --sites dc,clien,fmk,quasar

키워드 파일 실행:
python server_upload_worker.py --keywords-file keywords.txt --sites dc,clien,fmk,quasar

기존 수집 데이터만 분석 후 업로드:
python server_upload_worker.py "검색어" --llm-only


차이 요약

site_worker.py는 서버가 내려주는 작업을 받아 자동으로 처리한다.
server_upload_worker.py는 사용자가 직접 지정한 키워드를 처리해서 서버로 업로드한다.