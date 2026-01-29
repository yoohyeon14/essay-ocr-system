# 논술 OCR 시스템

학생 논술 답안 원고지를 OCR로 인식하고 Google Sheets에 자동 저장하는 시스템

## 기능

- PDF 업로드 → 이미지 추출
- 답안 영역 자동 크롭
- 네이버 CLOVA OCR로 텍스트 추출
- Gemini AI로 텍스트 복원/정리 (기초자료 참고)
- Google Sheets 자동 저장

## 설치

```bash
pip install -r requirements.txt
```

## 환경변수 설정

`.env` 파일 또는 Streamlit secrets에 다음 값 설정:

```
# Google
GOOGLE_API_KEY=your_gemini_api_key
GOOGLE_SPREADSHEET_ID=your_spreadsheet_id
GOOGLE_SERVICE_ACCOUNT_FILE=credentials.json

# Naver CLOVA OCR
CLOVA_OCR_API_URL=https://xxxxx.apigw.ntruss.com/custom/v1/xxxxx/general
CLOVA_OCR_SECRET_KEY=your_secret_key
```

## 실행

```bash
streamlit run ocr_web_app.py
```

## Streamlit Cloud 배포

1. GitHub에 push
2. [share.streamlit.io](https://share.streamlit.io) 접속
3. 저장소 연결
4. Secrets에 환경변수 설정

## 기술 스택

- Streamlit (웹 UI)
- 네이버 CLOVA OCR (텍스트 추출)
- Google Gemini (텍스트 복원)
- Google Sheets API (데이터 저장)
- pdf2image (PDF 처리)
