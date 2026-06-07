---
name: stax-context
description: Load STAX platform context before any STAX roadmap, infrastructure, or code work. Provides architecture overview, repo layout, coding conventions, deployment workflow, and active constraints. Always load this skill first when asked to work on anything in the Anomalous-Ventures/stax repository or its services.
---

# STAX Platform Context

Load this skill before any work touching the STAX Kubernetes platform. It provides architecture, conventions, and constraints the agent must follow.

## Repository Layout

```
Anomalous-Ventures/stax
├── pulumi/
│   ├── stacks/          # One dir per deployed stack (numbered)
│   │   ├── 08-ai/       # LiteLLM, Ollama, Langfuse, MCPO
│   │   ├── 27-deer-flow/ # DeerFlow agent harness
│   │   └── ...
│   ├── modules/
│   │   └── services/    # Reusable Pulumi modules per service
│   └── tests/           # Unit tests for Pulumi helpers
├── .github/workflows/   # CI: lint, pytest, kubescape, trivy
└── roadmap.json         # Platform roadmap (products > features > items)
```

Related repos (same org):
- `Anomalous-Ventures/deer-flow` — DeerFlow fork (this agent's harness)
- `Anomalous-Ventures/ophanim-qa` — QA test plans (`plans/deer-flow.yaml` is merge gate)
- `Anomalous-Ventures/healthcare-agent` — SOAP note / ambient docs agent
- `Anomalous-Ventures/hive` — Hive AI platform

## Cluster Architecture

**Nodes:**
| Node | Arch | RAM | GPU | Role |
|------|------|-----|-----|------|
| spark | ARM64 | 32Gi | — | medium Ollama models |
| spark02 | ARM64 Grace | 88Gi | — | large Ollama models |
| gpu01 | x86 | ~128Gi | A100 | heavy inference |
| gpu02 | x86 | ~128Gi | A100 | vision + phi4 |
| rk5c-02 | ARM64 | 120Gi | — | general workloads |
| bee01/02/03 | x86 | ~32Gi | — | media/misc |

**Namespaces:**
- `llm` — AI services (LiteLLM, Ollama, Langfuse, DeerFlow, SearXNG)
- `deer-flow-sandboxes` — isolated sandbox pods spawned by DeerFlow provisioner
- `devops` — ARC runners, Vault, cert-manager, external-secrets
- `archivist` — media stack (Jellyfin, Sonarr, Radarr, qBittorrent)

**Secrets pipeline:** `.env` files → `env_loader` → Vault KV v2 → ExternalSecrets → K8s Secrets. Never bake secrets into Pulumi YAML.

**Pulumi backend:** MinIO S3 at `s3://pulumi-state` (internal cluster MinIO). Never use file:// backend.

## Coding Conventions

### Python
- Type hints everywhere. `from __future__ import annotations`.
- Functions < 100 lines. Cyclomatic complexity < 8. Max 5 positional params.
- Error handling at system boundaries only. Internal code trusts types.
- No commented-out code. No speculative features.
- Tests: pytest + anyio for async. Three tiers: unit, integration, smoke.

### Pulumi (Python)
- Every service in `modules/services/<name>.py` as a single `deploy_<name>()` function.
- Labels: always include `app.kubernetes.io/name`, `app.kubernetes.io/part-of: stax`, `stax.io/stack`.
- ConfigMap hash annotation pattern for automatic pod reload on config change.
- `ignore_changes=["kubeconfig"]` on k8s.Provider to prevent URN churn.
- Never use `pulumi.export` for secrets.

### Git / PR workflow
1. Feature branch: `feat/<scope>` (never commit to main directly).
2. Commit format: `<type>(<scope>): <subject>` followed by bullet points.
3. Push branch → `gh pr create` → CI must pass → `gh pr merge --squash --delete-branch`.
4. Max 3 open PRs per repo at any time.
5. No AI attribution in commit messages, comments, or PR descriptions.

## Deployment Workflow

```
edit code
└─> git add + commit
└─> git push origin feat/<branch>
└─> gh pr create
└─> wait for CI (lint + pytest + kubescape + trivy)
└─> if CI green: gh pr merge --squash --delete-branch
└─> (image build auto-triggers via build-images.yml if Dockerfile changed)
└─> (auto-bump PR opens in stax updating image SHA)
└─> validate deployment (curl health, ophanim run, kubectl get pods)
```

## Active Constraints (must check before any change)

- **No cloud model endpoints.** All models via Ollama (spark/spark02/gpu01/gpu02) or LiteLLM proxy (internal only).
- **No secrets in code.** Vault → ExternalSecrets pipeline only.
- **Branch deploy guard.** Never push directly to `main`.
- **Ophanim gate.** DeerFlow changes require `ophanim-qa validate plans/deer-flow.yaml` to pass before merge (9 checkpoints).
- **Harbor registry.** All images pushed to `harbor.spooty.io/<project>/<name>:<sha-tag>`.
- **Longhorn CSI.** PVCs use `storageClassName: longhorn`. Replicas are scatter-pinned; always check `kubectl get pv` before scaling to 0.
- **gpu02 vision path.** gpu02 hosts `qwen3-vl:32b` and `phi4-reasoning`. Avoid scheduling non-AI workloads there.

## Roadmap Source of Truth

```bash
# View pending items by product
python3 -c "
import json
with open('roadmap.json') as f: d=json.load(f)
for pk,pv in d['products'].items():
    feats=pv.get('features',{})
    pending=feats.get('pending',[]) if isinstance(feats,dict) else []
    for item in pending:
        print(pk, item.get('id','?'), item.get('priority','?'), item.get('description','?')[:60])
"
```

GitHub Issues are the per-item work tracking. Roadmap items map 1:1 to GitHub Issues in the relevant repo.

## Common Tasks Reference

| Task | Tool / Pattern |
|------|---------------|
| Find function across STAX | `bash: grep -r "func_name" pulumi/` |
| Check pod health | `kubectl get pods -n <ns>` |
| Check recent deploy logs | `kubectl logs -n llm deploy/deer-flow-gateway --tail=50` |
| Run Pulumi tests locally | `cd pulumi && python -m pytest tests/ -x -q` |
| List open PRs | `gh pr list --repo Anomalous-Ventures/stax` |
| View roadmap pending | see Roadmap Source above |
| Validate DeerFlow | `ophanim-qa validate plans/deer-flow.yaml` (in ophanim repo) |
