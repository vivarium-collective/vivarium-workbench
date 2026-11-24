# vivarium-workbench-vivaapi

The viva-api (SMS / viva-core) backend provider for
[vivarium-workbench](https://github.com/vivarium-collective/vivarium-workbench): remote runs, builds, listings,
analyses and figures against a viva-api deployment. The import package is `vivarium_workbench_sms`; the workbench
finds it through the `vivarium_workbench.backends` entry point (`sms`) and never imports it by name.

Install it through the workbench's extra:

```bash
pip install 'vivarium-workbench[vivaapi]'
```

It is released in **lockstep** with `vivarium-workbench`: identical versions, with `==` pins both ways (this package
requires `vivarium-workbench==X`, and the `[vivaapi]` extra requires `vivarium-workbench-vivaapi==X`). The source lives
in the workbench repository as a uv workspace member (`packages/vivarium-workbench-vivaapi/`); see
`docs/backend-adapters.md` there (§2.4, step P4b).
