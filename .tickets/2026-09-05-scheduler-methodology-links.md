# Render scheduler restoration and artifact links correctly

Status: candidate documentation repair; consolidated acceptance pending.

The strict MkDocs build at replica documentation candidate `1741a3a6` succeeded,
but its rendered methodology still contained literal `[tier transition]`:
the destination had been separated from the label by a newline. The ordinary
relative-link checker could not validate a link that Markdown never recognized.
The nearby campaign-artifact link also led to the findings index rather than
the repeatable-campaign workflow explaining its artifact contract.

Keep the transition label and destination contiguous and link the artifact set
to `docs/benchmarks/repeatable-campaigns.md`. In addition to strict build and
relative-link checks, assert the two intended destinations and absence of the
literal unrendered label in the generated HTML. Qualification thresholds,
evidence rules, restoration policy and human promotion authority are unchanged.
No benchmark, route change or live deployment occurred.
