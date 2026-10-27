#!/usr/bin/env bash
# 실패한 E2E 테스트를 Playwright healer 에이전트로 고치고, heal-guard 검사 결과를 남긴다.
#
# 실행 위치: ui_tests/   (Jenkins에서는 dir('ui_tests') 안에서 호출)
# 필요한 것: claude CLI, ANTHROPIC_API_KEY(또는 CLAUDE_CODE_OAUTH_TOKEN), node, git
# 종료 코드:
#   0 = 수정 후 전체 통과, heal-guard 경고 없음        → PR 생성
#   2 = 수정은 했지만 사람이 꼭 봐야 할 변경 있음 → PR 생성(needs-review 표시)
#   1 = heal-guard 위반(테스트 밖 파일 수정 등) 또는 수정 실패 → PR 만들지 않음
set -uo pipefail
cd "$(dirname "$0")/.."

BASE_REF=$(git rev-parse HEAD)
MAX_TURNS=${HEAL_MAX_TURNS:-40}
MAX_BUDGET=${HEAL_MAX_BUDGET_USD:-2}

PROMPT=$(cat <<'EOF'
Run the Playwright test suite in this directory and fix the failing tests.
Rules:
- Edit only files under tests/. Never edit application code, configs, or other directories.
- Keep every assertion as strict as before. Do not delete assertions, do not switch to
  positional locators (.first(), .nth(), .last()), do not negate assertions to make them pass.
- If the application itself looks broken (not just a changed selector or text),
  mark the test with test.fixme() and add a comment describing the observed behavior.
- Prefer values read from the running app (for example the /health response) over hard-coded
  values that will go stale on the next release.
Finish with a short Korean summary: which tests failed, the root cause of each, and what you changed.
EOF
)

# 설치된 claude CLI가 지원하는 옵션만 붙인다 (오래된 버전에서 usage 에러로 즉시 죽는 것 방지)
command -v claude >/dev/null || { echo "❌ claude CLI가 설치되지 않았습니다 (jenkins 사용자 PATH 확인)"; exit 1; }
CLAUDE_HELP=$(claude --help 2>&1)
# --max-turns는 공식 print 모드 옵션이지만 claude --help 목록에는 나오지 않아 검사하지 않는다
for f in --agent --strict-mcp-config; do
  if ! grep -qE -- "$f([ ,=]|$)" <<<"$CLAUDE_HELP"; then
    echo "❌ claude CLI에 $f 옵션이 없습니다. 'claude update' 후 다시 실행하세요."; exit 1
  fi
done
OPTIONAL_ARGS=()
grep -qE -- "--permission-prompts([ ,=]|$)" <<<"$CLAUDE_HELP" && OPTIONAL_ARGS+=(--permission-prompts none)
if grep -qE -- "--max-budget-usd([ ,=]|$)" <<<"$CLAUDE_HELP"; then
  OPTIONAL_ARGS+=(--max-budget-usd "$MAX_BUDGET")
else
  echo "⚠️ 이 claude CLI는 --max-budget-usd를 지원하지 않아 턴 상한(${MAX_TURNS})만 적용합니다."
fi

echo "▶ healer 실행 (max-turns=${MAX_TURNS}, budget=\$${MAX_BUDGET})"
claude -p "$PROMPT" \
  --agent playwright-test-healer \
  --mcp-config .mcp.json --strict-mcp-config \
  --allowedTools "Read" "Glob" "Grep" "Edit" "Write" "mcp__playwright-test" \
  --disallowedTools "Bash" "WebFetch" "WebSearch" \
  --permission-mode acceptEdits \
  --max-turns "$MAX_TURNS" \
  "${OPTIONAL_ARGS[@]}" \
  --output-format json > heal-output.json
CLAUDE_EXIT=$?

# 에이전트의 요약과 비용을 리포트용으로 뽑아 두고, 턴·예산 한도로 중단됐으면 실패로 처리한다
node -e '
  const o = JSON.parse(require("fs").readFileSync("heal-output.json", "utf8"));
  require("fs").writeFileSync("heal-summary.md", (o.result ?? "(요약 없음)") + "\n");
  console.log(`▶ healer 종료: turns=${o.num_turns ?? "?"}, cost=$${(o.total_cost_usd ?? 0).toFixed(3)}, subtype=${o.subtype ?? "?"}`);
  if (o.is_error === true || (o.subtype && o.subtype !== "success")) {
    console.error(`▶ healer 중단: ${o.subtype ?? "is_error"}`);
    process.exit(3);
  }
'
PARSE_EXIT=$?
if [ "$PARSE_EXIT" -eq 3 ] && [ "$CLAUDE_EXIT" -eq 0 ]; then
  CLAUDE_EXIT=3
elif [ "$PARSE_EXIT" -ne 0 ] && [ "$PARSE_EXIT" -ne 3 ]; then
  echo "(healer 출력 파싱 실패)" > heal-summary.md
  [ "$CLAUDE_EXIT" -eq 0 ] && CLAUDE_EXIT=4
fi

echo "▶ 수정된 테스트 재실행"
npx playwright test --reporter=list > heal-rerun.log 2>&1
RERUN_EXIT=$?
tail -5 heal-rerun.log

echo "▶ heal-guard 검사"
node scripts/heal-guard.mjs "$BASE_REF" "$RERUN_EXIT" "$CLAUDE_EXIT"
exit $?
