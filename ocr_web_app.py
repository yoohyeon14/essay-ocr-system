"""
논술 OCR 웹앱 (Streamlit)

기능:
1. PDF 업로드 → 이미지 추출
2. 헤더 OCR → 학생명/강/문항 자동 추출
3. 기초자료 자동 로드 → Gemini Vision OCR
4. OCR 결과 확인/수정
5. Google Sheets 자동 저장

실행:
    streamlit run ocr_web_app.py
"""

import os
import io
import re
from pathlib import Path
from typing import Optional, List, Dict
from dotenv import load_dotenv
import streamlit as st

load_dotenv()

# ============================================================
# 페이지 설정
# ============================================================

st.set_page_config(
    page_title="박기호논술 OCR 시스템",
    page_icon="📝",
    layout="wide"
)

# ============================================================
# 상수
# ============================================================

# 학원명 매핑
ACADEMY_MAPPING = {
    "김포각인": "김포 각인", "김포": "김포 각인", "각인": "김포 각인",
    "본원": "본원", "대치박기호": "본원", "박기호": "본원",
    "분당러셀": "분당 러셀", "분당": "분당 러셀", "러셀분당": "분당 러셀",
    "대치러셀": "대치 러셀", "대치": "대치 러셀", "러셀대치": "대치 러셀",
}

# 헤더 노이즈 단어
NOISE_WORDS = [
    "수험생", "유의사항", "답안지", "필기구", "작성", "검정색", "볼펜",
    "소속학원", "학생", "이름", "첨삭", "담임", "작성일자", "논술",
    "제출", "사용", "금지", "문제", "실전", "원고지"
]


# ============================================================
# 세션 상태 초기화
# ============================================================

if "students_data" not in st.session_state:
    st.session_state.students_data = []  # 학생별 데이터

if "processing_complete" not in st.session_state:
    st.session_state.processing_complete = False

# 크롭 영역 좌표 (비율로 저장: 0~1)
if "crop_coords_q1" not in st.session_state:
    # 1번 문항 기본값 (홀수 페이지)
    st.session_state.crop_coords_q1 = {
        "left": 0.03,
        "top": 0.15,
        "right": 0.66,
        "bottom": 0.74
    }

if "crop_coords_q2" not in st.session_state:
    # 2번 문항 기본값 (짝수 페이지)
    st.session_state.crop_coords_q2 = {
        "left": 0.03,
        "top": 0.07,
        "right": 0.66,
        "bottom": 0.84
    }

if "crop_calibrated" not in st.session_state:
    st.session_state.crop_calibrated = True  # 기본값이 이미 보정됨


# ============================================================
# Google Sheets 연동
# ============================================================

@st.cache_resource
def get_sheets_client():
    """Google Sheets 클라이언트 (캐시)"""
    try:
        import gspread
        from google.oauth2.service_account import Credentials
        import json
        
        SCOPES = [
            'https://www.googleapis.com/auth/spreadsheets',
            'https://www.googleapis.com/auth/drive'
        ]
        
        # spreadsheet_id: 환경변수 또는 Streamlit secrets
        spreadsheet_id = os.getenv("GOOGLE_SPREADSHEET_ID")
        if not spreadsheet_id and hasattr(st, 'secrets'):
            spreadsheet_id = st.secrets.get("GOOGLE_SPREADSHEET_ID")
        
        if not spreadsheet_id:
            return None, "GOOGLE_SPREADSHEET_ID 환경변수 필요"
        
        # credentials 처리
        credentials_path = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "credentials.json")
        
        if Path(credentials_path).exists():
            # 로컬: 파일에서 로드
            creds = Credentials.from_service_account_file(credentials_path, scopes=SCOPES)
        elif hasattr(st, 'secrets') and "GOOGLE_SERVICE_ACCOUNT" in st.secrets:
            # Streamlit Cloud: secrets에서 섹션으로 로드
            service_account_info = dict(st.secrets["GOOGLE_SERVICE_ACCOUNT"])
            creds = Credentials.from_service_account_info(service_account_info, scopes=SCOPES)
        elif hasattr(st, 'secrets') and "GOOGLE_SERVICE_ACCOUNT_JSON" in st.secrets:
            # Streamlit Cloud: JSON 문자열로 로드
            service_account_info = json.loads(st.secrets["GOOGLE_SERVICE_ACCOUNT_JSON"])
            creds = Credentials.from_service_account_info(service_account_info, scopes=SCOPES)
        else:
            return None, "credentials.json 파일 또는 GOOGLE_SERVICE_ACCOUNT 필요"
        
        client = gspread.authorize(creds)
        spreadsheet = client.open_by_key(spreadsheet_id)
        
        return spreadsheet, None
        
    except Exception as e:
        return None, str(e)


def get_lesson_prompt(spreadsheet, lesson: int, question_num: int) -> Dict:
    """
    기초자료 시트에서 해당 강/문항의 기초자료 로드
    
    기초자료 시트 구조 (예상):
    - A열: 강
    - B열: 문항
    - C열: 문제
    - D열: 제시문
    - E열: 채점기준
    - F열: 모범답안
    """
    try:
        # "기초자료" 시트에서 로드
        sheet = spreadsheet.worksheet("기초자료")
        all_data = sheet.get_all_values()
        
        st.write(f"   📚 기초자료 시트 로드: {len(all_data)}행")
        
        for row_idx, row in enumerate(all_data[1:], start=2):  # 헤더 제외
            if len(row) >= 2:
                try:
                    row_lesson = int(row[0]) if row[0] else 0
                    row_question = int(row[1]) if row[1] else 0
                    
                    if row_lesson == lesson and row_question == question_num:
                        result = {
                            "question": row[2] if len(row) > 2 else "",
                            "passage": row[3] if len(row) > 3 else "",
                            "rubric": row[4] if len(row) > 4 else "",
                            "model_answer": row[5] if len(row) > 5 else ""
                        }
                        
                        # 디버깅: 로드된 기초자료 요약
                        st.write(f"   ✅ 기초자료 찾음 ({lesson}강 {question_num}번)")
                        st.write(f"      - 문제: {len(result['question'])}자")
                        st.write(f"      - 제시문: {len(result['passage'])}자")
                        st.write(f"      - 채점기준: {len(result['rubric'])}자")
                        st.write(f"      - 모범답안: {len(result['model_answer'])}자")
                        
                        return result
                except ValueError:
                    continue
        
        st.warning(f"   ⚠️ 기초자료 없음: {lesson}강 {question_num}번")
        return {}
        
    except Exception as e:
        st.error(f"   ❌ 기초자료 로드 실패: {e}")
        return {}


def get_students_list(spreadsheet, lesson: int) -> List[Dict]:
    """학생 목록 조회"""
    try:
        sheet = spreadsheet.worksheet(f"{lesson}강")
        all_data = sheet.get_all_values()
        
        students = []
        data_start = 1
        
        for i, row in enumerate(all_data):
            if row and row[0] and row[0] not in ["학생이름", "이름", ""]:
                data_start = i
                break
        
        for i, row in enumerate(all_data[data_start:], start=data_start + 1):
            if row and row[0] and row[0].strip():
                students.append({
                    "row": i,
                    "name": row[0].strip(),
                    "teacher": row[1].strip() if len(row) > 1 else "",
                })
        
        return students
        
    except Exception as e:
        return []


def find_student_row(spreadsheet, lesson: int, student_name: str) -> Optional[int]:
    """학생명으로 행 찾기"""
    students = get_students_list(spreadsheet, lesson)
    
    for student in students:
        # 부분 매칭 허용
        if student_name in student["name"] or student["name"] in student_name:
            return student["row"]
    
    return None


def save_ocr_to_sheet(spreadsheet, lesson: int, row: int, question_num: int, text: str) -> bool:
    """OCR 결과 저장"""
    try:
        sheet = spreadsheet.worksheet(f"{lesson}강")
        col = 8 if question_num == 1 else 15  # H열 또는 O열
        sheet.update_cell(row, col, text)
        return True
    except Exception as e:
        st.error(f"저장 실패: {e}")
        return False


# ============================================================
# Gemini OCR
# ============================================================

def extract_context_keywords(prompt_data: Dict) -> List[str]:
    """기초자료에서 핵심 키워드 추출"""
    all_text = " ".join([
        prompt_data.get("passage", ""),
        prompt_data.get("question", ""),
        prompt_data.get("rubric", ""),
        prompt_data.get("model_answer", "")
    ])
    
    words = re.findall(r'[가-힣]{2,}', all_text)
    
    from collections import Counter
    word_counts = Counter(words)
    return [w for w, c in word_counts.most_common(50)]


def run_header_ocr(image_bytes: bytes) -> Dict:
    """
    헤더 OCR → 학생명/강/문항 추출
    
    Returns:
        {"name": "고훈서", "lesson": 2, "question_num": 1, "academy": "분당 러셀"}
    """
    from google import genai
    from google.genai import types
    import json
    
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key and hasattr(st, 'secrets'):
        api_key = st.secrets.get("GOOGLE_API_KEY")
    
    if not api_key:
        return {"error": "GOOGLE_API_KEY 필요"}
    
    client = genai.Client(api_key=api_key)
    
    prompt = """이 원고지 이미지의 상단 헤더 부분에서 다음 정보를 추출하세요:

1. 학생 이름 (손글씨로 작성된 2-4글자 한글 이름)
2. 강 번호 (예: 1강, 2강, 3강...)
3. 문제 번호 (예: 문제1, 문제2)
4. 소속 학원명

## 출력 형식 (JSON)
```json
{
  "name": "학생이름",
  "lesson": 2,
  "question_num": 1,
  "academy": "학원명"
}
```

정보를 찾을 수 없으면 빈 문자열이나 0으로 표시하세요."""
    
    try:
        import base64
        image_base64 = base64.b64encode(image_bytes).decode('utf-8')
        
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=[
                {
                    "role": "user",
                    "parts": [
                        {"text": prompt},
                        {
                            "inline_data": {
                                "mime_type": "image/png",
                                "data": image_base64
                            }
                        }
                    ]
                }
            ],
            config={"temperature": 0.1}
        )
        
        result_text = response.text.strip()
        
        if "```" in result_text:
            parts = result_text.split("```")
            if len(parts) >= 2:
                result_text = parts[1]
                if result_text.startswith("json"):
                    result_text = result_text[4:]
        
        result = json.loads(result_text.strip())
        
        # 학원명 매핑
        academy = result.get("academy", "")
        academy_normalized = academy.replace(" ", "").lower()
        for keyword, mapped in ACADEMY_MAPPING.items():
            if keyword.lower() in academy_normalized:
                result["academy"] = mapped
                break
        
        return result
        
    except Exception as e:
        return {"error": str(e)}


def crop_answer_area(image_bytes: bytes, is_page1: bool = True) -> bytes:
    """
    답안 영역만 크롭 (세션 상태의 좌표 사용)
    
    Returns:
        크롭된 이미지 bytes
    """
    from PIL import Image
    import io
    
    img = Image.open(io.BytesIO(image_bytes))
    w, h = img.size
    
    # 세션 상태에서 좌표 가져오기
    if is_page1:
        coords = st.session_state.crop_coords_q1
    else:
        coords = st.session_state.crop_coords_q2
    
    x1 = int(w * coords["left"])
    y1 = int(h * coords["top"])
    x2 = int(w * coords["right"])
    y2 = int(h * coords["bottom"])
    
    cropped = img.crop((x1, y1, x2, y2))
    
    # bytes로 변환
    buf = io.BytesIO()
    cropped.save(buf, format='PNG')
    return buf.getvalue()


def run_naver_ocr(image_bytes: bytes) -> tuple:
    """
    네이버 CLOVA OCR로 텍스트 추출
    
    Returns:
        (raw_text, error)
    """
    import requests
    import json
    import base64
    import time
    import uuid
    
    # CLOVA_OCR 또는 NAVER_OCR 둘 다 지원
    api_url = os.getenv("CLOVA_OCR_API_URL") or os.getenv("NAVER_OCR_API_URL")
    secret_key = os.getenv("CLOVA_OCR_SECRET_KEY") or os.getenv("NAVER_OCR_SECRET_KEY")
    
    # 디버깅: 환경변수 로드 확인
    st.write(f"   🔧 API URL 로드: {'✅' if api_url else '❌'}")
    st.write(f"   🔧 Secret Key 로드: {'✅' if secret_key else '❌'}")
    
    if not api_url or not secret_key:
        return "", "CLOVA_OCR_API_URL 또는 CLOVA_OCR_SECRET_KEY 환경변수 필요"
    
    # 이미지를 base64로 인코딩
    image_base64 = base64.b64encode(image_bytes).decode('utf-8')
    
    # 요청 데이터 구성
    request_json = {
        'images': [
            {
                'format': 'png',
                'name': 'answer_sheet',
                'data': image_base64
            }
        ],
        'requestId': str(uuid.uuid4()),
        'version': 'V2',
        'timestamp': int(round(time.time() * 1000))
    }
    
    headers = {
        'X-OCR-SECRET': secret_key,
        'Content-Type': 'application/json'
    }
    
    try:
        response = requests.post(api_url, headers=headers, json=request_json, timeout=30)
        response.raise_for_status()
        
        result = response.json()
        
        # 텍스트 추출
        texts = []
        if 'images' in result and len(result['images']) > 0:
            image_result = result['images'][0]
            if 'fields' in image_result:
                for field in image_result['fields']:
                    text = field.get('inferText', '')
                    if text:
                        texts.append(text)
        
        raw_text = ' '.join(texts)
        return raw_text, None
        
    except requests.exceptions.RequestException as e:
        return "", f"네이버 OCR API 오류: {str(e)}"
    except Exception as e:
        return "", f"OCR 처리 오류: {str(e)}"


def run_gemini_restore(raw_ocr_text: str, prompt_data: Dict = None, image_bytes: bytes = None) -> tuple:
    """
    Gemini로 OCR 텍스트 복원/정리 (이미지 + 텍스트 + 기초자료)
    
    Args:
        raw_ocr_text: 네이버 OCR로 추출한 원시 텍스트
        prompt_data: 기초자료
        image_bytes: 크롭된 답안 이미지 (추가 참고용)
    
    Returns:
        (restored_text, error)
    """
    from google import genai
    import base64
    
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key and hasattr(st, 'secrets'):
        api_key = st.secrets.get("GOOGLE_API_KEY")
    
    if not api_key:
        return raw_ocr_text, "GOOGLE_API_KEY 없음 - 원본 OCR 텍스트 반환"
    
    client = genai.Client(api_key=api_key)
    
    # 기초자료 구성
    context_text = ""
    if prompt_data:
        question = prompt_data.get('question', '')
        passage = prompt_data.get('passage', '')
        rubric = prompt_data.get('rubric', '')
        model_answer = prompt_data.get('model_answer', '')
        
        # 디버깅 정보 세션에 저장 (나중에 확인 가능)
        if "debug_info" not in st.session_state:
            st.session_state.debug_info = []
        
        debug_entry = {
            "question_len": len(question),
            "passage_len": len(passage),
            "rubric_len": len(rubric),
            "model_answer_len": len(model_answer),
            "question_preview": question[:200] + "..." if len(question) > 200 else question,
            "passage_preview": passage[:300] + "..." if len(passage) > 300 else passage,
            "rubric_preview": rubric[:200] + "..." if len(rubric) > 200 else rubric,
        }
        
        with st.expander("🔍 [디버그] 복원에 사용되는 기초자료", expanded=True):
            st.write(f"**문제** ({len(question)}자)")
            st.text(debug_entry["question_preview"])
            st.write(f"**제시문** ({len(passage)}자)")
            st.text(debug_entry["passage_preview"])
            st.write(f"**채점기준** ({len(rubric)}자)")
            st.text(debug_entry["rubric_preview"])
            st.write(f"**모범답안** ({len(model_answer)}자)")
        
        context_text = f"""<기초자료>
<문제>
{question}

<제시문>
{passage}

<채점기준>
{rubric}

<모범답안>
{model_answer}
</기초자료>"""
    else:
        context_text = "(기초자료 없음)"
    
    # 복원 프롬프트 - 이미지 + OCR 텍스트 + 기초자료
    prompt = f"""이미지는 학생이 작성한 논술 답안이고, 아래는 OCR로 인식한 텍스트야.
OCR 텍스트에 오류가 있을 수 있으니, 이미지를 직접 보면서 기초자료를 참고해 정확하게 복원해줘.

핵심 규칙:
1. 이미지에 실제로 쓰여진 글자를 읽어서 복원
2. OCR 텍스트는 참고용 (위치/순서 파악)
3. 기초자료에 나오는 용어와 비슷하면 그 용어로 수정
   예: 이미지에 "공공선"처럼 보이는데 OCR이 "곰곰신"으로 인식했다면 → "공공선"
   
4. 절대 금지:
   - 이미지에 없는 내용 추가 금지
   - 문장 지어내기 금지

5. 출력 형식
    - 원고지 상 줄바꿈 무시하고 문장 단위로 연결!!
    - 문단 구분은 유지(새로운 줄에서 시작)

{context_text}

<OCR 텍스트 (참고용)>
{raw_ocr_text}
</OCR 텍스트>

이미지를 보고 정확하게 복원한 텍스트만 출력해."""
    
    # 디버깅: 전체 프롬프트 확인
    with st.expander("📝 [디버그] Gemini 복원 프롬프트", expanded=True):
        st.code(prompt, language=None)
        st.write(f"**프롬프트 길이**: {len(prompt)}자")
        st.write(f"**이미지 포함**: {'✅' if image_bytes else '❌'}")
    
    try:
        # 이미지가 있으면 이미지 + 텍스트, 없으면 텍스트만
        if image_bytes:
            image_base64 = base64.b64encode(image_bytes).decode('utf-8')
            contents = [
                {
                    "role": "user",
                    "parts": [
                        {"text": prompt},
                        {
                            "inline_data": {
                                "mime_type": "image/png",
                                "data": image_base64
                            }
                        }
                    ]
                }
            ]
        else:
            contents = [{"role": "user", "parts": [{"text": prompt}]}]
        
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=contents,
            config={"temperature": 0.1}
        )
        
        restored_text = response.text.strip()
        return restored_text, None
        
    except Exception as e:
        return raw_ocr_text, f"Gemini 복원 오류: {str(e)}"


def run_answer_ocr(cropped_image_bytes: bytes, prompt_data: Dict = None, ocr_key: str = "") -> tuple:
    """
    답안 OCR: 네이버 OCR → Gemini 복원
    
    Args:
        cropped_image_bytes: 크롭된 답안 영역 이미지
        prompt_data: 기초자료
        ocr_key: 결과 저장용 키 (예: "1_1" = 1번 학생 1번 문항)
    
    Returns:
        (text, confidence, error)
    """
    # 1단계: 네이버 CLOVA OCR로 텍스트 추출 (원본 이미지 사용)
    st.write("   🔤 네이버 OCR 처리 중...")
    raw_text, ocr_error = run_naver_ocr(cropped_image_bytes)
    
    if ocr_error:
        st.error(f"   ❌ OCR 오류: {ocr_error}")
        return "", 0.0, ocr_error
    
    st.write(f"   ✅ OCR 완료: {len(raw_text)}자 추출")
    
    # 2단계: Gemini로 텍스트 복원/정리 (이미지 + OCR 텍스트 + 기초자료)
    st.write("   ✨ Gemini 텍스트 복원 중...")
    restored_text, restore_error = run_gemini_restore(raw_text, prompt_data, cropped_image_bytes)
    
    if restore_error:
        st.warning(f"   ⚠️ 복원 경고: {restore_error}")
    
    st.write(f"   ✅ 복원 완료: {len(restored_text)}자")
    
    # 결과를 세션 상태에 저장 (나중에 비교용)
    if "ocr_debug" not in st.session_state:
        st.session_state.ocr_debug = {}
    
    st.session_state.ocr_debug[ocr_key] = {
        "raw_text": raw_text,
        "restored_text": restored_text,
        "raw_len": len(raw_text),
        "restored_len": len(restored_text)
    }
    
    return (restored_text, 0.9, None)


# ============================================================
# PDF 처리
# ============================================================

def extract_images_from_pdf(pdf_bytes: bytes) -> List[bytes]:
    """PDF에서 이미지 추출"""
    from pdf2image import convert_from_bytes
    
    try:
        poppler_path = None
        possible_paths = [
            Path("poppler-24.08.0/Library/bin"),
            Path("sample_code/poppler-24.08.0/Library/bin"),
            Path(r"C:\poppler\Library\bin"),
        ]
        
        for p in possible_paths:
            if p.exists():
                poppler_path = str(p)
                break
        
        images = convert_from_bytes(pdf_bytes, dpi=200, poppler_path=poppler_path)
        
        image_bytes_list = []
        for img in images:
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            image_bytes_list.append(buf.getvalue())
        
        return image_bytes_list
        
    except Exception as e:
        st.error(f"PDF 변환 실패: {e}")
        return []


# ============================================================
# 메인 처리 함수
# ============================================================

def process_pdf(pdf_bytes: bytes, spreadsheet, lesson: int) -> List[Dict]:
    """
    PDF 전체 처리
    
    구조:
    - 홀수 페이지: 헤더 + 1번 문항 답안
    - 짝수 페이지: 헤더 없음 + 2번 문항 답안 (같은 학생)
    
    Args:
        pdf_bytes: PDF 파일 바이트
        spreadsheet: Google Sheets 객체
        lesson: 강 번호 (사용자 선택)
    
    Returns:
        [{"name": "고훈서", "lesson": 2, "question_num": 1, ...}, 
         {"name": "고훈서", "lesson": 2, "question_num": 2, ...}, ...]
    """
    images = extract_images_from_pdf(pdf_bytes)
    
    if not images:
        st.error("PDF에서 이미지를 추출할 수 없습니다.")
        return []
    
    st.info(f"📄 {len(images)} 페이지 추출됨")
    
    students_data = []
    current_student_info = None  # 헤더에서 추출한 학생 정보 (홀수 페이지에서 설정)
    
    progress = st.progress(0)
    status_text = st.empty()
    
    for idx, image_bytes in enumerate(images):
        page_num = idx + 1
        is_odd = (page_num % 2 == 1)
        
        status_text.text(f"페이지 {page_num}/{len(images)} 처리 중...")
        
        if is_odd:
            # 홀수 페이지: 헤더 OCR + 1번 문항
            st.write(f"🔍 페이지 {page_num}: 헤더 OCR...")
            header_info = run_header_ocr(image_bytes)
            st.write(f"   헤더 결과: {header_info}")
            
            # 1번 문항 처리
            # 1) 먼저 원본 이미지에서 헤더 OCR (학생명, 학원 추출)
            st.write(f"🔍 페이지 {page_num}: 헤더 OCR...")
            header_info = run_header_ocr(image_bytes)  # 원본 이미지 사용
            st.write(f"   헤더 결과: {header_info}")
            
            if "error" in header_info:
                st.warning(f"   헤더 인식 실패: {header_info['error']}")
                student_name = ""
                academy = ""
            else:
                student_name = header_info.get("name", "")
                academy = header_info.get("academy", "")
            
            current_student_info = {
                "name": student_name,
                "lesson": lesson,
                "academy": academy
            }
            
            st.write(f"   👤 학생: {student_name or '(이름 미인식)'}, {lesson}강")
            
            # 2) 기초자료 로드
            prompt_data_q1 = {}
            if spreadsheet:
                prompt_data_q1 = get_lesson_prompt(spreadsheet, lesson, 1)
            
            # 3) 학생 행 찾기
            student_row = None
            if spreadsheet and student_name:
                student_row = find_student_row(spreadsheet, lesson, student_name)
            
            # 4) 답안 영역 크롭
            st.write(f"   📝 1번 문항 OCR...")
            try:
                cropped_img_q1 = crop_answer_area(image_bytes, is_page1=True)
                with st.expander("🔍 [디버그] 1번 문항 크롭된 답안 영역", expanded=True):
                    st.image(cropped_img_q1, caption="크롭된 답안 영역", use_container_width=True)
            except Exception as e:
                st.error(f"크롭 실패: {e}")
                cropped_img_q1 = image_bytes  # 실패 시 원본 사용
            
            # 5) 답안 OCR (크롭된 이미지 사용)
            ocr_key_q1 = f"{student_name}_{lesson}_1"
            text_q1, confidence_q1, error_q1 = run_answer_ocr(cropped_img_q1, prompt_data_q1, ocr_key_q1)
            
            if error_q1:
                st.error(f"   OCR 오류: {error_q1}")
            else:
                st.write(f"   완료: {len(text_q1)}자, 확신도 {confidence_q1:.0%}")
                
                # 1번 문항 저장
                students_data.append({
                    "name": student_name,
                    "lesson": lesson,
                    "question_num": 1,
                    "academy": academy,
                    "row": student_row,
                    "prompt_data": prompt_data_q1,
                    "pages": [page_num],
                    "images": [image_bytes],
                    "status": "matched" if student_row else "unmatched",
                    "text": text_q1,
                    "confidence": confidence_q1
                })
                st.success(f"✅ {student_name} 1번 문항 완료")
        
        else:
            # 짝수 페이지: 2번 문항 (이전 홀수 페이지 학생)
            if not current_student_info:
                st.warning(f"   ⚠️ 페이지 {page_num}: 학생 정보 없음 (이전 홀수 페이지 실패)")
                progress.progress((idx + 1) / len(images))
                continue
            
            # 2번 문항 처리
            # current_student_info에서 학생 정보 가져오기
            student_name = current_student_info["name"]
            academy = current_student_info["academy"]
            
            st.write(f"🔍 페이지 {page_num}: {student_name} 2번 문항")
            
            # 1) 기초자료 로드
            prompt_data_q2 = {}
            if spreadsheet:
                prompt_data_q2 = get_lesson_prompt(spreadsheet, lesson, 2)
            
            # 2) 학생 행 찾기
            student_row = None
            if spreadsheet and student_name:
                student_row = find_student_row(spreadsheet, lesson, student_name)
            
            # 3) 답안 영역 크롭
            st.write(f"   📝 2번 문항 OCR...")
            try:
                cropped_img_q2 = crop_answer_area(image_bytes, is_page1=False)
                with st.expander("🔍 [디버그] 2번 문항 크롭된 답안 영역", expanded=True):
                    st.image(cropped_img_q2, caption="크롭된 답안 영역", use_container_width=True)
            except Exception as e:
                st.error(f"크롭 실패: {e}")
                cropped_img_q2 = image_bytes  # 실패 시 원본 사용
            
            # 4) 답안 OCR (크롭된 이미지 사용)
            ocr_key_q2 = f"{student_name}_{lesson}_2"
            text_q2, confidence_q2, error_q2 = run_answer_ocr(cropped_img_q2, prompt_data_q2, ocr_key_q2)
            
            if error_q2:
                st.error(f"   OCR 오류: {error_q2}")
            else:
                st.write(f"   완료: {len(text_q2)}자, 확신도 {confidence_q2:.0%}")
                
                # 2번 문항 저장
                students_data.append({
                    "name": student_name,
                    "lesson": lesson,
                    "question_num": 2,
                    "academy": academy,
                    "row": student_row,
                    "prompt_data": prompt_data_q2,
                    "pages": [page_num],
                    "images": [image_bytes],
                    "status": "matched" if student_row else "unmatched",
                    "text": text_q2,
                    "confidence": confidence_q2
                })
                st.success(f"✅ {student_name} 2번 문항 완료")
        
        progress.progress((idx + 1) / len(images))
    
    status_text.text("처리 완료!")
    
    return students_data


# ============================================================
# UI
# ============================================================

def main():
    st.title("📝 박기호논술 OCR 시스템")
    st.caption("PDF 업로드 → 헤더 자동 인식 → OCR → Google Sheets 저장")
    
    # -------------------- 사이드바: 연결 상태 --------------------
    with st.sidebar:
        st.header("⚙️ 시스템 상태")
        
        spreadsheet, error = get_sheets_client()
        
        if spreadsheet:
            st.success(f"✅ Sheets 연결됨")
            st.caption(f"📊 {spreadsheet.title}")
        else:
            st.error(f"❌ Sheets 연결 실패")
            st.caption(error)
            spreadsheet = None
        
        # API 키 상태
        if os.getenv("GOOGLE_API_KEY"):
            st.success("✅ Gemini API 연결됨")
        else:
            st.error("❌ GOOGLE_API_KEY 필요")
        
        st.divider()
        
        # 사용 안내
        st.subheader("📖 사용 방법")
        st.markdown("""
        1. **PDF 업로드**: 스캔된 원고 PDF
        2. **자동 처리**: 헤더에서 학생/강/문항 인식
        3. **결과 확인**: OCR 텍스트 검토/수정
        4. **저장**: Google Sheets에 자동 저장
        """)
        
        # 크롭 영역 설정
        st.divider()
        st.subheader("✂️ 답안 영역 설정")
        
        if not st.session_state.crop_calibrated:
            st.warning("답안 영역이 설정되지 않았습니다. 샘플 PDF로 영역을 설정해주세요.")
        else:
            st.success("✅ 답안 영역 설정 완료")
        
        with st.expander("📐 크롭 영역 조정", expanded=not st.session_state.crop_calibrated):
            st.info("샘플 PDF를 업로드하고 슬라이더로 답안 영역을 조정하세요.")
            
            sample_pdf = st.file_uploader(
                "샘플 PDF (영역 설정용)",
                type=["pdf"],
                key="sample_pdf"
            )
            
            if sample_pdf:
                # PDF에서 이미지 추출
                sample_bytes = sample_pdf.read()
                sample_images = extract_images_from_pdf(sample_bytes)
                
                if sample_images and len(sample_images) >= 2:
                    st.success(f"✅ {len(sample_images)} 페이지 로드됨")
                    
                    tab1, tab2 = st.tabs(["📄 1번 문항 (홀수 페이지)", "📄 2번 문항 (짝수 페이지)"])
                    
                    with tab1:
                        st.markdown("**1번 문항 답안 영역 설정**")
                        
                        col1, col2 = st.columns([2, 1])
                        
                        with col2:
                            st.markdown("**크롭 비율 조정 (%)**")
                            left1 = st.slider("왼쪽", 0, 30, int(st.session_state.crop_coords_q1["left"]*100), key="left1")
                            top1 = st.slider("위쪽", 0, 30, int(st.session_state.crop_coords_q1["top"]*100), key="top1")
                            right1 = st.slider("오른쪽 (끝점)", 50, 100, int(st.session_state.crop_coords_q1["right"]*100), key="right1")
                            bottom1 = st.slider("아래쪽 (끝점)", 70, 100, int(st.session_state.crop_coords_q1["bottom"]*100), key="bottom1")
                            
                            # 좌표 업데이트
                            st.session_state.crop_coords_q1 = {
                                "left": left1 / 100,
                                "top": top1 / 100,
                                "right": right1 / 100,
                                "bottom": bottom1 / 100
                            }
                        
                        with col1:
                            # 크롭 미리보기
                            try:
                                cropped_preview1 = crop_answer_area(sample_images[0], is_page1=True)
                                st.image(cropped_preview1, caption="1번 문항 크롭 미리보기", use_container_width=True)
                            except Exception as e:
                                st.error(f"미리보기 실패: {e}")
                    
                    with tab2:
                        st.markdown("**2번 문항 답안 영역 설정**")
                        
                        col1, col2 = st.columns([2, 1])
                        
                        with col2:
                            st.markdown("**크롭 비율 조정 (%)**")
                            left2 = st.slider("왼쪽", 0, 30, int(st.session_state.crop_coords_q2["left"]*100), key="left2")
                            top2 = st.slider("위쪽", 0, 30, int(st.session_state.crop_coords_q2["top"]*100), key="top2")
                            right2 = st.slider("오른쪽 (끝점)", 50, 100, int(st.session_state.crop_coords_q2["right"]*100), key="right2")
                            bottom2 = st.slider("아래쪽 (끝점)", 70, 100, int(st.session_state.crop_coords_q2["bottom"]*100), key="bottom2")
                            
                            # 좌표 업데이트
                            st.session_state.crop_coords_q2 = {
                                "left": left2 / 100,
                                "top": top2 / 100,
                                "right": right2 / 100,
                                "bottom": bottom2 / 100
                            }
                        
                        with col1:
                            # 크롭 미리보기
                            try:
                                cropped_preview2 = crop_answer_area(sample_images[1], is_page1=False)
                                st.image(cropped_preview2, caption="2번 문항 크롭 미리보기", use_container_width=True)
                            except Exception as e:
                                st.error(f"미리보기 실패: {e}")
                    
                    if st.button("✅ 이 설정으로 저장", type="primary"):
                        st.session_state.crop_calibrated = True
                        st.success("크롭 영역이 저장되었습니다!")
                        st.rerun()
                else:
                    st.error("PDF에서 이미지를 추출할 수 없습니다.")
    
    # -------------------- 메인: 강 선택 + PDF 업로드 --------------------
    st.header("1️⃣ 강 선택 및 PDF 업로드")
    
    col1, col2 = st.columns([1, 3])
    
    with col1:
        selected_lesson = st.selectbox(
            "📚 강 선택",
            options=list(range(1, 13)),
            index=1,  # 기본값 2강
            help="업로드할 PDF의 강 번호를 선택하세요"
        )
        st.session_state.selected_lesson = selected_lesson
    
    with col2:
        uploaded_file = st.file_uploader(
            "학생 원고 PDF를 업로드하세요",
            type=["pdf"],
            help="여러 학생의 원고가 포함된 스캔 PDF"
        )
    
    if uploaded_file and spreadsheet:
        st.success(f"📄 {uploaded_file.name} ({uploaded_file.size / 1024:.1f} KB)")
        st.info(f"📚 선택된 강: **{selected_lesson}강**")
        
        if st.button("🚀 OCR 처리 시작", use_container_width=True, type="primary"):
            with st.spinner("PDF 처리 중..."):
                pdf_bytes = uploaded_file.read()
                students_data = process_pdf(pdf_bytes, spreadsheet, selected_lesson)
                
                if students_data:
                    st.session_state.students_data = students_data
                    st.session_state.processing_complete = True
                    st.success(f"✅ {len(students_data)}개 문항 처리 완료")
                    st.rerun()
                else:
                    st.error("처리된 데이터가 없습니다.")
    
    # -------------------- 메인: 결과 확인/수정 --------------------
    if st.session_state.students_data:
        st.divider()
        st.header("2️⃣ OCR 결과 확인")
        
        students_data = st.session_state.students_data
        
        for idx, student in enumerate(students_data):
            st.subheader(f"{'✅' if student['status'] == 'matched' else '⚠️'} "
                        f"{student['name'] or '이름 미인식'} | "
                        f"{student['lesson']}강 문항{student['question_num']}")
            
            # 3열 배치: 원본 이미지 / 네이버 OCR / 복원 결과
            col1, col2, col3 = st.columns(3)
            
            with col1:
                st.markdown("**📷 원본 이미지**")
                if student.get('images'):
                    for img in student['images'][:1]:
                        st.image(img, use_container_width=True)
                else:
                    st.info("이미지 없음")
            
            with col2:
                st.markdown("**🔤 네이버 OCR 원본**")
                ocr_key = f"{student['name']}_{student['lesson']}_{student['question_num']}"
                if "ocr_debug" in st.session_state and ocr_key in st.session_state.ocr_debug:
                    debug_data = st.session_state.ocr_debug[ocr_key]
                    st.text_area(
                        f"OCR 원본 ({debug_data['raw_len']}자)",
                        debug_data['raw_text'],
                        height=300,
                        disabled=True,
                        key=f"debug_raw_{idx}"
                    )
                else:
                    st.info("OCR 데이터 없음")
            
            with col3:
                st.markdown("**✨ Gemini 복원 결과**")
                if "ocr_debug" in st.session_state and ocr_key in st.session_state.ocr_debug:
                    debug_data = st.session_state.ocr_debug[ocr_key]
                    st.text_area(
                        f"복원 ({debug_data['restored_len']}자)",
                        debug_data['restored_text'],
                        height=300,
                        disabled=True,
                        key=f"debug_restored_{idx}"
                    )
                    
                    # 글자 수 차이 경고
                    diff = debug_data['restored_len'] - debug_data['raw_len']
                    if abs(diff) > 50:
                        st.warning(f"⚠️ 차이: {diff:+d}자")
                else:
                    st.info("복원 데이터 없음")
            
            # 메타 정보 + 편집 영역
            st.divider()
            col1, col2 = st.columns([1, 2])
            
            with col1:
                # 메타 정보
                st.markdown("**📋 인식 정보**")
                
                # 수정 가능한 필드
                new_name = st.text_input(
                    "학생명", 
                    value=student['name'],
                    key=f"name_{idx}"
                )
                
                col_a, col_b = st.columns(2)
                with col_a:
                    new_lesson = st.number_input(
                        "강", 
                        value=student['lesson'],
                        min_value=1, max_value=12,
                        key=f"lesson_{idx}"
                    )
                with col_b:
                    new_question = st.number_input(
                        "문항",
                        value=student['question_num'],
                        min_value=1, max_value=3,
                        key=f"question_{idx}"
                    )
                
                # 변경사항 반영
                if new_name != student['name']:
                    st.session_state.students_data[idx]['name'] = new_name
                    new_row = find_student_row(spreadsheet, new_lesson, new_name) if spreadsheet else None
                    st.session_state.students_data[idx]['row'] = new_row
                    st.session_state.students_data[idx]['status'] = 'matched' if new_row else 'unmatched'
                
                if new_lesson != student['lesson']:
                    st.session_state.students_data[idx]['lesson'] = new_lesson
                
                if new_question != student['question_num']:
                    st.session_state.students_data[idx]['question_num'] = new_question
                
                # 매칭 상태
                if student['row']:
                    st.success(f"✅ 시트 매칭: {student['lesson']}강 {student['row']}행")
                else:
                    st.warning("⚠️ 시트에서 학생을 찾을 수 없음")
                    
                    # 수동 선택
                    if spreadsheet:
                        students_list = get_students_list(spreadsheet, new_lesson)
                        if students_list:
                            options = ["(선택하세요)"] + [f"{s['name']} ({s['row']}행)" for s in students_list]
                            selected = st.selectbox(
                                "학생 수동 선택",
                                options=options,
                                key=f"manual_{idx}"
                            )
                            if selected != "(선택하세요)":
                                selected_idx = options.index(selected) - 1
                                st.session_state.students_data[idx]['row'] = students_list[selected_idx]['row']
                                st.session_state.students_data[idx]['status'] = 'matched'
                                st.rerun()
                
                # 확신도 표시
                conf = student.get('confidence', 0)
                if conf >= 0.9:
                    st.success(f"확신도: {conf:.0%}")
                elif conf >= 0.8:
                    st.warning(f"확신도: {conf:.0%}")
                else:
                    st.error(f"확신도: {conf:.0%} (검토 필요)")
                
                # 이미지 미리보기
                if student.get('images'):
                    with st.expander("📷 원본 이미지 보기"):
                        for img in student['images'][:2]:
                            st.image(img, use_container_width=True)
            
            with col2:
                st.markdown("**📝 최종 텍스트 (수정 가능)**")
                
                # 텍스트 편집 (큰 영역)
                edited_text = st.text_area(
                    "텍스트 (수정 가능)",
                    value=student.get('text', ''),
                    height=300,
                    key=f"text_{idx}",
                    help="OCR 결과를 확인하고 필요시 수정하세요"
                )
                
                if edited_text != student.get('text', ''):
                    st.session_state.students_data[idx]['text'] = edited_text
                
                # 글자 수 표시
                char_count = len(edited_text.replace(" ", "").replace("\n", ""))
                st.caption(f"📊 글자 수: {char_count}자 (공백 제외)")
                
                # 복사 버튼
                st.code(edited_text, language=None)
            
            st.divider()
        
        # -------------------- 메인: 저장 --------------------
        st.divider()
        st.header("3️⃣ Google Sheets 저장")
        
        # 저장 가능한 학생 수
        matched_count = len([s for s in students_data if s['row']])
        total_count = len(students_data)
        
        if matched_count < total_count:
            st.warning(f"⚠️ {total_count - matched_count}명 학생이 시트에 매칭되지 않았습니다.")
        
        col1, col2 = st.columns(2)
        
        with col1:
            if st.button(
                f"💾 매칭된 {matched_count}명 저장", 
                use_container_width=True, 
                type="primary",
                disabled=(matched_count == 0)
            ):
                saved_count = 0
                
                for student in students_data:
                    if student['row'] and student.get('text'):
                        success = save_ocr_to_sheet(
                            spreadsheet,
                            student['lesson'],
                            student['row'],
                            student['question_num'],
                            student['text']
                        )
                        if success:
                            saved_count += 1
                
                if saved_count > 0:
                    st.success(f"✅ {saved_count}명 저장 완료!")
                    st.balloons()
                else:
                    st.error("저장 실패")
        
        with col2:
            if st.button("🔄 초기화", use_container_width=True):
                st.session_state.students_data = []
                st.session_state.processing_complete = False
                st.rerun()
    
    # -------------------- 푸터 --------------------
    st.divider()
    st.caption("논술연구소 OCR 시스템 v1.0 | 헤더 자동 인식 + Google Sheets 연동")


if __name__ == "__main__":
    main()
