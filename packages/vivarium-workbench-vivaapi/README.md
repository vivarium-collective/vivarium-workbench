# vivarium-workbench-vivaapi

The viva-api (SMS / viva-core) backend provider for
[vivarium-workbench](https://github.com/vivarium-collective/vivarium-workbench): remote runs, builds, listings,
analyses and figures against a viva-api deployment. The import package is `vivarium_workbench_sms`; the workbench
finds it through the `vivarium_workbench.backends` entry point (`sms`) and never imports it by name.

It is **not published to PyPI** (Jim, 2026-11-24): only the core, `vivarium-workbench`, is. Install it from git; with
uv, the workbench's extra pulls it from the same commit:

```bash
uv add 'vivarium-workbench[vivaapi] @ git+https://github.com/vivarium-collective/vivarium-workbench.git'
```

With pip, name this subdirectory as well:

```bash
pip install 'vivarium-workbench[vivaapi] @ git+https://github.com/vivarium-collective/vivarium-workbench.git' \
  'vivarium-workbench-vivaapi @ git+https://github.com/vivarium-collective/vivarium-workbench.git#subdirectory=packages/vivarium-workbench-vivaapi'
```

`pip install 'vivarium-workbench[vivaapi]'` from PyPI does not resolve. The Stanford image installs this package from
the same commit as the core.

It has the **same version** as `vivarium-workbench`, with `==` pins both ways (this package requires
`vivarium-workbench==X`, and the `[vivaapi]` extra requires `vivarium-workbench-vivaapi==X`); both are built and checked
on every release tag, and only the core is uploaded. The source lives
in the workbench repository as a uv workspace member (`packages/vivarium-workbench-vivaapi/`); see
`docs/backend-adapters.md` there (§2.4, step P4b).
