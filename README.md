# sre-platform

A production-shaped SRE platform on Kubernetes: GitOps-driven deploys, full observability, autoscaling, and a reproducible incident-response loop. This repo is the **local development environment** — a `kind` cluster used to build and validate real SRE practices. Production deployment to AWS EKS is a separate, dedicated project (see [Production target](#production-target)).

Two Python microservices with a real inter-service dependency, deployed to Kubernetes and reconciled entirely from Git by Argo CD. Nothing in the cluster is applied by hand after bootstrap; drift self-heals; deploys flow through a pull-based pipeline.

```
Developer push ──► GitHub Actions ──► GHCR (image)
                        │
                        └──► PR bumping image tag in k8s/  ──► merge to main
                                                                      │
                                          ┌───────────────────────────┘
                                          ▼
                                    Argo CD (in-cluster) — pulls & reconciles
                                          │
                    ┌─────────────────────┼─────────────────────┐
                    ▼                     ▼                     ▼
              service-a ──HTTP──►   service-b            kube-prometheus-stack
              (ingress /a)         (ingress /b)          (Prometheus/Grafana/
                    │                                     Alertmanager)
                    └── HPA (CPU-based autoscaling) ──────┘
```

**CI never touches the cluster.** GitHub Actions only builds images and opens a PR; Argo CD, running inside the cluster, pulls the change in. Cluster credentials never leave the cluster — the pull-based security boundary that is the whole point of GitOps.

---

## Two ways to run this

- **[Quickstart](#quickstart-5-minutes-see-it-run)** — clone, spin up a cluster, deploy the services against the author's public images, curl them. ~5 minutes, no fork, no registry setup. Proves the *services* run.
- **[Full GitOps setup](#full-setup-the-gitops-pipeline)** — fork, repoint to yourself, and run the whole Argo CD + CI/CD pipeline. Proves the *GitOps mechanism*, which is the actual subject of this project.

The quickstart deliberately bypasses Argo CD to get you to a running service fast. The full setup is where the GitOps story lives.

---

## Prerequisites

- **Docker** (running)
- **kind** — `brew install kind` or `go install sigs.k8s.io/kind@latest`
- **kubectl**
- **git**

---

## Quickstart (5 minutes, see it run)

The author's service images are public on GHCR, so you can run the workloads without forking or building anything.

```bash
git clone https://github.com/alleem18/sre-platform.git
cd sre-platform

# 1. Cluster (named sre-platform via kind-config.yaml)
kind create cluster --config kind-config.yaml

# 2. Ingress controller (pinned; see version note below)
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.15.1/deploy/static/provider/kind/deploy.yaml
kubectl wait --namespace ingress-nginx \
  --for=condition=ready pod \
  --selector=app.kubernetes.io/component=controller --timeout=120s

# 3. Deploy just the app resources (no Argo CD, no monitoring CRDs)
kubectl apply -f k8s/service-a-deployment.yaml \
              -f k8s/service-a-service.yaml \
              -f k8s/service-a-hpa.yaml \
              -f k8s/service-b-deployment.yaml \
              -f k8s/service-b-service.yaml \
              -f k8s/ingress.yaml

# 4. Verify
kubectl get pods
curl localhost/a/          # {"service":"service-a","status":"healthy"}
curl localhost/b/          # service-b response
curl localhost/a/good      # {"status":"yes"}
```

> The quickstart intentionally skips `service-a-alerts.yaml` and `servicemonitors.yaml` — those are `PrometheusRule`/`ServiceMonitor` resources that require the monitoring CRDs, installed in the full setup. Applying them here would error with `no matches for kind`.

Tear it down when done: [Teardown](#teardown).

---

## Full setup (the GitOps pipeline)

This runs the real thing: Argo CD app-of-apps reconciling the whole platform from Git, plus the CI/CD pipeline. Because Argo CD pulls from a Git URL and pods pull from a registry, **you must fork and repoint to yourself first.**

### Step 0 — Fork and repoint

Fork the repo on GitHub, clone your fork, then:

```bash
# Repoint Argo CD's Git URL to your fork (macOS sed shown; on Linux drop the '')
grep -rl 'github.com/alleem18/sre-platform' argocd/ \
  | xargs sed -i '' 's|github.com/alleem18/sre-platform|github.com/YOUR_GH_USERNAME/sre-platform|g'

# Repoint container images to your GHCR namespace
grep -rl 'ghcr.io/alleem18' k8s/ .github/ \
  | xargs sed -i '' 's|ghcr.io/alleem18|ghcr.io/YOUR_GH_USERNAME|g'

# Sanity check — nothing should remain:
grep -rn 'alleem18' . --exclude-dir=.git
```

Build and publish your own images (the cluster can't pull images that don't exist under your namespace):

```bash
echo $GH_TOKEN | docker login ghcr.io -u YOUR_GH_USERNAME --password-stdin
for svc in a b; do
  docker build -t ghcr.io/YOUR_GH_USERNAME/sre-platform-service-$svc:latest ./service-$svc
  docker push  ghcr.io/YOUR_GH_USERNAME/sre-platform-service-$svc:latest
done
```

**Make both GHCR packages public:** GitHub → Profile → Packages → each package → Package settings → Change visibility → Public. Private images fail with `ImagePullBackOff` on `kind`, which has no pull credentials.

**(For the CI pipeline)** In your fork: Settings → Actions → General → Workflow permissions → **Read and write permissions** + check **Allow GitHub Actions to create and approve pull requests**. Settings → General → enable **Automatically delete head branches**.

Commit the repointing:

```bash
git commit -am "chore: repoint repoURL and image namespace to fork"
git push
```

### Step 1 — Cluster

```bash
kind create cluster --config kind-config.yaml
kind get clusters                    # sre-platform
kubectl config current-context       # kind-sre-platform
```

### Step 2 — Bootstrap components (not managed by Argo CD)

These three are cluster-level and installed once by hand. Everything else is owned by Argo CD. Versions are pinned to what this project was validated against.

```bash
# metrics-server (HPA depends on it) — patched for kind's self-signed kubelet TLS
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/download/v0.7.2/components.yaml
kubectl -n kube-system patch deployment metrics-server --type=json \
  -p='[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"}]'

# ingress-nginx (kind provider) — pinned; see version note below
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.15.1/deploy/static/provider/kind/deploy.yaml
kubectl wait --namespace ingress-nginx \
  --for=condition=ready pod \
  --selector=app.kubernetes.io/component=controller --timeout=120s

# Argo CD
kubectl create namespace argocd
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/v2.13.2/manifests/install.yaml
kubectl -n argocd rollout status deploy/argocd-server
```

### Step 3 — Hand the platform to Argo CD

```bash
kubectl apply -f argocd/root-app.yaml
kubectl get applications -n argocd -w
```

`root-app`, `monitoring`, and `workloads` should appear and move toward `Synced`/`Healthy`.

### Step 4 — Expected: the monitoring stack needs one manual nudge

The kube-prometheus-stack CRDs are very large. Their sync uses `ServerSideApply=true` + `Replace=true` (already set in `argocd/apps/monitoring.yaml`) to clear Kubernetes' 256 KB annotation limit — without those, the six biggest CRDs fail with `metadata.annotations: Too long`.

Because the CRDs are created underneath an already-running Prometheus operator, the operator's watches go stale and it won't build the Prometheus/Alertmanager StatefulSets until restarted **once**:

```bash
kubectl get crd | grep monitoring.coreos.com          # wait for all 10
kubectl -n monitoring rollout restart deploy/kube-prometheus-stack-operator
watch "kubectl get statefulset,pod -n monitoring"     # StatefulSets appear ~30s later
```

Sync-wave annotations make CRD-before-workload ordering deterministic; the operator-restart is a known kube-prometheus-stack quirk on a from-scratch install.

### Step 5 — Verify and access the UIs

```bash
kubectl get applications -n argocd     # all Synced / Healthy
curl localhost/a/  ;  curl localhost/b/  ;  curl localhost/a/good

# Argo CD UI
kubectl port-forward svc/argocd-server -n argocd 8081:443
kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath="{.data.password}" | base64 -d; echo

# Grafana
kubectl port-forward -n monitoring svc/kube-prometheus-stack-grafana 3000:80
kubectl get secret -n monitoring kube-prometheus-stack-grafana -o jsonpath="{.data.admin-password}" | base64 -d; echo
```

(If the host is remote, SSH-tunnel the forwarded port from your workstation.)

---

## Validating the platform

```bash
# Autoscaling — replicas climb under load, settle back after
kubectl run load --image=busybox --restart=Never -- \
  /bin/sh -c "while true; do wget -q -O- http://service-a:80/ >/dev/null; done"
kubectl get hpa -w
kubectl delete pod load

# Incident loop — sever the dependency, watch the SLO alert fire, remediate
kubectl scale deployment service-b --replicas=0
for i in $(seq 1 200); do curl -s localhost/a/call-b >/dev/null; done
# ServiceAHighErrorRate: inactive → pending (for: window) → firing
kubectl scale deployment service-b --replicas=2

# CI/CD round-trip (full setup only) — code change reaches the cluster, no manual kubectl
echo "# trigger $(date +%s)" >> service-a/app.py
git commit -am "test: trigger CI/CD loop"
git push
# → Actions builds & opens PR → merge → Argo CD reconciles → new rollout
```

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `curl localhost/a/` → `Empty reply from server` | ingress-nginx not installed | Run the ingress-nginx step |
| Pods `ImagePullBackOff` | GHCR image private, or namespace not repointed | Make packages public; repoint `ghcr.io/...` |
| `monitoring`: `metadata.annotations: Too long` | CRD sync without server-side apply | Confirm `ServerSideApply=true` + `Replace=true` in `argocd/apps/monitoring.yaml` |
| CRDs exist but no `prometheus-*` / `alertmanager-*` pods | Operator watches stale after CRD create | `kubectl -n monitoring rollout restart deploy/kube-prometheus-stack-operator` |
| HPA shows `<unknown>/50%` | metrics-server missing/unpatched | Reinstall + patch metrics-server |
| Argo apps `OutOfSync`, can't reach repo | `repoURL` still points at original author | Repoint `repoURL` in `argocd/` to your fork |
| CI runs but no PR | Actions can't open PRs | Enable read/write + "create and approve pull requests" |
| CI doesn't run on push | Path filter only fires on `service-a/**` / `service-b/**` | Edit a file under those paths |
| `CrashLoopBackOff` after a code change | App fails to start (e.g. Python syntax error) | Check `kubectl logs`; the CI compile gate catches most of these before deploy |

---

## Teardown

`kind` is fully self-contained — deleting the cluster removes everything (pods, Argo CD, monitoring, ingress, all state). Nothing persists on the host outside Docker.

```bash
kind delete cluster --name sre-platform
kind get clusters        # confirm it's gone
```

GHCR images and any open CI PRs live on GitHub, not in the cluster — clean those up in the GitHub UI if desired.

---

## Production target

This repo is intentionally scoped to local development on `kind` — a fast, free, disposable dev loop. It is **not** production and doesn't pretend to be.

Production deployment to **AWS EKS** — Terraform-provisioned VPC, cluster, node groups, and IAM, with explicit teardown discipline for cost control — is handled as a separate project. Keeping dev environment and production target as distinct concerns mirrors how real platform teams structure things.

---

## Future changes / roadmap

In rough priority order:

- **CI test/lint gate** — a `python -m py_compile` (and eventually real tests) step so a broken service fails the build instead of shipping a `CrashLoopBackOff`.
- **Bootstrap-as-code** — fold ingress-nginx and metrics-server into Argo CD Applications so a rebuild is a single `kubectl apply -f argocd/root-app.yaml` with no manual pre-steps.
- **Config-repo split** — move manifests to a separate config repo so CI never writes image bumps into the repo developers push to (the canonical production pattern).
- **Multi-environment promotion** — staging/prod via Argo CD ApplicationSet; staging auto-deploys, production gated behind a reviewed PR.
- **Argo CD Image Updater** — registry-driven tag promotion (needs a sortable tag scheme, e.g. `main-<timestamp>-<sha>`), removing the manual PR merge for lower environments.
- **Alertmanager routing** — wire firing alerts to Slack/PagerDuty; they currently fire and are visible but aren't routed out.
- **Secrets in Git** — SOPS/age encrypted secrets, decrypted in-cluster by Argo CD.
- **Ingress successor** — ingress-nginx is end-of-life (maintenance ended March 2026); migrate to a Gateway API implementation.
- **EKS deploy target** — the local platform becomes a second deploy target on real cloud infrastructure (tracked as its own project).

---

## A note on versions

Pinned versions are what this project was validated against:

- **ingress-nginx** `controller-v1.15.1` — note this project is end-of-life (best-effort maintenance ended March 2026; repo archived). It still works and images remain available; Gateway API is the forward path (see roadmap).
- **metrics-server** `v0.7.2`
- **Argo CD** `v2.13.2`

Pinning rather than tracking `latest`/`main`/`stable` is deliberate: it means a clone six months from now gets the same versions that were tested, instead of whatever happens to be current.
