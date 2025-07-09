# app.py

import streamlit as st
import re
import pandas as pd
from typing import List, Dict, Tuple
import docx
from docx.document import Document
from docx.table import Table
from docx.text.paragraph import Paragraph
import io

# --- 로직 클래스: 텍스트 추출에 모든 역량을 집중 ---
class AdvancedDocxExtractor:
    """
    LLM 입력을 위한 가장 정확한 텍스트 추출에 집중하는 클래스.
    테이블, 복합 문단, 다양한 블릿, 들여쓰기를 모두 분석하여 계층 구조를 추출합니다.
    """
    def __init__(self, business_code: str = "MFDS"):
        self.business_code = business_code

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
                        # 테이블 셀 내의 문단도 모두 추가
                        for p in cell.paragraphs:
                            if p.text.strip(): # 내용이 있는 문단만 추가
                                all_paragraphs.append(p)
        return all_paragraphs

    def _get_indentation_level(self, p: Paragraph) -> float:
        """문단의 들여쓰기 수준을 반환합니다. (None일 경우 0으로 처리)"""
        indent = p.paragraph_format.left_indent
        return indent.pt if indent else 0.0

    # [핵심 로직] 계층 구조를 분석하여 DataFrame용 딕셔너리 리스트 생성
    def _parse_hierarchical_text(self, paragraphs: List[Paragraph], req_id: str, req_name: str) -> List[Dict]:
        extracted_items = []
        item_counter = 1
        # (들여쓰기 수준, 텍스트)를 저장하는 스택
        parent_stack: List[Tuple[float, str]] = []

        for p in paragraphs:
            # 1. 문단을 줄 단위로 분해
            lines = [line for line in p.text.split('\n') if line.strip()]
            if not lines:
                continue

            # 문단 자체의 들여쓰기 수준을 모든 줄이 상속
            base_indent = self._get_indentation_level(p)

            for line in lines:
                # 2. 각 줄의 블릿과 텍스트 분리
                # 다양한 블릿 문자(숫자 포함)와 공백을 처리하는 정규식
                match = re.match(r'^\s*([*◦○•·▴-]|[가-힣]\.|[0-9]+\.)\s*(.*)', line)
                if match:
                    bullet = match.group(1)
                    content = match.group(2).strip()
                    # 블릿이 있는 경우, 들여쓰기 수준을 약간 높여 계층을 명확히 함
                    current_indent = base_indent + 5 
                else:
                    bullet = ""
                    content = line.strip()
                    current_indent = base_indent

                if not content:
                    continue

                # 3. 부모 항목 결정
                # 스택을 확인하여 현재 항목보다 들여쓰기가 깊거나 같은 부모를 모두 제거
                while parent_stack and current_indent <= parent_stack[-1][0]:
                    parent_stack.pop()
                
                parent_text = parent_stack[-1][1] if parent_stack else req_name
                
                # 4. 결과 리스트에 추가
                extracted_items.append({
                    '상위 요구사항 ID': req_id,
                    '상위 요구사항 명칭': req_name,
                    'ID': f"{req_id}-{item_counter:03d}",
                    '레벨': len(parent_stack) + 1,
                    '구분(블릿)': bullet,
                    '내용': content,
                    '상위 항목': parent_text,
                })
                item_counter += 1

                # 현재 항목을 다음 항목의 잠재적 부모로 스택에 추가
                parent_stack.append((current_indent, content))

        return extracted_items


    def process(self, docx_file: io.BytesIO) -> pd.DataFrame:
        doc = docx.Document(docx_file)
        all_paragraphs = self._get_all_paragraphs_in_order(doc)
        
        all_requirements = []
        
        # '요구사항 명칭' 또는 '요구사항 고유번호'를 기준으로 블록을 식별
        block_starts = [i for i, p in enumerate(all_paragraphs) if '요구사항 명칭' in p.text or '요구사항 고유번호' in p.text]
        if not block_starts:
            st.warning("문서에서 '요구사항 명칭' 또는 '요구사항 고유번호' 키워드를 찾을 수 없습니다.")
            return pd.DataFrame()
            
        st.info(f"총 {len(block_starts)}개의 요구사항 블록 시작점을 식별했습니다.")

        for i, start_idx in enumerate(block_starts):
            end_idx = block_starts[i+1] if i + 1 < len(block_starts) else len(all_paragraphs)
            
            block_paragraphs = all_paragraphs[start_idx:end_idx]
            block_text = "\n".join([p.text for p in block_paragraphs])
            
            # ID와 명칭 추출 (더 유연하게)
            req_id_match = re.search(r'요구사항\s*고유번호\s*[:]?\s*([A-Z0-9-]{3,})', block_text, re.IGNORECASE)
            req_name_match = re.search(r'요구사항\s*명칭\s*[:]?\s*(.+?)(?:\n|$)', block_text)
            
            req_id = req_id_match.group(1).strip() if req_id_match else f"REQ-TEMP-{i+1:03d}"
            req_name = req_name_match.group(1).strip() if req_name_match else "명칭 미상"

            # '세부내용' 키워드를 찾아 그 이후의 문단만 파싱 대상으로 함
            details_start_offset = -1
            for j, p in enumerate(block_paragraphs):
                if '세부내용' in p.text:
                    details_start_offset = j + 1
                    break
            
            if details_start_offset != -1:
                details_paragraphs = block_paragraphs[details_start_offset:]
                parsed_items = self._parse_hierarchical_text(details_paragraphs, req_id, req_name)
                all_requirements.extend(parsed_items)

        if not all_requirements:
            st.warning("요구사항 블록은 찾았으나, 세부 내용을 추출하지 못했습니다. 문서 구조를 확인해주세요.")
            return pd.DataFrame()

        df = pd.DataFrame(all_requirements)
        
        # LLM이 이해하기 좋은 순서로 컬럼 정리
        column_order = [
            'ID', '레벨', '내용', '상위 항목', '구분(블릿)', 
            '상위 요구사항 ID', '상위 요구사항 명칭'
        ]
        return df.reindex(columns=[col for col in column_order if col in df.columns])

# --- Streamlit UI 구성 ---
def main():
    st.set_page_config(page_title="LLM용 요구사항 텍스트 추출기", layout="wide")
    st.title("📑 DOCX 요구사항 텍스트 추출기 (For LLM)")
    st.markdown("LLM 입력을 위해, **테이블, 들여쓰기, 블릿**을 종합 분석하여 문서의 계층 구조를 포함한 텍스트를 정확하게 추출합니다.")

    with st.sidebar:
        st.header("⚙️ 분석 설정")
        business_code = st.text_input("사업 코드 (ID 생성용)", value="MFDS")
        st.info("이 도구는 블릿 문자 설정 없이, 문서의 구조 자체를 분석하여 자동으로 계층을 인식합니다.")

    uploaded_file = st.file_uploader("분석할 .docx 파일을 업로드하세요.", type=["docx"])

    if uploaded_file is not None:
        try:
            extractor = AdvancedDocxExtractor(business_code=business_code)
            
            with st.spinner("문서 구조 분석 및 텍스트 추출 중..."):
                file_bytes = io.BytesIO(uploaded_file.getvalue())
                extracted_df = extractor.process(file_bytes)

            if not extracted_df.empty:
                st.success(f"✅ 총 {len(extracted_df)}개의 요구사항 항목을 성공적으로 추출했습니다.")
                st.info("아래는 LLM이 처리하기 좋도록 계층 구조(레벨, 상위 항목)를 포함한 결과입니다.")
                st.dataframe(extracted_df)

                @st.cache_data
                def convert_df_to_csv(_df):
                    return _df.to_csv(index=False, encoding='utf-8-sig').encode('utf-8-sig')

                csv_data = convert_df_to_csv(extracted_df)
                
                st.download_button(
                    label="📥 추출 결과를 CSV 파일로 다운로드",
                    data=csv_data,
                    file_name=f"extracted_requirements_for_llm_{business_code}.csv",
                    mime="text/csv",
                )
            else:
                # extractor.process 내부에서 st.warning이 호출됨
                pass

        except Exception as e:
            st.error(f"❌ 분석 중 오류가 발생했습니다: {e}")
            st.exception(e)

if __name__ == '__main__':
    main()
