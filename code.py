# app.py

import streamlit as st
import re
import pandas as pd
from typing import List, Dict, Tuple, Any
import docx
from docx.document import Document
from docx.table import Table
from docx.text.paragraph import Paragraph
import io

# --- 로직 클래스 1: 추출기 (견고성 강화) ---
class StreamlitDocxExtractor:
    def __init__(self, business_code: str = "MFDS"):
        self.business_code = business_code

    def _generate_id(self, req_id: str, sequence: int) -> str:
        req_type_code = "F" if req_id.startswith("FUN") else "Q"
        id_num_match = re.search(r'\d+', req_id)
        id_num = id_num_match.group(0) if id_num_match else "000"
        return f"REQ-{self.business_code}-{req_type_code}-{id_num}{sequence:03d}"

    def _get_all_paragraphs_in_order(self, doc: Document) -> List[Paragraph]:
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
        indent = p.paragraph_format.left_indent
        return indent.pt if indent else 0

    def _parse_details_from_paragraphs(self, paragraphs: List[Paragraph], req_id: str, level1_bullets: str, level2_bullets: str) -> List[Dict]:
        final_requirements = []
        bfn_seq_counter = 1
        group_stack = []
        # [수정] SyntaxWarning 해결 및 공백 허용을 위해 Raw String(r'')과 \s* 사용
        l1_pattern = re.compile(rf'^\s*[{re.escape(level1_bullets)}]')
        l2_pattern = re.compile(rf'^\s*[{re.escape(level2_bullets)}]')

        for p in paragraphs:
            # strip() 하지 않은 원본 텍스트로 블릿 여부 먼저 확인
            raw_text = p.text
            if not raw_text.strip(): continue
            
            is_level1 = bool(l1_pattern.search(raw_text))
            is_level2 = bool(l2_pattern.search(raw_text))
            current_level = self._get_indentation_level(p)
            
            if not is_level1 and not is_level2: continue

            while group_stack and current_level < group_stack[-1]['level']:
                group_stack.pop()

            # [수정] 블릿과 앞뒤 공백을 제거한 순수 내용 추출
            clean_line = re.sub(rf'^\s*[{re.escape(level1_bullets + level2_bullets)}]+\s*', '', raw_text).strip()
            if not clean_line: continue

            if is_level1:
                while group_stack and current_level <= group_stack[-1]['level']:
                    group_stack.pop()
                group_stack.append({'title': clean_line, 'level': current_level})
            elif is_level2 and group_stack:
                # 2차 블릿은 스택에 추가하지 않고 바로 사용
                pass

            if group_stack:
                group_name = group_stack[0]['title']
                detail_content = clean_line
                
                is_duplicate = any(req['요구사항 그룹'] == group_name and req['세부 요구사항 내용'] == detail_content for req in final_requirements)
                if not is_duplicate:
                    final_requirements.append({
                        '요구사항 그룹': group_name, '세부 요구사항 내용': detail_content,
                        '세부 요구사항 ID': self._generate_id(req_id, bfn_seq_counter)
                    })
                    bfn_seq_counter += 1
        return final_requirements

    def process(self, docx_file: io.BytesIO, level1_bullets: str, level2_bullets: str) -> Tuple[pd.DataFrame, pd.DataFrame]:
        doc = docx.Document(docx_file)
        all_paragraphs = self._get_all_paragraphs_in_order(doc)
        
        block_markers = [i for i, p in enumerate(all_paragraphs) if '요구사항 분류' in p.text]
        if not block_markers: return pd.DataFrame(), pd.DataFrame()

        all_requirements = []
        high_level_reqs = [] 
        for i, start_index in enumerate(block_markers):
            end_index = block_markers[i+1] if i + 1 < len(block_markers) else len(all_paragraphs)
            block_paragraphs = all_paragraphs[start_index:end_index]
            block_text = "\n".join([p.text for p in block_paragraphs])
            
            # [개선] 콜론(:) 유무, 공백 등 다양한 포맷에 대응하도록 정규식 강화
            req_id_match = re.search(r'요구사항\s*고유번호\s*[:]?\s*([A-Z]{3}-\d{3})', block_text)
            req_name_match = re.search(r'요구사항\s*명칭\s*[:]?\s*(.+?)(?:\n|$)', block_text)

            if not req_id_match or not req_name_match: continue
            req_id, req_name = req_id_match.group(1).strip(), req_name_match.group(1).strip()
            
            details_start_index_offset = next((j + 1 for j, p in enumerate(block_paragraphs) if '세부내용' in p.text), -1)
            
            if details_start_index_offset != -1:
                details_paragraphs = block_paragraphs[details_start_index_offset:]
                parsed_reqs = self._parse_details_from_paragraphs(details_paragraphs, req_id, level1_bullets, level2_bullets)
                
                for req in parsed_reqs:
                    req['요구사항 ID (RFP 원천)'] = req_id
                    req['요구사항 명칭 (RFP 원천)'] = req_name
                all_requirements.extend(parsed_reqs)
                
                if parsed_reqs:
                    # '요구사항 그룹'의 고유한 개수를 세어 요약 정보 생성
                    unique_groups = pd.Series([r['요구사항 그룹'] for r in parsed_reqs]).nunique()
                    high_level_reqs.append({
                        '요구사항 ID': req_id, '요구사항 명칭': req_name,
                        '유형': '기능' if req_id.startswith('FUN') else '비기능',
                        '기능 그룹 수': unique_groups,
                        '총 세부 항목 수': len(parsed_reqs)
                    })
        summary_df = pd.DataFrame(high_level_reqs)
        details_df = pd.DataFrame(all_requirements)
        return summary_df, details_df

# --- 로직 클래스 2: 제공해주신 표준화기 (내용 생성 담당) ---
class RFPStandardizer:
    def __init__(self):
        self.standard_format = {'header_levels': {1: '###', 2: '####', 3: '#####'}, 'requirement_prefix': 'FUR-', 'priority_mapping': {'필수': 'Essential', '권장': 'Recommended', '선택': 'Optional'}}
    def standardize_requirements(self, raw_requirements: List[Dict]) -> List[Dict]:
        return [self._convert_to_standard_format(req, idx) for idx, req in enumerate(raw_requirements, 1)]
    def _convert_to_standard_format(self, requirement: Dict, index: int) -> Dict:
        req_id = f"FUR-{index:03d}"
        sub_requirements = [{'id': f"{req_id}-{sub_idx:03d}", 'name': self._clean_text(detail), 'description': self._generate_description(detail), 'input_info': self._generate_input_info(detail), 'output_info': self._generate_output_info(detail), 'processing_conditions': self._generate_processing_conditions(detail), 'deliverables': self._generate_deliverables(detail)} for sub_idx, detail in enumerate(requirement.get('details', []), 1)]
        return {'id': req_id, 'category': requirement.get('category', '미분류'), 'name': requirement.get('name', '').strip(), 'priority': self._map_priority(requirement.get('priority', '필수')), 'department': requirement.get('department', '전산관리부서'), 'sub_requirements': sub_requirements, 'summary': self._generate_summary(requirement)}
    def _clean_text(self, text: str) -> str:
        if not text: return ""
        return re.sub(r'\s+', ' ', re.sub(r'^[-*>\s]+', '', text)).strip()
    def _map_priority(self, priority: str) -> str:
        return self.standard_format['priority_mapping'].get(priority, 'Essential')
    def _generate_description(self, detail: str) -> str:
        d = {'프로그램관리': '시스템 내 프로그램 등록, 수정, 삭제 관리', '사용자관리': '시스템 사용자 계정 생성, 수정, 삭제 및 권한 부여', '권한관리': '사용자별, 그룹별 시스템 접근 권한 설정', '휴일관리': '공휴일, 임시휴일, 대체공휴일 등록 및 관리', '로그인이력': '사용자 로그인/로그아웃 이력 추적 및 관리', 'TABLE정보': '데이터베이스 테이블 구조 및 메타데이터 관리', 'SMS전송문구관리': 'SMS 발송용 템플릿 등록 및 관리', '학사력관리': '학기별 학사일정 등록 및 관리', '공지사항관리': '전체 공지사항 등록, 수정, 삭제 관리', '공통코드관리': '시스템 전반에서 사용하는 공통코드 관리', '학과부서관리': '대학 내 학과 및 부서 조직도 관리', '대학법인관리': '대학 법인 정보 및 관련 데이터 관리', '부서변경관리': '조직 변경 이력 및 부서 개편 관리'}
        clean_detail = self._clean_text(detail)
        return next((desc for keyword, desc in d.items() if keyword in clean_detail), f"{clean_detail} 기능 구현")
    def _generate_input_info(self, detail: str) -> str:
        d = {'프로그램관리': '프로그램ID, 프로그램명, 설명, 상태', '사용자관리': '사용자ID, 성명, 부서, 직급, 연락처', '권한관리': '사용자ID/그룹ID, 메뉴별 권한(조회/등록/수정/삭제)', '휴일관리': '휴일날짜, 휴일명, 휴일구분, 반복여부', '로그인이력': '사용자ID, 접속IP, 접속시간, 브라우저정보', 'TABLE정보': '테이블명, 컬럼정보, 인덱스, 제약조건', 'SMS전송문구관리': '템플릿ID, 제목, 내용, 발송조건', '학사력관리': '학년도, 학기, 일정구분, 시작일, 종료일', '공지사항관리': '제목, 내용, 첨부파일, 공지기간, 대상자', '공통코드관리': '코드그룹, 코드값, 코드명, 정렬순서, 사용여부'}
        clean_detail = self._clean_text(detail)
        return next((info for keyword, info in d.items() if keyword in clean_detail), f"{clean_detail} 관련 입력 데이터")
    def _generate_output_info(self, detail: str) -> str:
        d = {'프로그램관리': '프로그램 목록, 상세정보', '사용자관리': '사용자 목록, 권한 현황', '권한관리': '권한 매트릭스, 권한 변경 이력', '휴일관리': '휴일 달력, 휴일 목록', '로그인이력': '로그인 이력 조회, 통계 리포트', 'TABLE정보': '테이블 명세서, ERD', 'SMS전송문구관리': '템플릿 목록, 발송 이력', '학사력관리': '학사력 달력, 학사일정표', '공지사항관리': '공지사항 목록, 조회 통계', '공통코드관리': '코드 목록, 코드 체계도'}
        clean_detail = self._clean_text(detail)
        return next((info for keyword, info in d.items() if keyword in clean_detail), f"{clean_detail} 관련 출력 데이터")
    def _generate_processing_conditions(self, detail: str) -> str:
        d = {'프로그램관리': '관리자 권한 필요', '사용자관리': '시스템 관리자 권한 필요', '권한관리': '최고관리자 권한 필요', '휴일관리': '매년 휴일 정보 업데이트', '로그인이력': '실시간 로그 수집, 6개월 이상 보관', 'TABLE정보': 'DB 관리자 권한 필요', 'SMS전송문구관리': '통신사 규격 준수', '학사력관리': '학사관리 권한 필요', '공지사항관리': '부서별 공지 권한 차등 적용', '공통코드관리': '코드 중복 방지, 표준화 준수'}
        clean_detail = self._clean_text(detail)
        return next((cond for keyword, cond in d.items() if keyword in clean_detail), f"{clean_detail} 관련 처리 조건 적용")
    def _generate_deliverables(self, detail: str) -> str:
        d = {'프로그램관리': '프로그램 관리대장', '사용자관리': '사용자 등록대장, 권한 부여 내역', '권한관리': '권한 관리대장', '휴일관리': '연간 휴일 계획표', '로그인이력': '접속 이력 보고서', 'TABLE정보': '데이터베이스 설계서', 'SMS전송문구관리': 'SMS 발송 통계', '학사력관리': '연간 학사일정표', '공지사항관리': '공지사항 발행 대장', '공통코드관리': '공통코드 관리대장'}
        clean_detail = self._clean_text(detail)
        return next((item for keyword, item in d.items() if keyword in clean_detail), f"{clean_detail} 관련 산출물")
    def _generate_summary(self, requirement: Dict) -> str:
        return f"{requirement.get('name', '')} 영역의 {len(requirement.get('details', []))}개 세부 기능 구현"
    def export_to_markdown(self, standardized_requirements: List[Dict], project_name: str) -> str:
        md = [f"# 요구사항 명세서 (식약처 표준 기준)\n", "## 1. 사업 개요", f"- **사업명**: {project_name}", "- **사업 분야**: [사업 분야 입력]", "- **납기**: [별도 명시]\n", "## 2. 기능 요구사항 (Functional Requirements)\n"]
        for req in standardized_requirements:
            md.extend([f"### {req['id']} {req['name']}", f"**분류**: {req['category']}", f"**우선순위**: {req['priority']}", f"**담당부서**: {req['department']}", f"**요약**: {req['summary']}\n"])
            if req['sub_requirements']:
                for sub_req in req['sub_requirements']:
                    md.extend([f"#### {sub_req['id']} {sub_req['name']}", f"- **기능설명**: {sub_req['description']}", f"- **입력정보**: {sub_req['input_info']}", f"- **출력정보**: {sub_req['output_info']}", f"- **처리조건**: {sub_req['processing_conditions']}", f"- **산출정보**: {sub_req['deliverables']}\n"])
        return "\n".join(md)

# --- 로직 클래스 3: 전체 프로세스 지휘자 (Orchestrator) ---
class RFPAnalysisOrchestrator:
    def __init__(self, business_code: str):
        self.extractor = StreamlitDocxExtractor(business_code)
        self.standardizer = RFPStandardizer()
        self.business_code = business_code

    def _prepare_for_standardization(self, details_df: pd.DataFrame) -> List[Dict]:
        if details_df.empty: return []
        raw_requirements = []
        grouped = details_df.groupby('요구사항 ID (RFP 원천)')
        
        for name, group in grouped:
            # 1차, 2차 블릿 내용을 모두 Standardizer에 전달
            details_list = group['세부 요구사항 내용'].unique().tolist()
            req_dict = {
                'category': '기능 요구사항' if name.startswith('FUN') else '비기능 요구사항',
                'name': group['요구사항 명칭 (RFP 원천)'].iloc[0],
                'priority': '필수', 'details': details_list }
            raw_requirements.append(req_dict)
        return raw_requirements

    def run(self, docx_file: io.BytesIO, level1_bullets: str, level2_bullets: str) -> Tuple[str, pd.DataFrame, pd.DataFrame]:
        summary_df, details_df = self.extractor.process(docx_file, level1_bullets, level2_bullets)
        if details_df.empty:
            return "오류: 문서에서 요구사항 정보를 추출하지 못했습니다. '요구사항 분류' 또는 '세부내용' 키워드와 블릿 설정을 확인해주세요.", pd.DataFrame(), pd.DataFrame()
        raw_reqs = self._prepare_for_standardization(details_df)
        standardized_data = self.standardizer.standardize_requirements(raw_reqs)
        project_name = f"{self.business_code} 정보시스템 구축"
        markdown_output = self.standardizer.export_to_markdown(standardized_data, project_name)
        return markdown_output, summary_df, details_df

# --- Streamlit UI 구성 ---
def main():
    st.set_page_config(page_title="요구사항 명세서 자동 생성기", layout="wide")
    st.title("📑 DOCX 요구사항 정의서 자동 표준화 및 생성")
    st.markdown("""
    **어떤 형식의 요구사항 정의서(.docx)든 업로드만 하세요!**
    1.  문서 내의 요구사항을 **자동으로 추출**합니다.
    2.  추출된 내용을 **'식약처(MFDS) RFP 표준'**에 맞춰 변환하고 내용을 보강합니다.
    3.  완성된 **표준 요구사항 명세서**를 Markdown 형식으로 생성합니다.
    """)

    with st.sidebar:
        st.header("⚙️ 분석 설정")
        business_code = st.text_input("사업 코드", value="MFDS", help="사업을 식별하는 고유 코드입니다.")
        st.markdown("---")
        st.subheader("1단계: 원본 문서 분석 설정")
        st.info("문서에 사용된 대표 블릿 문자를 입력하세요. 블릿 앞/뒤 공백이나 약간의 변형은 자동으로 처리됩니다.")
        level1_bullets = st.text_input("기능 그룹 블릿(1차)", value="*◦○•", help="예: * 회원가입 기능")
        level2_bullets = st.text_input("세부 항목 블릿(2차)", value="-·▴", help="예: - 이메일로 가입")

    uploaded_file = st.file_uploader("분석할 .docx 파일을 업로드하세요.", type=["docx"])

    if uploaded_file is not None:
        try:
            orchestrator = RFPAnalysisOrchestrator(business_code=business_code)
            
            with st.spinner("요구사항 추출 및 표준 명세서 생성 중..."):
                # 업로드된 파일을 BytesIO로 변환하여 전달
                file_bytes = io.BytesIO(uploaded_file.getvalue())
                markdown_result, summary_df, details_df = orchestrator.run(
                    file_bytes, level1_bullets, level2_bullets
                )

            if "오류:" in markdown_result:
                st.error(markdown_result)
            else:
                st.success("✅ 표준 요구사항 명세서 생성을 완료했습니다!")
                
                st.subheader("📄 최종 산출물: 표준 요구사항 명세서")
                st.markdown(markdown_result, help="아래 내용은 식약처 표준에 따라 자동 생성된 명세서입니다.")
                
                st.download_button(
                    label="📥 명세서 다운로드 (.md 파일)",
                    data=markdown_result.encode('utf-8'),
                    file_name=f"Standardized_Requirements_{business_code}.md",
                    mime="text/markdown",
                )

                st.markdown("---")

                with st.expander("원본 추출 데이터 보기 (1단계 결과)"):
                    st.subheader("📊 상위 기능 요약")
                    st.dataframe(summary_df)
                    st.subheader("📋 원본 세부 요구사항 목록")
                    st.dataframe(details_df)

        except Exception as e:
            st.error(f"❌ 분석 중 예측하지 못한 오류가 발생했습니다: {e}")
            st.exception(e)

if __name__ == '__main__':
    main()
