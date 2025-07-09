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
import google.generativeai as genai

# --- 1단계 로직: 텍스트 추출기 (이전과 동일) ---
class AdvancedDocxExtractor:
    def __init__(self, business_code: str = "MFDS"):
        self.business_code = business_code

    def _get_all_paragraphs_in_order(self, doc: Document) -> List[Paragraph]:
        all_paragraphs = []
        for block in doc.element.body:
            if isinstance(block, docx.oxml.text.paragraph.CT_P):
                all_paragraphs.append(Paragraph(block, doc))
            elif isinstance(block, docx.oxml.table.CT_Tbl):
                table = Table(block, doc)
                for row in table.rows:
                    for cell in row.cells:
                        for p in cell.paragraphs:
                            if p.text.strip():
                                all_paragraphs.append(p)
        return all_paragraphs

    def _get_indentation_level(self, p: Paragraph) -> float:
        indent = p.paragraph_format.left_indent
        return indent.pt if indent else 0.0

    def _parse_hierarchical_text(self, paragraphs: List[Paragraph], req_id: str, req_name: str) -> List[Dict]:
        extracted_items = []
        item_counter = 1
        parent_stack: List[Tuple[float, str]] = []

        for p in paragraphs:
            lines = [line for line in p.text.split('\n') if line.strip()]
            if not lines: continue
            base_indent = self._get_indentation_level(p)

            for line in lines:
                match = re.match(r'^\s*([*◦○•·▴-]|[가-힣]\.|[0-9]+\.)\s*(.*)', line)
                if match:
                    bullet, content = match.group(1), match.group(2).strip()
                    current_indent = base_indent + 5
                else:
                    bullet, content = "", line.strip()
                    current_indent = base_indent

                if not content: continue

                while parent_stack and current_indent <= parent_stack[-1][0]:
                    parent_stack.pop()
                
                parent_text = parent_stack[-1][1] if parent_stack else req_name
                
                extracted_items.append({
                    '상위 요구사항 ID': req_id, '상위 요구사항 명칭': req_name,
                    'ID': f"{req_id}-{item_counter:03d}", '레벨': len(parent_stack) + 1,
                    '구분(블릿)': bullet, '내용': content, '상위 항목': parent_text,
                })
                item_counter += 1
                parent_stack.append((current_indent, content))
        return extracted_items

    def process(self, docx_file: io.BytesIO) -> pd.DataFrame:
        doc = docx.Document(docx_file)
        all_paragraphs = self._get_all_paragraphs_in_order(doc)
        all_requirements = []
        
        block_starts = [i for i, p in enumerate(all_paragraphs) if '요구사항 명칭' in p.text or '요구사항 고유번호' in p.text]
        if not block_starts:
            st.warning("문서에서 '요구사항 명칭' 또는 '요구사항 고유번호' 키워드를 찾을 수 없습니다.")
            return pd.DataFrame()
            
        st.info(f"총 {len(block_starts)}개의 요구사항 블록 시작점을 식별했습니다.")

        for i, start_idx in enumerate(block_starts):
            end_idx = block_starts[i+1] if i + 1 < len(block_starts) else len(all_paragraphs)
            block_paragraphs = all_paragraphs[start_idx:end_idx]
            block_text = "\n".join([p.text for p in block_paragraphs])
            
            req_id_match = re.search(r'요구사항\s*고유번호\s*[:]?\s*([A-Z0-9-]{3,})', block_text, re.IGNORECASE)
            req_name_match = re.search(r'요구사항\s*명칭\s*[:]?\s*(.+?)(?:\n|$)', block_text)
            
            req_id = req_id_match.group(1).strip() if req_id_match else f"REQ-TEMP-{i+1:03d}"
            req_name = req_name_match.group(1).strip() if req_name_match else "명칭 미상"

            details_start_offset = next((j + 1 for j, p in enumerate(block_paragraphs) if '세부내용' in p.text), -1)
            
            if details_start_offset != -1:
                details_paragraphs = block_paragraphs[details_start_offset:]
                parsed_items = self._parse_hierarchical_text(details_paragraphs, req_id, req_name)
                all_requirements.extend(parsed_items)

        if not all_requirements:
            st.warning("요구사항 블록은 찾았으나, 세부 내용을 추출하지 못했습니다. 문서 구조를 확인해주세요.")
            return pd.DataFrame()

        df = pd.DataFrame(all_requirements)
        column_order = ['ID', '레벨', '내용', '상위 항목', '구분(블릿)', '상위 요구사항 ID', '상위 요구사항 명칭']
        return df.reindex(columns=[col for col in column_order if col in df.columns])

# --- 2단계 로직: Gemini API 연동 클래스 ---
class GeminiProcessor:
    def __init__(self, api_key: str):
        self.api_key = api_key
        genai.configure(api_key=self.api_key)
        self.model = genai.GenerativeModel('gemini-1.5-pro-latest')

    def _format_dataframe_for_llm(self, df: pd.DataFrame) -> str:
        """DataFrame을 LLM이 이해하기 좋은 계층적 Markdown 텍스트로 변환"""
        markdown_lines = []
        for _, row in df.iterrows():
            # 레벨에 따라 들여쓰기 적용
            indent = "  " * (row['레벨'] - 1)
            markdown_lines.append(f"{indent}- {row['내용']}")
        return "\n".join(markdown_lines)

    def reconstruct_requirements(self, df: pd.DataFrame, custom_prompt: str) -> str:
        """추출된 데이터를 바탕으로 제미나이를 호출하여 요구사항을 재구성"""
        formatted_text = self._format_dataframe_for_llm(df)
        
        final_prompt = f"""{custom_prompt}

### 원본 추출 데이터:
{formatted_text}
"""
        try:
            response = self.model.generate_content(final_prompt)
            return response.text
        except Exception as e:
            return f"Gemini API 호출 중 오류가 발생했습니다: {e}"


# --- Streamlit UI 구성 ---
def main():
    st.set_page_config(page_title="AI 요구사항 분석기", layout="wide")
    st.title("📑 AI 기반 DOCX 요구사항 분석 및 재구성")
    st.markdown("**1단계**: DOCX 문서에서 요구사항 텍스트를 계층적으로 추출합니다.\n"
                "**2단계**: 추출된 텍스트를 Gemini AI를 사용하여 명확한 요구사항 명세로 재구성합니다.")

    # 세션 상태 초기화
    if 'extracted_df' not in st.session_state:
        st.session_state.extracted_df = pd.DataFrame()

    with st.sidebar:
        st.header("⚙️ 1단계: 추출 설정")
        business_code = st.text_input("사업 코드 (ID 생성용)", value="MFDS")

    uploaded_file = st.file_uploader("분석할 .docx 파일을 업로드하세요.", type=["docx"])

    if uploaded_file:
        extractor = AdvancedDocxExtractor(business_code=business_code)
        with st.spinner("1단계: 문서 구조 분석 및 텍스트 추출 중..."):
            file_bytes = io.BytesIO(uploaded_file.getvalue())
            st.session_state.extracted_df = extractor.process(file_bytes)

    if not st.session_state.extracted_df.empty:
        st.header("1️⃣ 추출 결과")
        st.success(f"✅ 총 {len(st.session_state.extracted_df)}개의 요구사항 항목을 성공적으로 추출했습니다.")
        st.dataframe(st.session_state.extracted_df)
        
        st.download_button(
            label="📥 추출 결과를 CSV 파일로 다운로드",
            data=st.session_state.extracted_df.to_csv(index=False, encoding='utf-8-sig').encode('utf-8-sig'),
            file_name=f"extracted_requirements_{business_code}.csv",
            mime="text/csv",
        )

        st.markdown("---")
        
        # --- Gemini 연동 UI ---
        st.header("2️⃣ Gemini AI로 요구사항 재구성")
        
        api_key = st.text_input("Gemini API 키를 입력하세요.", type="password", help="[Google AI Studio](https://aistudio.google.com/app/apikey)에서 API 키를 발급받을 수 있습니다.")
        
        if api_key:
            default_prompt = """당신은 IT 프로젝트 요구사항 분석 전문가입니다. 아래에 제공된 '원본 추출 데이터'는 문서에서 기계적으로 추출되어 다소 정제되지 않은 텍스트 목록입니다.

            당신의 임무는 다음 지침에 따라 이 데이터를 전문가 수준의 '요구사항 명세'로 재구성하는 것입니다.

            1.  **그룹화 및 구조화**: 연관된 항목들을 논리적인 그룹으로 묶고, 명확한 제목과 부제목을 사용하세요.
            2.  **명료한 문장**: 각 요구사항은 명확하고 간결한 문장으로 다시 작성하세요. (예: "~해야 한다", "~할 수 있어야 한다.")
            3.  **전문 용어 사용**: 적절한 경우, '기능 요구사항', '비기능 요구사항', '데이터 요구사항' 등 전문 용어를 사용하여 분류하세요.
            4.  **출력 형식**: 최종 결과물은 전문가가 작성한 것처럼 보이는 깔끔한 Markdown 형식으로 정리해주세요. (예: 제목, 목록, 테이블 등 활용)
            """
            
            user_prompt = st.text_area("LLM에게 내릴 지시사항 (프롬프트)", value=default_prompt, height=300)

            if st.button("요구사항 재구성 실행 ✨", type="primary"):
                processor = GeminiProcessor(api_key=api_key)
                with st.spinner("Gemini AI가 요구사항을 재구성하고 있습니다..."):
                    reconstructed_text = processor.reconstruct_requirements(st.session_state.extracted_df, user_prompt)
                
                st.subheader("🤖 Gemini 재구성 결과")
                st.markdown(reconstructed_text)

if __name__ == '__main__':
    main()
