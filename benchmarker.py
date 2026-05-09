import json
import os

def run_evaluation():
    # 1. 문제집 로드
    with open('eval_harness.json', 'r', encoding='utf-8') as f:
        tests = json.load(f)
    
    results = []
    
    print(f"--- 에이전트 성능 평가 시작 (총 {len(tests)}개 과제) ---")
    
    for test in tests:
        print(f"\n[과제 {test['id']}] 진행 중: {test['task']}")
        
        # 💡 여기서 실제로 에이전트를 가동합니다. 
        # Claude Code 창에서 수동으로 돌린 후 결과를 입력하거나, 
        # API 연동이 되어 있다면 자동 실행됩니다.
        
        # 지금은 수동 테스트 후 결과를 기록한다고 가정합니다.
        is_passed = input(f"QA 에이전트가 이 과제를 [PASS] 했나요? (y/n): ").lower()
        
        if is_passed == 'y':
            results.append(True)
        else:
            results.append(False)

    # 2. 백분율 계산
    score = (sum(results) / len(tests)) * 100
    print(f"\n==============================")
    print(f"최종 에이전트 성능 점수: {score}%")
    print(f"==============================")

if __name__ == "__main__":
    run_evaluation()