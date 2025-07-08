# app.py

import streamlit as st
import re
import pandas as pd
from typing import List, Dict
import docx
from docx.document import Document
from docx.table import Table
from docx.text.paragraph import Paragraph
import io

# --- 로직 클래스 ---
class StreamlitDocxExtractor:
    """
    [최종 완성본] 들여쓰기 수준(Indentation)을 분석하여 가장 정확한 계층 구조를 파악하는 클래스.
    """
    def __init__(self, business_code: str = "MFDS"):
        self.business_code = business_code

    def _generate_id(self, req_id: str, sequence: int) -> str:
        """요구사항 ID를 생성합니다."""
        req_type_code = "F" if req_id.startswith("FUN") else "Q"
        id_num_match = re.search(r'\d+', req_id)
        id_num = id_num_match.group(0) if id_num_match else "000"
        return f"REQ-{self.business_code}-{req_type_code}-{id_num}{sequence:03d}"

    def _get_all_paragraphs_in_order(self, doc: Document) -> List[Paragraph]:
        """문서의 모든 문단 객체를 표 안의 내용까지 포함하여 순서대로 가져옵니다."""
        all_paragraphs = []
        for block in doc.element.body:
            if isinstance(block, docx.oxml.text.paragraph.CT_P):
                all_paragraphs.append(Paragraph(block, doc))
            elif isinstance(block, docx.oxml.table.CT_Tbl):
                table = Table(block, doc)
                for row in table.rows:
                    for cell in row.cells:
                        all_paragraphs.extend(cell.paragraphs)
        return all_paragraphs

    def _get_indentation_level(self, p: Paragraph) -> int:
        """문단의 들여쓰기 수준을 반환합니다. (None일 경우 0으로 처리)"""
        return p.paragraph_format.left_indent.pt if p.paragraph_format.left_indent else 0

    def _parse_details_from_paragraphs(self, paragraphs: List[Paragraph], req_id: str) -> List[Dict]:
        """
        [최종 로직] 들여쓰기 수준을 기반으로 계층 구조를 파악하여 요구사항을 추출합니다.
        """
        final_requirements = []
        bfn_seq_counter = 1
        group_stack = []  # 그룹 제목을 저장하는 스택

        for p in paragraphs:
            line = p.text.strip()
            if not line:
                continue

            # 블릿 스타일이 적용된 문단만 처리
            if not (p.style and 'List' in p.style.name):
                continue
            
            level = self._get_indentation_level(p)
            
            # 스택의 마지막 레벨보다 현재 레벨이 더 깊으면, 스택은 유지
            # 스택의 마지막 레벨과 현재 레벨이 같거나 더 얕으면, 상위 그룹으로 돌아감
            while group_stack and level <= group_stack[-1]['level']:
                group_stack.pop()

            # 현재 문단이 그룹 제목이 될 수 있음
            group_stack.append({'title': line, 'level': level})

            # 현재 스택의 최상위 그룹(1레벨)
            top_group = group_stack[0]['title'] if group_stack else ""
            
            # 현재 문단이 세부 요구사항 (스택에 2개 이상 쌓였거나, 1개만 있어도 그 자체가 요구사항)
            if group_stack:
                 final_requirements.append({
                    '요구사항 그룹': top_group,
                    '세부 요구사항 내용': line,
                    '세부 요구사항 ID': self._generate_id(req_id, bfn_seq_counter)
                })
                 bfn_seq_counter += 1

        return final_requirements


    def process(self, docx_file: io.BytesIO, level1_bullets: str, level2_bullets: str) -> pd.DataFrame:
        doc = docx.Document(docx_file)
        all_paragraphs = self._get_all_paragraphs_in_order(doc)
        
        block_markers = []
        for i, p in enumerate(all_paragraphs):
            if '요구사항 분류' in p.text:
                block_markers.append(i)
        
        st.info(f"총 {len(block_markers)}개의 요구사항 블록 시작점을 식별했습니다.")
        if not block_markers:
            st.warning("문서에서 '요구사항 분류' 키워드를 찾을 수 없습니다.")
            return pd.DataFrame()

        all_requirements = []
        for i, start_index in enumerate(block_markers):
            end_index = block_markers[i+1] if i + 1 < len(block_markers) else len(all_paragraphs)
            
            block_paragraphs = all_paragraphs[start_index:end_index]
            block_text = "\n".join([p.text for p in block_paragraphs])
            
            # SyntaxWarning 해결: Raw string 사용
            req_id_match = re.search(r'요구사항 고유번호\s+([A-Z]{3}-\d{3})', block_text)
            req_name_match = re.search(r'요구사항 명칭\s+(.+?)(?:\n|$)', block_text)

            if not req_id_match or not req_name_match: continue

            req_id, req_name = req_id_match.group(1).strip(), req_name_match.group(1).strip()
            
            details_start_index_offset = -1
            for j, p in enumerate(block_paragraphs):
                if '세부내용' in p.text:
                    details_start_index_offset = j + 1
                    break
            
            if details_start_index_offset != -1:
                details_paragraphs = block_paragraphs[details_start_index_offset:]
                # 블릿 문자 설정은 더 이상 필요 없으므로 전달하지 않음
                parsed_reqs = self._parse_details_from_paragraphs(details_paragraphs, req_id)
                
                for req in parsed_reqs:
                    req['요구사항 ID (RFP 원천)'] = req_id
                    req['요구사항 명칭 (RFP 원천)'] = req_name
                all_requirements.extend(parsed_reqs)

        if not all_requirements:
            st.warning("요구사항 블록은 찾았으나, 세부 내용을 파싱하지 못했습니다. 문서의 '세부내용'에 글머리 기호 목록이 있는지 확인해주세요.")
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
    st.markdown("MS Word의 **들여쓰기(Indentation)**를 분석하여, 글머리 기호 및 표(Table)에 포함된 요구사항 목록을 정확하게 생성합니다.")

    with st.sidebar:
        st.header("⚙️ 분석 설정")
        business_code = st.text_input("사업 코드", value="MFDS", help="요구사항 ID 생성에 사용됩니다.")
        st.info("이제 블릿 문자를 직접 입력할 필요 없이, 문서의 **글머리 기호 스타일**과 **들여쓰기**를 기반으로 자동으로 계층을 분석합니다.")

    uploaded_file = st.file_uploader("분석할 .docx 파일을 업로드하세요.", type=["docx"])

    if uploaded_file is not None:
        try:
            # 블릿 설정은 더 이상 필요 없으므로 빈 문자열 전달
            level1_bullets = ""
            level2_bullets = ""
            
            extractor = StreamlitDocxExtractor(business_code=business_code)
            
            with st.spinner("파일 분석 및 요구사항 추출 중... (들여쓰기 분석 중)"):
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
                pass

        except Exception as e:
            st.error(f"❌ 분석 중 오류가 발생했습니다: {e}")
            st.exception(e)

if __name__ == '__main__':
    main()
