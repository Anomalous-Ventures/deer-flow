---
name: stax-implement
description: Implement a STAX platform roadmap item end-to-end. Load this skill when the user asks to implement, build, fix, or complete any STAX roadmap task. Covers the full workflow from codebase research through PR creation, covering Pulumi infrastructure, Python services, K8s manifests, tests, and sentinel validation.
---

# STAX Implementation Workflow

Load this alongside `stax-context` before implementing any STAX roadmap item.

## Pre-Implementation Checklist

Before writing any code:

1. **Load stax-context** — architecture, conventions, active constraints.
2. **Identify the item** — find in `roadmap.json` or GitHub Issues.
3. **Research the codebase** — use bash to grep/find relevant files. Read module dependencies.
4. **Check open PRs** — `gh pr list --repo Anomalous-Ventures/stax` — avoid overlap.
5. **Identify blast radius** — which other modules/services does the change affect?
6. **Draft acceptance criteria** — inputs, outputs, edge cases, failure modes.

## Implementation Phases

### Phase 1: Research

```bash
# Find relevant existing code
grep -r "keyword" /path/to/repo/pulumi/ --include="*.py" -l

# Read the module being changed
cat pulumi/modules/services/<service>.py

# Check existing tests for the module
cat pulumi/tests/test_<service>_helpers.py

# Understand current deployed state
kubectl get configmap -n llm | grep deer-flow
kubectl get deploy -n llm | grep deer-flow
```

### Phase 2: Plan

Produce a brief plan:
```
Files to change:
  - pulumi/modules/services/<name>.py  (what + why)
  - pulumi/tests/test_<name>_helpers.py  (tests to add/update)

Acceptance criteria:
  - [ ] <specific observable behavior>
  - [ ] Tests pass: pytest -x tests/test_<name>_helpers.py
  - [ ] No new warnings from linter

Risks:
  - <backward compat concern or blast radius note>
```

### Phase 3: Implement

**Order matters:**
1. Write/update tests FIRST (TDD). Run them — they should fail.
2. Implement the change.
3. Run tests again — they must pass.
4. Run full suite: `cd pulumi && python -m pytest tests/ -x -q`
5. Check for linter issues: `cd pulumi && python -m flake8 modules/ --max-line-length=120`

**Code discipline:**
- No speculative features. Only what the item requires.
- Delete dead code, don't comment out.
- Error handling only at system boundaries.
- Functions < 100 lines, complexity < 8.
- No AI attribution in code or comments.

### Phase 4: Commit and PR

```bash
# Verify branch
git branch --show-current  # must NOT be main

# Stage only relevant files
git add pulumi/modules/services/<name>.py pulumi/tests/test_<name>_helpers.py

# Commit with STAX format
git commit -m "$(cat <<'EOF'
<type>(<scope>): <subject>

- <bullet 1>
- <bullet 2>
EOF
)"

# Push and create PR
git push origin <branch>
gh pr create --title "<type>(<scope>): <subject>" --body "$(cat <<'EOF'
## Summary
- <what changed>
- <why>

## Test plan
- [ ] pytest passes: `python -m pytest pulumi/tests/ -x -q`
- [ ] No new linter warnings
EOF
)"
```

### Phase 5: Post-Merge Validation

If the change affects a running service:

```bash
# Wait for image build (if Dockerfile changed)
gh run list --workflow build-images.yml --limit 3

# After image SHA auto-bump PR merges, validate deployment
kubectl rollout status deploy/deer-flow-gateway -n llm
kubectl get pods -n llm | grep deer-flow
curl -sf http://deer-flow-gateway.llm.svc.cluster.local:8001/health

# For DeerFlow changes: sentinel validation
# cd to sentinel repo and run:
# sentinel validate plans/deer-flow.yaml
```

## Common STAX Implementation Patterns

### Adding a new model to DeerFlow config

1. Edit `pulumi/modules/services/deer_flow.py` → `_build_config_yaml()`
2. Bump `config_version` by 1
3. Add model block under the correct node section
4. Update `defaults:` if role assignment changes
5. Add test assertions in `test_deer_flow_helpers.py`
6. Patch ConfigMap directly for immediate effect:
   ```bash
   # Get current config
   kubectl get cm deer-flow-config -n llm -o json | jq -r '.data["config.yaml"]' > /tmp/config.yaml
   # Edit /tmp/config.yaml
   # Apply patch via kubectl
   kubectl create cm deer-flow-config -n llm --from-file=config.yaml=/tmp/config.yaml --dry-run=client -o yaml | kubectl apply -f -
   kubectl rollout restart deploy/deer-flow-gateway -n llm
   ```

### Adding a new Pulumi-managed resource

1. Add resource in `pulumi/modules/services/<service>.py`
2. Add `_comp_labels(<component>)` for consistent labeling
3. Add ConfigMap hash annotation if config drives the resource
4. Update `_build_config_yaml()` if YAML config is involved
5. Add unit test in `pulumi/tests/test_<service>_helpers.py`
6. Run `pulumi preview` in the stack dir to verify no unintended changes

### Creating a new K8s secret via ESO

```python
# In modules/services/<service>.py
k8s.apiextensions.CustomResource(
    f"{SERVICE}-secret",
    api_version="external-secrets.io/v1beta1",
    kind="ExternalSecret",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name=f"{SERVICE}-secret",
        namespace=namespace,
        labels=_comp_labels("secret"),
    ),
    spec={
        "refreshInterval": "1h",
        "secretStoreRef": {"name": "vault-backend", "kind": "ClusterSecretStore"},
        "target": {"name": f"{SERVICE}-secret"},
        "data": [
            {"secretKey": "MY_KEY", "remoteRef": {"key": "llm/deer-flow", "property": "my_key"}}
        ],
    },
    opts=ResourceOptions(parent=parent),
)
```

### Updating roadmap.json after completion

```python
import json, datetime
with open("roadmap.json") as f: d = json.load(f)
# Find and update the item status
item["status"] = "COMPLETED"
d["last_updated"] = datetime.date.today().isoformat()
with open("roadmap.json", "w") as f: json.dump(d, f, indent=2)
```

## Troubleshooting Reference

| Symptom | First check |
|---------|------------|
| Pod CrashLoopBackOff | `kubectl logs -n <ns> <pod> --previous --tail=50` |
| PVC stuck Pending | `kubectl describe pvc -n <ns> <name>` — check replica/node affinity |
| Pulumi plan shows unexpected deletes | check `ignore_changes`, resource URN, and provider config |
| CI failing on pytest | run locally: `cd pulumi && python -m pytest tests/ -x -q` |
| Image not updated after merge | check `gh run list --workflow build-images.yml --limit 3` |
| DeerFlow gateway 500 | check config.yaml syntax: `kubectl get cm deer-flow-config -n llm -o yaml` |
| Ollama model not loading | `kubectl exec -n llm <ollama-pod> -- ollama list` |
