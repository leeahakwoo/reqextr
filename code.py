def _parse_details_from_paragraphs(self, paragraphs: List[Paragraph], req_id: str, level1_bullets: str, level2_bullets: str) -> List[Dict]:
    """
    [수정된 로직] 사용자 지정 블릿과 들여쓰기 수준을 모두 사용하여 계층을 분석합니다.
    """
    final_requirements = []
    bfn_seq_counter = 1
    group_stack = []  # ({'title': str, 'level': int})

    # 개선된 정규표현식 패턴 생성
    def create_bullet_pattern(bullets):
        # 각 블릿 문자를 개별적으로 escape하여 OR 패턴으로 결합
        escaped_bullets = [re.escape(bullet) for bullet in bullets]
        return re.compile(f'^\\s*({"|".join(escaped_bullets)})\\s*')
    
    l1_pattern = create_bullet_pattern(level1_bullets)
    l2_pattern = create_bullet_pattern(level2_bullets)

    print(f"Level 1 bullets: {level1_bullets}")
    print(f"Level 2 bullets: {level2_bullets}")
    print(f"Level 1 pattern: {l1_pattern.pattern}")
    print(f"Level 2 pattern: {l2_pattern.pattern}")

    for p in paragraphs:
        line = p.text.strip()
        if not line:
            continue
        
        print(f"Processing line: '{line}'")
        
        # 현재 문단의 블릿 종류와 들여쓰기 수준 확인
        is_level1 = bool(l1_pattern.search(line))
        is_level2 = bool(l2_pattern.search(line))
        current_level = self._get_indentation_level(p)
        
        print(f"  Level 1 match: {is_level1}")
        print(f"  Level 2 match: {is_level2}")
        print(f"  Indentation level: {current_level}")
        
        # 블릿이 아니면 다음 문단으로
        if not is_level1 and not is_level2:
            continue

        # 상위 그룹으로 돌아가야 하는지 판단 (들여쓰기가 얕아지면)
        while group_stack and current_level < group_stack[-1]['level']:
            group_stack.pop()

        # 블릿 문자 제거 (개선된 방식)
        clean_line = line
        if is_level1:
            clean_line = l1_pattern.sub('', line).strip()
        elif is_level2:
            clean_line = l2_pattern.sub('', line).strip()
        
        print(f"  Clean line: '{clean_line}'")
        
        # 1차 블릿이 나오면 새로운 최상위 그룹이 될 수 있으므로 스택을 재구성
        if is_level1:
            # 현재 들여쓰기 수준보다 깊은 하위 그룹들은 모두 제거
            while group_stack and current_level <= group_stack[-1]['level']:
                group_stack.pop()
            group_stack.append({'title': clean_line, 'level': current_level})
        
        # 2차 블릿이면서, 상위 그룹이 존재할 때
        elif is_level2 and group_stack:
             group_stack.append({'title': clean_line, 'level': current_level})

        # 최종 요구사항으로 기록
        if group_stack:
            # 그룹명은 항상 스택의 첫 번째 요소
            group_name = group_stack[0]['title']
            # 세부 내용은 현재 문단의 내용
            detail_content = clean_line
            
            # 중복 방지: 이미 추가된 내용인지 확인
            is_duplicate = False
            for req in final_requirements:
                if req['요구사항 그룹'] == group_name and req['세부 요구사항 내용'] == detail_content:
                    is_duplicate = True
                    break
            
            if not is_duplicate:
                final_requirements.append({
                    '요구사항 그룹': group_name,
                    '세부 요구사항 내용': detail_content,
                    '세부 요구사항 ID': self._generate_id(req_id, bfn_seq_counter)
                })
                bfn_seq_counter += 1
                print(f"  Added requirement: Group='{group_name}', Detail='{detail_content}'")

    return final_requirements
