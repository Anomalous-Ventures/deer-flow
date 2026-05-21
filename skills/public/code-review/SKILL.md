---
name: code-review
description: Review a GitHub pull request for correctness, security, performance, and style. Fetches the diff, analyzes changes, and posts review comments. Requires GitHub MCP server.
---

# Code Review Skill

Perform thorough code review of GitHub pull requests with severity classification.

## Inputs

- **repo**: GitHub repository in `owner/repo` format
- **pr_number**: Pull request number to review

## Review Workflow

### Phase 1: Fetch PR Metadata

1. Use `mcp__stax-github__get_pull_request` to get PR title, description, and files changed
2. Extract file list and paths
3. Note: current branch, base branch, commit count

### Phase 2: Fetch Diff

1. Use `mcp__stax-github__get_pull_request_files` to get changed files with patch data
2. For each file: extract additions, deletions, modifications
3. Identify file type (backend: .py/.go/.ts; frontend: .tsx/.jsx; config: .yaml/.toml)

### Phase 3: Analyze by Category

**Correctness**
- Logic errors: Off-by-one, wrong operator, unreachable code
- Edge cases: Null/undefined handling, boundary conditions, empty collections
- Type mismatches: Wrong return type, implicit coercion
- Exception handling: Uncaught exceptions, swallowed errors

**Security**
- Injection: SQL, command, template injection (grep for string interpolation in queries)
- Auth/authz: Permission checks, RBAC bypass, hardcoded secrets
- Data exposure: Sensitive logs, public files, unencrypted storage
- CSRF/XSS: Missing CSRF tokens, unescaped output (frontend)
- Dependency vulns: Outdated packages with known CVEs (check against CVE feeds)

**Performance**
- N+1 queries: Loops with database calls (grep for nested DB ops)
- Unnecessary allocations: Large arrays/objects created unconditionally
- Missing indexes: Database queries on unindexed columns
- Blocking I/O: Synchronous network calls in async code
- Caching: Missing or incorrect cache invalidation

**Style**
- Naming: Unclear variable/function names
- Dead code: Unreachable branches, unused imports
- Unnecessary complexity: Nested conditionals, premature abstraction
- Error messages: Actionable? Debuggable? User-facing?
- Comments: Explain WHY, not WHAT; outdated comments

### Phase 4: Cross-Reference Changes

1. For modified functions: grep the codebase for callers
2. For deleted functions: verify no breakage (no remaining references)
3. For API changes: check if signatures match expected consumers
4. For config changes: verify downstream services handle new config

### Phase 5: Check Test Coverage

1. Did behavior change? Tests should change.
2. Are new functions tested? Grep test files for corresponding test cases.
3. Are edge cases covered? Look for tests of boundary conditions.
4. Report gaps: "New error handling added but no test for error case"

### Phase 6: Draft Review Comments

For each finding:
1. **Severity**: Critical (breaks functionality/security) / Warning (code smell / risk) / Nit (style/polish)
2. **File:Line**: Exact location
3. **Issue**: What's wrong + WHY it's wrong
4. **Suggested Fix**: Concrete code change or pattern
5. **Confidence**: High (clear bug) / Medium (potential issue) / Low (style preference)

Example format:
```
**[Critical] SQL Injection in query_user()**
- File: backend/users.py:45
- Issue: Username concatenated directly into SQL query without parameterization
- Why: Attacker can inject SQL commands via username parameter
- Fix: Use parameterized queries: `db.execute("SELECT * FROM users WHERE name = ?", [username])`
- Confidence: High
```

### Phase 7: Post Review

1. Use `mcp__stax-github__create_pull_request_review` with `event: COMMENT`
2. Include all findings (max 10 comments per review -- prioritize critical > warning > nit)
3. Provide actionable fixes for each comment
4. Do NOT set event to APPROVE/REQUEST_CHANGES unless explicitly asked

## Guidelines

- **Be specific.** "This could be a problem" is useless. Show the failure case with concrete inputs.
- **Don't flag style if no linter enforces it.** (Exception: dangling else, missing error handling)
- **For security**: demonstrate exploitability or mark as "potential" with risk level
- **For perf**: measure impact if possible (e.g., "10-100x slower on large datasets")
- **Don't bike-shed.** Skip variable name discussions unless naming is misleading
- **Assume competence.** Don't explain basic concepts; focus on non-obvious issues
- **No placeholders.** Suggested fixes must be compilable/runnable as-is

## Anti-Patterns to Avoid

- Don't flag "add error handling" without explaining which error case
- Don't suggest refactoring if it's orthogonal to the PR scope
- Don't use vague language ("this seems off", "might be a problem")
- Don't review test-only changes with the same rigor as production code
- Don't comment on pre-existing code unless the PR makes it worse

## Confidence Scoring Reference

| Level | Definition | Examples |
|-------|-----------|----------|
| **High** | Clear bug or security risk; reproducible | Off-by-one, missing auth check, SQL injection |
| **Medium** | Likely issue; needs verification | Potential race condition, missing edge case |
| **Low** | Code smell; subjective | Long function, unclear naming, style inconsistency |
