## Summary

<!-- What does this PR change, and why? -->

## Related issue

<!-- e.g. Closes #123 -->

## Checklist

- [ ] `python -m pytest tests/ -q` passes locally
- [ ] No new third-party runtime dependency (the package stays **stdlib-only**)
- [ ] Host bindings / examples use `127.0.0.1`, never `localhost`
- [ ] Tests stay hermetic (no real network / LLM / GPU) and cover the change
- [ ] Docs / CHANGELOG updated if behavior or public surface changed

## Notes for reviewers

<!-- Anything that needs a closer look, or context that isn't obvious from the diff. -->
