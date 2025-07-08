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
    [수정됨] 안정성을 높인 DOCX 요구사항 추출 클래스.
    NoneType 오류를 방지하기 위해 텍스트 추출 로직을 강화했습니다.
    """
    def __init__(self, business_code: str = "MFDS"):
        self.business_code = business_code

    def _generate_id(self, req_id: str, sequence: int) -> str:
        """요구사항 ID를 생성합니다."""
        req_type_code = "F" if req_id.startswith("FUN") else "Q"
        id_num_match = re.search(r'\d+', req_id)
        id_num = id_num_match.group(0) if id_num_match else "000"
        return f"REQ-{self.business_code}-{req_type_code}-{id_num}{sequence:03d}"

    def get_all_text_from_doc(self, doc: Document) -> str:
        """
        [수정됨] 문서의 모든 요소를 재귀적으로 탐색하여 텍스트를 재구성합니다.
        None 값을 빈 문자열로 처리하여 안정성을 확보합니다.
        """
        full_text = []
        # 문서의 모든 최상위 블록(문단, 표 등)을 순서대로 순회
        for block in doc.element.body:
            # 1. 블록이 문단(Paragraph)인 경우
            if isinstance(block, docx.oxml.text.paragraph.CT_P):
                para = docx.text.paragraph.Paragraph(block, doc)
                # para.text가 None일 경우를 대비해 'or ""' 추가
                full_text.append(para.text or "")
            # 2. 블록이 표(Table)인 경우
            elif isinstance(block, docx.oxml.table.CT_Tbl):
                table = docx.table.Table(block, doc)
                for row in table.rows:
                    # 각 셀의 텍스트를 재귀적으로 추출 (이 함수는 이제 항상 문자열을 반환)
                    row_texts = [self.get_all_text_from_cell(cell) for cell in row.cells]
                    full_text.append("\t".join(row_texts))
        
        # full_text 리스트에는 이제 None이 없으므로 안전하게 join 가능
        return "\n".join(full_text)

    def get_all_text_from_cell(self, cell: docx.table._Cell) -> str:
        """
        [수정됨] 표의 셀(cell) 안에 있는 모든 텍스트를 추출합니다 (중첩 표 포함).
        None 값을 빈 문자열로 처리하여 안정성을 확보합니다.
        """
        cell_text_list = []
        # 셀 내부의 모든 블록(문단, 표 등)을 순회
        for block in cell._element:
            # 1. 블록이 문단(Paragraph)인 경우
            if isinstance(block, docx.oxml.text.paragraph.CT_P):
                # 문단의 부모 객체는 'cell'임
                para = docx.text.paragraph.Paragraph(block, cell)
                cell_text_list.append(para.text or "")
            # 2. 블록이 중첩된 표(Table)인 경우
            elif isinstance(block, docx.oxml.table.CT_Tbl):
                # 중첩된 표의 부모 객체는 'cell'임
                inner_table = docx.table.Table(block, cell)
                for row in inner_table.rows:
                    row_texts = [self.get_all_text_from_cell(inner_cell) for inner_cell in row.cells]
                    cell_text_list.append("\t".join(row_texts))
        
        # 셀의 모든 내용을 하나의 문자열로 합쳐서 반환
        return "\n".join(cell_text_list)

    def _parse_block_content(self, req_id: str, req_name: str, content: str, level1_bullets: str, level2_bullets: str) -> List[Dict]:
        """
        사용자가 지정한 블릿을 기반으로 세부내용 텍스트를 계층적으로 파싱합니다.
        """
        final_requirements = []
        bfn_seq_counter = 1

        l1_escaped = re.escape(level1_bullets)
        l2_escaped = re.escape(level2_bullets)
        
        # 1차 블릿을 기준으로 그룹 분할
        sfr_groups_text = re.split(f'\n\s*(?=[{l1_escaped}])', content)

        for group_text in sfr_groups_text:
            group_text = group_text.strip()
            if not group_text: continue

            lines = [line.strip() for line in group_text.split('\n') if line.strip()]
            if not lines: continue

            # 그룹 제목에서 블릿 제거
            group_title = re.sub(f'^[{l1_escaped}]+\s*', '', lines[0])

            # 2차 블릿(세부 항목) 추출
            detailed_points = []
            for line in lines:
                if re.match(f'^\s*[{l2_escaped}]', line):
                    detailed_points.append(re.sub(f'^\s*[{l2_escaped}]+\s*', '', line))

            if not detailed_points: # 2차 블릿이 없으면 1차 블릿 자체가 요구사항
                final_requirements.append({
                    '요구사항 그룹': group_title, '세부 요구사항 내용': group_title,
                    '세부 요구사항 ID': self._generate_id(req_id, bfn_seq_counter)
                })
                bfn_seq_counter += 1
            else: # 2차 블릿이 있으면 각각을 세부 요구사항으로 추가
                for point in detailed_points:
                    final_requirements.append({
                        '요구사항 그룹': group_title, '세부 요구사항 내용': point,
                        '세부 요구사항 ID': self._generate_id(req_id, bfn_seq_counter)
                    })
                    bfn_seq_counter += 1
        return final_requirements

    def process(self, docx_file: io.BytesIO, level1_bullets: str, level2_bullets: str) -> pd.DataFrame:
        """
        업로드된 DOCX 파일을 처리하여 요구사항을 DataFrame으로 반환합니다.
        """
        doc = docx.Document(docx_file)

        st.info("문서의 텍스트를 재구성하는 중...")
        full_text = self.get_all_text_from_doc(doc)
        
        block_pattern = r"(요구사항 분류(?:.|\n)*?요구사항 고유번호\s+[A-Z]{3}-\d{3}(?:.|\n)*?)(?=요구사항 분류|$)"
        blocks = re.findall(block_pattern, full_text)

        st.info(f"총 {len(blocks)}개의 요구사항 블록을 식별했습니다.")
        if not blocks:
            st.warning("문서에서 '요구사항 분류/고유번호' 패턴을 찾을 수 없습니다. 문서 구조를 확인해주세요.")
            return pd.DataFrame()

        all_requirements = []
        for block in blocks:
            req_id_match = re.search(r'요구사항 고유번호\s+([A-Z]{3}-\d{3})', block)
            req_name_match = re.search(r'요구사항 명칭\s+(.+?)(?:\n|$)', block)

            if not req_id_match or not req_name_match: continue
            
            req_id, req_name = req_id_match.group(1).strip(), req_name_match.group(1).strip()
            details_match = re.search(r'세부내용\s*((?:.|\n)*)', block, re.DOTALL)
            
            if details_match:
                content = details_match.group(1).strip()
                parsed_reqs = self._parse_block_content(req_id, req_name, content, level1_bullets, level2_bullets)
                for req in parsed_reqs:
                    req['요구사항 ID (RFP 원천)'] = req_id
                    req['요구사항 명칭 (RFP 원천)'] = req_name
                all_requirements.extend(parsed_reqs)

        if not all_requirements:
            st.warning("요구사항 블록은 찾았으나, 세부 내용을 파싱하지 못했습니다. 블릿 설정을 확인해주세요.")
            return pd.DataFrame()

        df = pd.DataFrame(all_requirements)
        df['출처'] = 'DOCX 문서'
        df['유형'] = df['요구사항 ID (RFP 원천)'].apply(lambda x: '기능' if x.startswith('FUN') else '비기능')
        
        column_order = [
            '세부 요구사항 ID', '요구사항 그룹', '세부 요구사항 내용', 
            '요구사항 ID (RFP 원천)', '요구사항 명칭 (RFP 원천)', '유형', '출처'
        ]
        # 없는 컬럼이 있을 수 있으므로 reindex 사용
        return df.reindex(columns=column_order)

# --- Streamlit UI 구성 ---
def main():
    st.set_page_config(page_title="요구사항 추출기", layout="wide", initial_sidebar_state="expanded")
    st.title("📄 DOCX 요구사항 정의서 자동 추출기")
    st.markdown("DOCX 형식의 요구사항 정의서를 업로드하면, 내용을 분석하여 세부 요구사항 목록을 생성합니다.")

    with st.sidebar:
        st.header("⚙️ 분석 설정")
        
        business_code = st.text_input("사업 코드", value="MFDS", help="요구사항 ID 생성에 사용됩니다.")
        
        st.markdown("---")
        
        st.subheader("블릿(Bullet) 체계 설정")
        level1_bullets = st.text_input("1차 블릿 문자 (그룹)", value="◦○•", help="요구사항 그룹 기호를 모두 입력하세요.")
        level2_bullets = st.text_input("2차 블릿 문자 (세부 항목)", value="-*·▴", help="세부 요구사항 기호를 모두 입력하세요.")
        st.info("문서에 사용된 블릿을 정확하게 입력해야 분석 성공률이 높아집니다.")

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
                    # Cache the conversion to prevent computation on every rerun
                    return _df.to_csv(index=False, encoding='utf-8-sig').encode('utf-8-sig')

                csv_data = convert_df_to_csv(requirements_df)
                
                st.download_button(
                    label="📥 추출 결과를 CSV 파일로 다운로드",
                    data=csv_data,
                    file_name=f"extracted_requirements_{business_code}.csv",
                    mime="text/csv",
                )
            else:
                # 이전에 경고 메시지가 extractor.process() 내부에서 표시되므로 추가 메시지는 생략 가능
                pass

        except Exception as e:
            st.error(f"❌ 분석 중 오류가 발생했습니다: {e}")
            st.exception(e) # 개발자 확인용 상세 에러 로그

if __name__ == '__main__':
    main()
