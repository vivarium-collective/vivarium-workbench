## Next: Manual browser confirmation (last remaining check)

The k8s subpath deployment fix is **resolved and E2E-verified by curl** — see
`SAVE_SLOT.md` for the full root-cause and resolution writeup. The last item is a
human-eyes check that curl can't perform:

```bash
AWS_PROFILE=stanford-sso AWS_DEFAULT_REGION=us-gov-west-1 \
  ~/sms/sms-cdk/scripts/sms-proxy.sh -s smsvpctest
# → http://localhost:8080/workbench
```

Confirm in a real browser:
- CSS renders correctly (no unstyled page)
- Investigations tab → DAG renders → click a study → iframe loads with
  **functional, clickable sub-tabs** (this was the original bug —
  `demos/v2ecoli/NOTES.md:232-233`)
- Simulations DB table renders

If any of those still fail, it's a frontend/JS issue distinct from the routing
bug just fixed — capture the browser console error and treat as a new issue.

---

## What was actually wrong (short version)

Not a code bug, not a security group, not a slow app. A prior session deployed
the workbench ALB target group to the **wrong CDK environment**
(`DEPLOY_ENV=stanford` instead of `DEPLOY_ENV=stanford-vpc-test`), creating a
duplicate target group in a VPC with no route to the EKS pods. It then
misdiagnosed the *pre-existing correct* target group as stale and repointed
`sms-api`'s `TargetGroupBinding` at the new, broken one. Fix was: revert the
`sms-api` edit (never committed), recreate the `TargetGroupBinding`, and revert
the stray AWS resources. No vivarium-workbench code changes were ever needed —
that part of the original diagnosis was correct.

**See also:** `SAVE_SLOT.md` (full resolution writeup), `todo.md` (updated plan)
