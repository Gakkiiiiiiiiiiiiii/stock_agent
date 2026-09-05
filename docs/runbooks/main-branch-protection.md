# Main branch protection

Run `python scripts/check_branch_protection.py` for a report of the required
settings. The script intentionally performs no GitHub mutation. Configure
required checks (`lint`, `unit-tests`, `architecture`, `contract`, `decision-replay`,
`integration`, `docker-build`, `k8s-smoke`), one
CODEOWNERS review, stale-review dismissal, no force-push/deletion, and audited
admin enforcement through repository administration.
