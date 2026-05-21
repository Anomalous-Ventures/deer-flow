---
name: ci-debug
description: Analyze a failing CI workflow run, identify root cause, classify the failure type, and propose a fix. Works with GitHub Actions workflows.
---

# CI Debug Skill

Diagnose and fix failing CI/CD workflow runs.

## Inputs

- **repo**: GitHub repository in `owner/repo` format
- **run_id** (optional): Specific run ID. If omitted, finds the latest failing run.
- **branch** (optional): Filter to a specific branch

## Workflow

### Phase 1: Identify the Failure

1. If no `run_id` provided:
   ```bash
   gh run list --repo <repo> --status failure --limit 5 \
     ${branch:+--branch <branch>} --json databaseId,name,conclusion,event,headBranch,createdAt
   ```
   Select the most recent failing run.

2. Get run details:
   ```bash
   gh run view <run_id> --repo <repo> --json status,conclusion,workflowName,event,headBranch,headSha,jobs
   ```

3. List failed jobs:
   ```bash
   gh run view <run_id> --repo <repo> --log-failed 2>&1 | head -200
   ```

4. For each failed job, extract error section:
   ```bash
   gh run view <run_id> --repo <repo> --log-failed 2>&1 | grep -A 20 "##\[error\]" | head -100
   ```

### Phase 2: Classify

Assign ONE primary classification based on indicators:

| Class | Indicators | Typical Fix |
|-------|-----------|-------------|
| **flaky-test** | Passes on retry; race condition; timing assertion | Add retry/wait; fix race; mark as known-flaky with issue link |
| **dependency** | Package resolution failure; version conflict; registry timeout; 429/503 from package registry | Pin version; clear cache (`actions/cache` key); retry |
| **config-drift** | Missing env var; expired secret; service URL changed; auth failure to external service | Update secret in Vault + rotate to GitHub secrets; update config |
| **code-bug** | Assertion failure; type error; import error in files changed in this PR | Fix the code in the failing file |
| **infra** | Runner OOM; disk full; Docker pull rate limit; network timeout; runner offline | Scale runner; add `--memory` limit; increase cache; retry |
| **workflow-bug** | YAML syntax error; bad step reference; `uses:` path wrong; permission denied | Fix workflow YAML |
| **pre-existing** | Failure on commits before this PR's changes; unrelated test | Note pre-existing; do NOT block PR for it |

### Phase 3: Root Cause Analysis

1. For each failed step, extract:
   - Step name
   - Exit code
   - Last 30 lines of output before failure
   - Any `##[error]` or `Error:` lines

2. Determine whether failure is in:
   - **User code**: Changed files in this run's commit
   - **Test infrastructure**: Test runner, fixtures, setup
   - **CI infrastructure**: Runner, network, secrets, environment

3. Regression check:
   ```bash
   gh run list --repo <repo> --workflow <workflow-name> --limit 10 \
     --json databaseId,conclusion,headBranch,createdAt
   ```
   Did this workflow pass on a prior commit on the same branch?
   - Yes, then no: **regression** (this commit introduced it)
   - Always failing: **pre-existing** (note separately)

4. Cross-reference changed files:
   ```bash
   gh pr list --repo <repo> --head <branch> --json number,files 2>/dev/null | \
     jq -r '.[0].files[].filename'
   ```
   Match failing test/module to changed files.

### Phase 4: Propose Fix

1. **code-bug**: Show the diff -- exact file:line, what to change
2. **config-drift**: 
   - Identify which secret/env is missing
   - Trace to Vault path: `vault kv get <path>`
   - Confirm GitHub secret name and repo/env scope
   - Update procedure: Vault write -> env_loader sync -> verify in GH secrets UI
3. **flaky-test**:
   - Show timing issue or race condition
   - Propose retry annotation or `waitForPort`/`sleep`/event-driven wait
   - Create GitHub issue to track (link in code comment)
4. **dependency**:
   - Show failing package + version
   - Propose pin to last-known-good version
   - Check if registry is down (transient) vs version yanked (permanent)
5. **workflow-bug**:
   - Show exact YAML line with error
   - Show corrected YAML
6. **infra**:
   - Quantify: OOM = memory limit, disk = usage, rate limit = registry
   - Propose: runner size, cache key, registry mirror, retry step

Estimate confidence:
- **High**: Seen this exact pattern; clear root cause in logs
- **Medium**: Likely cause but needs verification
- **Low**: Novel failure or insufficient log output

### Phase 5: Validate Fix

1. **Code fix**: Does it compile/parse?
   ```bash
   # Python
   python -m py_compile <file>
   # TypeScript
   npx tsc --noEmit 2>&1 | head -20
   # Go
   go vet ./... 2>&1 | head -20
   ```
2. **Config fix**: Is the value present in Vault?
   ```bash
   vault kv get <path> 2>&1 | grep -i <key>
   ```
3. **Workflow fix**: Is YAML valid?
   ```bash
   python -c "import yaml; yaml.safe_load(open('.github/workflows/<file>'))"
   ```

### Phase 6: Recommendation

State one of:
- **"Push fix to branch and monitor next run"** -- code/config/workflow fix ready
- **"Retry without changes -- transient failure"** -- infra/registry timeout with no code change needed
- **"Investigate further -- insufficient logs"** -- need debug logs or more context
- **"Pre-existing failure -- do not block PR"** -- regression predates this PR

## Anti-Patterns

- Don't `|| true` failures -- find the real cause
- Don't disable tests that fail -- fix them or create tracking issue with `# TODO(issue-link)`
- Don't retry indefinitely -- 1 automated retry is enough; then investigate
- Don't skip classification -- it determines the fix approach
- Don't conflate pre-existing failures with new regressions

## Common Stax CI Patterns

| Symptom | Cause | Fix |
|---------|-------|-----|
| `KUBECONFIG_B64` decode failure | Stale secret pointing to dead CP | Rotate to cp02 kubeconfig |
| Harbor push 401 | Robot token expired | Regenerate in Harbor UI + update Vault |
| ARC runner offline | ARC controller pod down | `kubectl rollout restart deployment/arc-controller -n arc-systems` |
| `vault kv get` 403 | Vault token expired in CI secret | Re-provision via env_loader |
| Pulumi state lock | Prior run stuck | `pulumi stack export` then `pulumi cancel` |
| `gh pr merge` no checks | Workflow YAML trigger removed | Restore `pull_request` trigger |
