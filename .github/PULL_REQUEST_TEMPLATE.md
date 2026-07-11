## Summary

<!-- What does this PR change, and why? -->

## Related issue

<!-- e.g. Closes #123 -->

## Anvil delivery

- PRD ID(s): <!-- e.g. operator-cli-v2 -->
- Task ID(s): <!-- exact namespaced task IDs -->
- Delivery manifest: <!-- path, or N/A with reason -->
- Delivery gate: <!-- exact command and pass/fail status -->
- Implementer: <!-- identity -->
- Independent task reviewer: <!-- must differ from implementer -->
- Evidence status: <!-- complete / incomplete, with blockers -->
- Human apply disposition: <!-- approved / rejected / pending -->
- Documentation review: <!-- passed / failed / pending; reviewer + summary -->
- Adversarial review: <!-- passed / failed / pending; reviewer + summary -->
- Human merge gate: <!-- approved / rejected / pending -->

## Checklist

- [ ] `python -m pytest tests/ -q` passes locally
- [ ] No new third-party runtime dependency (the package stays **stdlib-only**)
- [ ] Host bindings / examples use `127.0.0.1`, never `localhost`
- [ ] Tests stay hermetic (no real network / LLM / GPU) and cover the change
- [ ] Docs / CHANGELOG updated if behavior or public surface changed
- [ ] Required Anvil tasks are `done` with observed proofs and explicit human dispositions
- [ ] Code author and task reviewer are different people/agents
- [ ] Documentation and adversarial final reviews have explicit dispositions

## Notes for reviewers

<!-- Anything that needs a closer look, or context that isn't obvious from the diff. -->
