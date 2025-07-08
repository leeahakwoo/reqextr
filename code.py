# app.py

import streamlit as st
import re
import pandas as pd
from typing import List, Dict
import docx
from docx.document import Document
from docx.table import Table
import io

# --- 로직 클래스 ---
class StreamlitDocxExtractor:
    """
    [개선됨] 문단의 스타일을 분석하여 자동 글머리 기호까지 인식하는 클래스.
    'List Paragraph' 스타일을 감지하여 안정성을 대폭 향상시켰습니다.
    """
    def __init__(self, business_code: str = "MFDS"):
        self.business_code = business_code

    def _generate_id(self, req_id: str, sequence: int) -> str:
        """요구사항 ID를 생성합니다."""
        req_type_code = "F" if req_id.startswith("FUN") else "Q"
        id_num_match = re.search(r'\d+', req_id)
        id_num = id_num_match.group(0) if id_num_match else "000"
        return f"REQ-{self.business_code}-{req_type_code}-{id_num}{sequence:03d}"

    def _is_bullet_paragraph(self, p: docx.text.paragraph.Paragraph, bullet_chars: str) -> bool:
        """
        문단이 주어진 블릿 문자로 시작하거나, 자동 글머리 기호 스타일인지 확인합니다.
        """
        line_text = p.text.strip()
        # 1. 'List Paragraph' 스타일인지 확인 (가장 확실한 방법)
        if 'List' in p.style.name:
            return True
        # 2. 텍스트가 직접 입력된 블릿 문자로 시작하는지 확인
        if line_text and re.match(f'^[{re.escape(bullet_chars)}]', line_text):
            return True
        return False
    
    def _parse_details_from_paragraphs(self, doc: Document, start_idx: int, end_idx: int, req_id: str, level1_bullets: str, level2_bullets: str) -> List[Dict]:
        """
        [핵심 로직] 문단 리스트를 순회하며 계층적 요구사항을 추출합니다.
        """
        final_requirements = []
        bfn_seq_counter = 1
        current_group_title = ""
        
        # '세부내용' 바로 다음 문단부터 블록 끝까지 순회
        for i in range(start_idx, end_idx):
            p = doc.paragraphs[i]
            line = p.text.strip()
            if not line:
                continue

            # 1차 블릿인지 확인 (스타일 또는 텍스트 기반)
            if self._is_bullet_paragraph(p, level1_bullets):
                current_group_title = re.sub(f'^[{re.escape(level1_bullets)}]+\s*', '', line)
                
                # 다음 문단이 2차 블릿인지 미리 확인
                is_next_l2 = False
                if i + 1 < end_idx:
                    next_p = doc.paragraphs[i+1]
                    if self._is_bullet_paragraph(next_p, level2_bullets):
                        is_next_l2 = True
                
                # 2차 블릿이 없는 단독 1차 블릿이면, 그 자체가 요구사항
                if not is_next_l2:
                    final_requirements.append({
                        '요구사항 그룹': current_group_title, '세부 요구사항 내용': current_group_title,
                        '세부 요구사항 ID': self._generate_id(req_id, bfn_seq_counter)
                    })
                    bfn_seq_counter += 1
                    
            # 2차 블릿인지 확인하고, 현재 그룹에 속해있는지 확인
            elif current_group_title and self._is_bullet_paragraph(p, level2_bullets):
                point_text = re.sub(f'^\s*[{re.escape(level2_bullets)}]+\s*', '', line)
                final_requirements.append({
                    '요구사항 그룹': current_group_title, '세부 요구사항 내용': point_text,
                    '세부 요구사항 ID': self._generate_id(req_id, bfn_seq_counter)
                })
                bfn_seq_counter += 1
        
        return final_requirements

    def process(self, docx_file: io.BytesIO, level1_bullets: str, level2_bullets: str) -> pd.DataFrame:
        """
        [수정됨] Paragraph 객체 기반의 새로운 파싱 로직을 사용하도록 수정
        """
        doc = docx.Document(docx_file)
        
        # 텍스트 기반으로 '요구사항 블록'의 시작과 끝 위치(문단 인덱스)를 먼저 찾음
        block_markers = []
        for i, p in enumerate(doc.paragraphs):
            # '요구사항 분류' 키워드가 포함된 문단을 블록의 시작으로 간주
            if '요구사항 분류' in p.text:
                block_markers.append(i)
        
        st.info(f"총 {len(block_markers)}개의 요구사항 블록 시작점을 식별했습니다.")
        if not block_markers:
            st.warning("문서에서 '요구사항 분류' 키워드를 찾을 수 없습니다. 문서 구조를 확인해주세요.")
            return pd.DataFrame()

        all_requirements = []
        # 각 요구사항 블록별로 처리
        for i, start_index in enumerate(block_markers):
            # 현재 블록의 끝 인덱스 계산 (다음 블록 시작 전까지)
            end_index = block_markers[i+1] if i + 1 < len(block_markers) else len(doc.paragraphs)
            
            # 현재 블록에 포함된 문단들의 텍스트를 하나로 합침
            block_text = "\n".join([p.text for p in doc.paragraphs[start_index:end_index]])
            
            # 블록 내에서 ID와 명칭 추출
            req_id_match = re.search(r'요구사항 고유번호\s+([A-Z]{3}-\d{3})', block_text)
            req_name_match = re.search(r'요구사항 명칭\s+(.+?)(?:\n|$)', block_text)

            if not req_id_match or not req_name_match: continue

            req_id, req_name = req_id_match.group(1).strip(), req_name_match.group(1).strip()
            
            # '세부내용' 키워드가 있는 문단 인덱스를 찾아 파싱 시작점으로 설정
            details_start_index = -1
            for j in range(start_index, end_index):
                if '세부내용' in doc.paragraphs[j].text:
                    details_start_index = j + 1 # 세부내용 바로 다음 문단부터
                    break
            
            if details_start_index != -1:
                # 스타일 기반 파서 호출
                parsed_reqs = self._parse_details_from_paragraphs(doc, details_start_index, end_index, req_id, level1_bullets, level2_bullets)
                for req in parsed_reqs:
                    req['요구사항 ID (RFP 원천)'] = req_id
                    req['요구사항 명칭 (RFP 원천)'] = req_name
                all_requirements.extend(parsed_reqs)

        if not all_requirements:
            st.warning("요구사항 블록은 찾았으나, 세부 내용을 파싱하지 못했습니다. 문서 구조나 블릿 설정을 확인해주세요.")
            return pd.DataFrame()

        df = pd.DataFrame(all_requirements)
        df['출처'] = 'DOCX 문서'
        df['유형'] = df['요구사항 ID (RFP 원천)'].apply(lambda x: '기능' if x.startswith('FUN') else '비기능')
        
        column_order = [
            '세부 요구사항 ID', '요구사항 그룹', '세부 요구사항 내용', 
            '요구사항 ID (RFP 원천)', '요구사항 명칭 (RFP 원천)', '유형', '출처'
        ]
        return df.reindex(columns=column_order)

# --- Streamlit UI 구성 ---
def main():
    st.set_page_config(page_title="요구사항 추출기", layout="wide", initial_sidebar_state="expanded")
    st.title("📄 DOCX 요구사항 정의서 자동 추출기")
    st.markdown("MS Word의 자동 글머리 기호(`-`, `*` 등)를 포함한 요구사항 정의서를 분석하여 세부 목록을 생성합니다.")

    with st.sidebar:
        st.header("⚙️ 분석 설정")
        
        business_code = st.text_input("사업 코드", value="MFDS", help="요구사항 ID 생성에 사용됩니다. (예: REQ-**MFDS**-F-001001)")
        
        st.markdown("---")
        
        st.subheader("블릿(Bullet) 체계 설정")
        level1_bullets = st.text_input("1차 블릿 문자 (그룹)", value="◦○•", help="요구사항 그룹을 나타내는 글머리 기호를 모두 입력하세요.")
        level2_bullets = st.text_input("2차 블릿 문자 (세부 항목)", value="-*·▴", help="세부 요구사항을 나타내는 글머리 기호를 모두 입력하세요.")
        st.info("문서에 사용된 블릿을 정확히 입력해야 분석 성공률이 높아집니다.")

    uploaded_file = st.file_uploader("분석할 .docx 파일을 업로드하세요.", type=["docx"])

    if uploaded_file is not None:
        try:
            extractor = StreamlitDocxExtractor(business_code=business_code)
            
            with st.spinner("파일 분석 및 요구사항 추출 중..."):
                requirements_df = extractor.process(uploaded_file, level1_bullets, level2_bullets)

            if not requirements_df.empty:
                st.success(f"✅ 총 {len(requirements_df)}개의 세부 요구사항을 성공적으로 추출했습니다.")
                st.dataframe(requirements_df)

                @st.cache_data
                def convert_df_to_csv(_df):
                    return _df.to_csv(index=False, encoding='utf-8-sig').encode('utf-8-sig')

                csv_data = convert_df_to_csv(requirements_df)
                
                st.download_button(
                    label="📥 추출 결과를 CSV 파일로 다운로드",
                    data=csv_data,
                    file_name=f"extracted_requirements_{business_code}.csv",
                    mime="text/csv",
                )
            else:
                pass # 경고 메시지는 process() 함수 내부에서 이미 처리됨

        except Exception as e:
            st.error(f"❌ 분석 중 오류가 발생했습니다: {e}")
            st.exception(e) # 개발자 확인용 상세 에러 로그

if __name__ == '__main__':
    main()
