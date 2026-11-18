# Agent cards for HW7 card-variant experiments (AGE-62, AGE-63)

`original/` holds the 7 public agent cards exactly as frozen at
`agent-hunter-providers@5d9536a` (PREREG §12), copied with
`git show 5d9536a:providers/<slug>/card.json`. Do not edit them.

These are the only provider files the AGE-62 / AGE-63 work needs. Per PREREG §13 and AGE-63's
fairness rules, that work must not read `agent-hunter-providers/` (it holds the private traits).
Card variants go next to `original/` (e.g. `inflated/`, `price_shuffled/`) and are generated
mechanically by a committed script.
