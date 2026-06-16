site_worker.py

서버의 작업 큐에서 키워드를 받아 자동으로 크롤링, 분석, 결과 전송까지 수행하는 워커이다.
서버에서 작업을 내려주는 자동 처리 방식에서 사용한다.

실행:
python site_worker.py


server_upload_worker.py

사용자가 직접 입력한 키워드나 keywords.txt 파일을 처리한 뒤, 결과 JSON을 Oracle Cloud 서버로 업로드하는 스크립트이다.
서버 작업 큐를 기다리지 않고, 직접 지정한 키워드를 실행할 때 사용한다.

단일 키워드 실행:
python server_upload_worker.py "검색어" --sites dc,clien,fmk,quasar

키워드 파일 실행:
python server_upload_worker.py --keywords-file keywords.txt --sites dc,clien,fmk,quasar

기존 수집 데이터만 분석 후 업로드:
python server_upload_worker.py "검색어" --llm-only


## 데이터 저장 과정

- 로컬 또는 조원 PC에서 `server_upload_worker.py`를 실행하면 크롤링, LLM 분석, 딥서치가 순서대로 수행됩니다.
- 분석이 끝나면 결과 JSON이 우분투 서버의 `server_upload_receiver.py`로 전송됩니다.
- Oracle Cloud 서버에서 실행 중인 `server_upload_receiver.py`는 결과 JSON과 언급량 데이터를 병합한 뒤 Oracle Cloud 안의 mysql DB로 업로드합니다.
- 업로드된 데이터는 DB에 누적 저장되며, 이후 리뷰 분석 결과 조회와 통계 시각화에 활용됩니다.
