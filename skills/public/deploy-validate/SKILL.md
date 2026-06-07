---
name: deploy-validate
description: Validate a deployment end-to-end using Pulumi preview/up and Ophanim test plans. Covers infrastructure diff review, deployment execution, and post-deploy QA. Load stax-context first.
---

# Deploy Validate Skill

Full deployment validation workflow for STAX platform services.

## Inputs

- **stack**: Pulumi stack directory (e.g., `pulumi/stacks/27-deer-flow`)
- **ophanim_plan**: Ophanim plan name (e.g., `deer-flow`)
- **skip_deploy**: If true, only validate current state without running `pulumi up`

## Workflow

### Phase 1: Pre-Deploy Review

1. Run `pulumi preview --diff` in the stack directory:
   ```bash
   cd <stack_dir> && pulumi preview --diff 2>&1
   ```
2. Parse and classify planned changes:
   - **create**: New resources (generally safe)
   - **update**: In-place modifications (verify idempotent)
   - **replace**: Destroy + recreate (STATEFUL RISK -- see below)
   - **delete**: Removal (requires explicit user approval)
3. Flag critical changes:
   - DELETE on any resource: STOP and request explicit confirmation
   - REPLACE on PVCs, databases, StatefulSets: STOP and confirm data persistence
   - Changes to NetworkPolicy, RBAC, ServiceAccounts: note for security review
4. Check image tag changes: expected format is `<service>:<git-sha>` or `<service>:latest@sha256:<digest>`
5. Report preview summary before proceeding

### Phase 2: Deploy (skip if skip_deploy=true)

1. Run deploy:
   ```bash
   cd <stack_dir> && pulumi up --yes --skip-preview 2>&1
   ```
2. For each Deployment/DaemonSet/StatefulSet modified:
   ```bash
   kubectl rollout status deployment/<name> -n <namespace> --timeout=300s
   ```
3. On rollout failure:
   - `kubectl get events -n <namespace> --sort-by=.lastTimestamp | tail -30`
   - `kubectl describe pod -l app=<name> -n <namespace>`
   - `kubectl logs -l app=<name> -n <namespace> --tail=50 --previous` (if crash loop)
4. On image pull timeout: check if this is first deploy with new image (normal -- wait up to 5m)

### Phase 3: Post-Deploy Validation

**Health checks**

For each service with a health endpoint:
```bash
curl -sf http://<service>.<namespace>.svc.cluster.local:<port>/health \
  || curl -sf http://<service>.<namespace>.svc.cluster.local:<port>/healthz
```

Report: HTTP status, response time, body snippet.

**Ophanim validation**

```bash
ophanim-qa validate plans/<ophanim_plan>.yaml 2>&1
```

Parse results:
- Extract per-checkpoint pass/fail
- For failures: capture screenshot if visual checkpoint, error details if functional
- Classify: hard-fail (breaks core user flow) vs soft-fail (degraded experience)

**NetworkPolicy verification**

For any NetworkPolicy change:
```bash
kubectl exec -n <namespace> <test-pod> -- curl -v http://<target>:<port> 2>&1
```
Verify expected traffic passes, blocked traffic is blocked.

**Endpoint population**

```bash
kubectl get endpoints <service> -n <namespace>
```
Verify non-empty (pods registered as healthy).

### Phase 4: Report

Format:
```
## Deployment Validation: <service>
**Stack**: <path>  **Timestamp**: <ISO-8601>
**Status**: PASS | FAIL | PARTIAL

### Changes Applied
- Creates: N
- Updates: N
- Replaces: N (list replaced resources)
- Deletes: N

### Health
| Service | Endpoint | Status | Latency |
|---------|----------|--------|---------|
| <svc>   | <url>    | 200 OK | 42ms    |

### Ophanim Results
| Checkpoint | Result | Notes |
|------------|--------|-------|
| <name>     | PASS   |       |
| <name>     | FAIL   | <err> |

### Failures
[detailed failure descriptions with kubectl output]
```

## Rollback Criteria

Suggest rollback ONLY when ALL of:
- Hard-fail Ophanim checkpoint
- Health endpoint returning 5xx
- Previous image tag is known-good

**Never auto-rollback.** Present evidence and recommendation; let user decide.

Rollback command (if suggested):
```bash
cd <stack_dir> && pulumi up --yes --target '<resource_urn>' 2>&1
```

## Common Failure Patterns

| Symptom | Likely Cause | Diagnostic |
|---------|-------------|------------|
| Pod CrashLoopBackOff | Config/secret missing | `kubectl logs --previous` |
| ImagePullBackOff | Registry auth or image tag wrong | `kubectl describe pod` |
| 503 from ingress | Health check failing | Check readinessProbe |
| Ophanim timeout | Service not reachable | Check NetworkPolicy + Service selector |
| PVC Pending | StorageClass missing or node affinity | `kubectl describe pvc` |

## Stax-Specific Notes

- Secrets from Vault via ExternalSecrets -- if pod fails to start, check ESO sync status
- Harbor registry -- verify robot token not expired (`kubectl get secret harbor-creds -n <ns>`)
- Pulumi backend is MinIO S3 -- never use local backend (`file://`)
- Stack passphrase exported as `PULUMI_CONFIG_PASSPHRASE` before running (see parallel-deploy.sh)
