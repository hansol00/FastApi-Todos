// healer가 만든 변경(diff)을 검사해 "고친 것"인지 "느슨하게 만든 것"인지 가려내고 heal-report.md를 쓴다.
//
// 사용법: node scripts/heal-guard.mjs <기준 커밋> <재실행 종료코드> <claude 종료코드>
// 종료 코드: 0 = 통과, 2 = 사람 확인 필요(경고 있음), 1 = 위반 또는 실패
//
// 검사 항목은 Currents의 AI 생성 테스트 리뷰 체크리스트(6·7번: 수정 방향, 조용한 skip)를 기준으로 했다.
import { execFileSync } from "node:child_process";
import { writeFileSync, existsSync, readFileSync } from "node:fs";

const [baseRef = "HEAD", rerunExit = "1", claudeExit = "0"] = process.argv.slice(2);
const git = (...args) => execFileSync("git", args, { encoding: "utf8" });

// 테스트 실행과 claude CLI가 만드는 산출물 — .gitignore가 빠져 있어도 "테스트 밖 수정"으로 차단하지 않는다
const IGNORED = [
  /^heal-(output\.json|summary\.md|rerun\.log|report\.md)$/,
  /^(test-results|playwright-report|blob-report|node_modules)\//,
  /^playwright\/\.cache\//,
  /^\.claude\/settings\.local\.json$/,
];

// ui_tests/ 기준 상대 경로로 바뀐 파일 목록 (스테이징 여부와 무관하게 작업 트리 기준)
const changed = git("diff", "--name-only", "--relative", baseRef, "--", ".")
  .split("\n").filter(Boolean)
  .concat(git("ls-files", "--others", "--exclude-standard", ".").split("\n").filter(Boolean))
  .filter((f) => !IGNORED.some((re) => re.test(f)));

const violations = [];
const warnings = [];
const rows = [];

for (const file of changed) {
  if (!file.startsWith("tests/")) {
    violations.push(`테스트 밖 파일 수정: \`${file}\``);
    continue;
  }
  const diff = git("diff", "--relative", "-U0", baseRef, "--", file);
  const added = diff.split("\n").filter((l) => l.startsWith("+") && !l.startsWith("+++")).map((l) => l.slice(1));
  const removed = diff.split("\n").filter((l) => l.startsWith("-") && !l.startsWith("---")).map((l) => l.slice(1));
  const count = (lines, re) => lines.filter((l) => re.test(l)).length;

  const before = countExpect(showAt(baseRef, file));
  const after = existsSync(file) ? countExpect(readFileSync(file, "utf8")) : 0;
  rows.push(`| \`${file}\` | ${before} → ${after} | +${added.length} / -${removed.length} |`);

  if (after < before) warnings.push(`\`${file}\`: expect() 개수 감소 (${before} → ${after}) — 검증이 빠졌는지 확인`);
  if (count(added, /test\.(fixme|skip)\s*\(/)) warnings.push(`\`${file}\`: test.fixme/skip 추가 — healer가 "앱이 고장났다"고 판단한 테스트. 버그 이슈로 등록 필요`);
  if (count(added, /\.(first|last)\(\)|\.nth\(/) > count(removed, /\.(first|last)\(\)|\.nth\(/))
    warnings.push(`\`${file}\`: positional locator(.first/.nth/.last) 추가 — 엉뚱한 요소를 가리킬 수 있음`);
  if (count(added, /\.not\.|toHaveCount\(0\)|toBeHidden\(/) > count(removed, /\.not\.|toHaveCount\(0\)|toBeHidden\(/))
    warnings.push(`\`${file}\`: negated assertion(not./toHaveCount(0)/toBeHidden) 추가 — 요소가 없어도 통과할 수 있음`);
  if (count(added, /waitForTimeout\(/) > count(removed, /waitForTimeout\(/))
    warnings.push(`\`${file}\`: waitForTimeout 추가 — hard wait 대신 web-first assertion 사용 권장`);
}

// 기준 커밋 시점의 파일 내용 (새로 생긴 파일이면 빈 문자열)
function showAt(ref, file) {
  try {
    return execFileSync("git", ["show", `${ref}:./${file}`], { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] });
  } catch {
    return "";
  }
}

function countExpect(src) {
  return (src.match(/\bexpect\s*\(/g) ?? []).length;
}

if (claudeExit !== "0") violations.push(`healer 비정상 종료 (exit ${claudeExit}) — 턴/예산 한도 초과 가능성`);
if (rerunExit !== "0") violations.push("수정 후 재실행에서도 실패한 테스트가 남아 있음");
if (changed.length === 0) violations.push("변경 사항 없음 — healer가 아무것도 고치지 못함");

const status = violations.length ? "❌ 차단" : warnings.length ? "⚠️ 사람 확인 필요" : "✅ 자동 검사 통과";
const summary = existsSync("heal-summary.md") ? readFileSync("heal-summary.md", "utf8").trim() : "(요약 없음)";

const report = `## AI Heal 리포트 — ${status}

### heal-guard 검사
${violations.map((v) => `- ❌ ${v}`).join("\n") || "- 위반 없음"}
${warnings.map((w) => `- ⚠️ ${w}`).join("\n") || "- 경고 없음"}

### 변경 파일
| 파일 | expect() 개수 | 줄 변화 |
|---|---|---|
${rows.join("\n") || "| (없음) | | |"}

### healer 요약
${summary}

---
리뷰 체크: assertion이 여전히 원래 요구사항(비즈니스 결과)을 검증하는가? locator가 원래와 같은 요소를 가리키는가? fixme는 이슈로 등록했는가?
`;

writeFileSync("heal-report.md", report);
console.log(report);
process.exit(violations.length ? 1 : warnings.length ? 2 : 0);
