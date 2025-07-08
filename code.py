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
    [최종 완성본] 일반 문단, 표(Table), 다양한 블릿 계층 구조를 모두 처리하는 클래스.
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
        """
        문서의 모든 문단 객체를 표 안의 내용까지 포함하여 순서대로 가져옵니다.
        """
        all_paragraphs = []
        for block in doc.element.body:
            if isinstance(block, docx.oxml.text.paragraph.CT_P):
                all_paragraphs.append(Paragraph(block, doc))
            elif isinstance(block, docx.oxml.table.CT_Tbl):
                table = Table(block, doc)
                for row in table.rows:
                    for cell in row.cells:
                        for para in cell.paragraphs:
                            all_paragraphs.append(para)
        return all_paragraphs

    def _is_bullet_paragraph(self, p: Paragraph, bullet_chars: str) -> bool:
        """
        문단이 주어진 블릿 문자로 시작하거나, 자동 글머리 기호 스타일인지 확인합니다.
        """
        line_text = p.text.strip()
        if not line_text:
            return False
        if p.style and 'List' in p.style.name:
            return True
        if re.match(f'^[{re.escape(bullet_chars)}]', line_text):
            return True
        return False
    
    def _parse_details_from_paragraphs(self, paragraphs: List[Paragraph], req_id: str, level1_bullets: str, level2_bullets: str) -> List[Dict]:
        """
        [수정됨] 계층 구조 인식 로직을 개선하여 단일 레벨 블릿도 처리합니다.
        """
        final_requirements = []
        bfn_seq_counter = 1
        current_group_title = ""
        
        # 임시 버퍼: 1차 블릿이 그룹 제목인지 단독 요구사항인지 판단하기 위해 사용
        temp_group_buffer = None

        def flush_buffer():
            """버퍼에 저장된 이전 그룹 정보를 최종 결과에 추가하는 함수"""
            nonlocal bfn_seq_counter
            if temp_group_buffer:
                final_requirements.append({
                    '요구사항 그룹': temp_group_buffer['title'],
                    '세부 요구사항 내용': temp_group_buffer['title'], # 2차 블릿이 없었으므로 그룹명과 내용이 동일
                    '세부 요구사항 ID': self._generate_id(req_id, bfn_seq_counter)
                })
                bfn_seq_counter += 1
                
        for p in paragraphs:
            line = p.text.strip()
            if not line:
                continue

            is_level1 = self._is_bullet_paragraph(p, level1_bullets)
            is_level2 = self._is_bullet_paragraph(p, level2_bullets)

            # 1. 1차 블릿을 만난 경우
            if is_level1:
                # 이전에 처리되지 않은 1차 블릿 그룹이 있었다면 먼저 결과에 추가
                flush_buffer()
                
                # 현재 1차 블릿을 새로운 그룹 후보로 버퍼에 저장
                current_group_title = re.sub(f'^[{re.escape(level1_bullets)}]+\s*', '', line)
                temp_group_buffer = {'title': current_group_title}

            # 2. 2차 블릿을 만난 경우
            elif is_level2 and current_group_title:
                # 2차 블릿이 나타났다는 것은, 버퍼의 1차 블릿이 그룹 제목이 맞다는 의미
                temp_group_buffer = None # 버퍼를 비워 중복 추가 방지
                
                point_text = re.sub(f'^\s*[{re.escape(level2_bullets)}]+\s*', '', line)
                final_requirements.append({
                    '요구사항 그룹': current_group_title,
                    '세부 요구사항 내용': point_text,
                    '세부 요구사항 ID': self._generate_id(req_id, bfn_seq_counter)
                })
                bfn_seq_counter += 1
            
            # 3. 블릿이 아닌 일반 텍스트가 그룹에 속한 경우 (2차 블릿 처럼 취급)
            elif not is_level1 and not is_level2 and temp_group_buffer:
                # 버퍼에 있는 1차 블릿을 그룹으로 확정
                current_group_title = temp_group_buffer['title']
                temp_group_buffer = None

                final_requirements.append({
                    '요구사항 그룹': current_group_title,
                    '세부 요구사항 내용': line, # 일반 텍스트 라인
                    '세부 요구사항 ID': self._generate_id(req_id, bfn_seq_counter)
                })
                bfn_seq_counter +=1


        # 루프가 끝난 후, 마지막으로 버퍼에 남아있는 1차 블릿 처리
        flush_buffer()
        
        return final_requirements


    def process(self, docx_file: io.BytesIO, level1_bullets: str, level2_bullets: str) -> pd.DataFrame:
        """
        문서 전체를 순회하며 표 내부를 포함한 모든 요구사항을 분석합니다.
        """
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
                parsed_reqs = self._parse_details_from_paragraphs(details_paragraphs, req_id, level1_bullets, level2_bullets)
                
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
    st.markdown("MS Word의 자동 글머리 기호 및 **표(Table)에 포함된 내용**까지 분석하여 세부 요구사항 목록을 생성합니다.")

    with st.sidebar:
        st.header("⚙️ 분석 설정")
        
        business_code = st.text_input("사업 코드", value="MFDS", help="요구사항 ID 생성에 사용됩니다. (예: REQ-**MFDS**-F-001001)")
        
        st.markdown("---")
        
        st.subheader("블릿(Bullet) 체계 설정")
        level1_bullets = st.text_input("1차 블릿 문자 (그룹)", value="*◦○•", help="요구사항 그룹을 나타내는 글머리 기호를 모두 입력하세요.")
        level2_bullets = st.text_input("2차 블릿 문자 (세부 항목)", value="-·▴", help="세부 요구사항을 나타내는 글머리 기호를 모두 입력하세요.")
        st.info("문서에 사용된 블릿을 정확히 입력해야 분석 성공률이 높아집니다.")

    uploaded_file = st.file_uploader("분석할 .docx 파일을 업로드하세요.", type=["docx"])

    if uploaded_file is not None:
        try:
            extractor = StreamlitDocxExtractor(business_code=business_code)
            
            with st.spinner("파일 분석 및 요구사항 추출 중... (표/계층 구조 포함)"):
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
