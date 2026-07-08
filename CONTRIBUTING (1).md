# Contributing

This started as an internal tool for a semi-detailed soil survey project, so contributions that keep it broadly useful across different survey areas are especially welcome:

- **Bug reports** — please include the QGIS version, whether `numpy`/`scikit-learn` were installed, and the console output/traceback.
- **Adapting to another survey area** — if you extend this to a new region, consider submitting your series lookup table as a separate config file rather than overwriting the default one, so both remain usable as examples.
- **Pull requests** — keep the step-by-step console print structure (`[n/10] ...`) intact; it's relied on for tracing pipeline progress during long runs.

Before submitting a PR, please test against a small sample dataset and include the printed summary report in your PR description so reviewers can sanity-check the assignment breakdown.
