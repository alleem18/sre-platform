# Postmortem: service-a Error Budget Breach (service-b Dependency Failure)

**Date:** 2026-07-06
**Author:** Aleem
**Severity:** SEV-3 (single dependency failure, synthetic/planned exercise)
**Status:** Resolved

---

## Summary

`service-b` was intentionally scaled to 0 replicas to manufacture a dependency
failure. `service-a`'s `/call-b` endpoint, which depends synchronously on
`service-b`, began returning `502` on every call. The sustained error rate
breached the platform's 1% error-budget SLO for `service-a`, and the
`ServiceAHighErrorRate` Prometheus alert correctly transitioned from
`Inactive` → `Pending` → `Firing`. The dependency was restored by scaling
`service-b` back to 2 replicas, and `service-a` recovered automatically once
the new `service-b` pods passed their readiness probes — no code deploy or
manual intervention beyond the scale command was required.

## Impact

- 100% of requests to `service-a`'s `/call-b` endpoint failed with `502`
  for the duration of the outage.
- `/` and `/healthz` on `service-a` were unaffected (no dependency on
  service-b), which is why the alert's reported error *ratio* (~0.58–0.59,
  see Root Cause below) was well under 100% even though the affected
  endpoint was fully down.
- No data loss. No effect on `service-b` clients other than `service-a`
  (n/a in this exercise — only consumer is `service-a`).

## Timeline

All times EDT, 2026-07-06.

| Time | Event | Source |
|---|---|---|
| 16:11:16 | `kubectl scale deployment service-b --replicas=0` executed — incident injected | Measured (`date` output) |
| ~16:11:20–16:11:30 | `service-a`'s `/call-b` error rate begins exceeding 1% SLO threshold (est.) | Estimated — synthetic traffic began immediately at injection, so the metric likely crossed threshold within seconds |
| ~16:13:20–16:13:30 | `ServiceAHighErrorRate` alert transitions Pending → Firing (est.) | Estimated from `for: 2m` rule duration + observed "Active Since" values on the Prometheus Alerts UI |
| 16:15:17 | Load-generation loop (300 requests) completes; `kubectl scale deployment service-b --replicas=2` executed — remediation applied | Measured (`date` output) |
| ~16:15:20–16:15:30 | First post-remediation `curl` still fails (`Connection refused`) — new service-b pod not yet passing readiness probe | Measured (curl output) |
| ~16:15:30–16:15:45 | Subsequent `curl` calls succeed — service-a/service-b chain fully recovered | Measured (curl output) |

**Key metrics (with honesty about precision):**
- **Time to breach threshold:** near-instant (seconds), since traffic was already flowing when the dependency was killed.
- **Time to detect (alert Pending → Firing):** ~2 minutes, which is expected and by design — the `for: 2m` window exists specifically to avoid firing on transient blips, so this number is essentially the floor set by the alert rule's own sensitivity tuning, not a detection failure.
- **Time from injection to remediation command:** ~4 minutes (16:11:16 → 16:15:17).
- **Time from remediation to confirmed recovery:** ~15–30 seconds (bounded by pod scheduling + the `initialDelaySeconds: 3` readiness probe warm-up).

## Root Cause

`service-a`'s `/call-b` route makes a synchronous HTTP call to `service-b`
with no fallback, retry, or circuit breaker. When `service-b` had zero
healthy endpoints, every call failed with a connection error, which the
route correctly translates into a `502` response — this is a **hard
dependency with no graceful degradation**, not a bug in the error-handling
logic itself (the try/except and 502 mapping worked exactly as designed).

One nuance worth documenting: the alert's `Value` in the Prometheus UI
showed ~0.58–0.59 rather than ~1.0, even though `/call-b` was 100% broken.
This is because the alert query (`sum(rate(...status=~"5.."[5m])) /
sum(rate(...[5m]))`) is scoped to *all* traffic in the `service-a` job —
which includes the continuous background `/healthz` traffic from
Kubernetes' own readiness/liveness probes, all returning `200`. That
background traffic dilutes the ratio. This is a real, useful thing to
understand about SLO metrics scoped at the service level: **a fully broken
single endpoint will not necessarily show as a 100% error rate if other
healthy endpoints on the same service are being hit by other traffic** —
which is exactly why per-route SLOs are sometimes preferable to
per-service ones in real production setups.

## Detection

Detected via the `ServiceAHighErrorRate` Prometheus alerting rule
(`for: 2m`, threshold `> 1%`), observed directly in the Prometheus `/alerts`
UI. **Important gap:** no Alertmanager receiver (Slack, email, PagerDuty,
etc.) was configured for this rule. Detection in this exercise depended
entirely on a human (me) having the Prometheus UI open and refreshing it
manually. In a real production environment with this exact setup, the
alert would have fired silently with no one notified — see Action Items.

## Resolution

Restored `service-b` to its normal replica count (`kubectl scale
deployment service-b --replicas=2`). No code changes, no rollback, no
manual pod intervention required — Kubernetes handled scheduling the new
pods and the existing readiness probe correctly gated traffic until they
were ready.

## What Went Well

- The SLO/alerting pipeline worked exactly as designed: metric →
  PromQL expression → `for` duration → state transition, all visible and
  correct in the Prometheus UI.
- The dependency failure was isolated cleanly to the one endpoint that
  actually depended on the failed service (`/call-b`); `/` and `/healthz`
  on service-a stayed healthy throughout, which is good evidence the
  blast radius was scoped correctly.
- Recovery was fast and required a single command — no manual pod
  surgery, no stuck state, no need to restart service-a.
- The existing readiness probe on service-b prevented `service-a` from
  being routed to not-yet-ready service-b pods immediately after scale-up,
  which is exactly what caused the first post-fix curl to (correctly)
  still fail — a sign the probe is doing its job, not a bug.

## What Went Wrong / Gaps

1. **No Alertmanager routing was configured.** The alert fired correctly
   but notified no one. This is the single biggest gap in this exercise —
   a production incident detected only by an engineer happening to have a
   browser tab open is not real detection.
2. **The remediation timing in this exercise reflects the drill's design,
   not real incident response speed.** The fix was applied the moment the
   300-iteration load loop finished (16:15:17), not the moment the alert
   was observed firing (~16:13:25). In a real incident, an on-call
   engineer notified immediately at Firing could plausibly have remediated
   within seconds of that page — meaning the true achievable MTTR here is
   closer to **under a minute**, not the ~4 minutes this specific run
   shows. Worth being precise about this distinction rather than quoting
   the raw ~4-minute number as if it reflects real responder speed.
3. **`service-a` has no resilience pattern (retry, timeout tuning, circuit
   breaker, fallback response) for its dependency on `service-b`.** A
   single dependency failure fully takes out one endpoint with no
   degraded-but-available behavior.
4. **The SLO is service-scoped, not route-scoped**, which — as noted in
   Root Cause — can mask a fully-broken single endpoint behind a diluted
   aggregate ratio if other endpoints on the same service keep serving
   healthy traffic.

## Action Items

| Action | Owner | Priority |
|---|---|---|
| Configure an Alertmanager receiver (Slack webhook is simplest) so `ServiceAHighErrorRate` actually notifies someone | Aleem | High |
| Add a route-level SLO/alert specifically for `/call-b`, in addition to the service-wide one, so a single broken endpoint isn't diluted by healthy traffic elsewhere | Aleem | Medium |
| Add a timeout + simple retry (e.g. 1 retry with backoff) on the `service-a → service-b` HTTP call | Aleem | Medium |
| Consider a fallback/degraded response from `/call-b` when `service-b` is unreachable, instead of a bare 502 | Aleem | Low |
| Re-run this same drill once Alertmanager routing exists, and measure true detection-to-notification latency end-to-end | Aleem | Low |
