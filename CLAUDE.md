# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Cronjob that gathers open pull request and issue statistics from a configured community ([openEuler](https://gitcode.com/openeuler) or [BoostKit](https://gitcode.com/boostkit)) and emails reports to SIG maintainers/committers. The active community is selected by the `COMMUNITY` env var (default `openeuler`); per-community settings live in **`communities.yaml`** (clone URL, orgs, data source, labels, mail texts). BoostKit additionally requires `GITCODE_TOKEN` for the GitCode API.

Entry points:
- **`pr_statistics.py`** — PR statistics. Collects all open PRs, groups by reviewer, generates Excel/HTML reports and emails.
- **`issue_statistics.py`** — Issue statistics. Same flow for issues, with its own column layout (no branch column).

Jenkins jobs: **`jenkins_job_openeuler.sh`** (openEuler) and **`jenkins_job_boostkit.sh`** (BoostKit, checks `GITCODE_TOKEN`).

## Build & Run

### Local development

```bash
# Install dependencies (system-level packages + pip)
pip3 install requests openpyxl pandas PyYAML xlsx2html -i https://pypi.tuna.tsinghua.edu.cn/simple

# Run PR statistics report (default community: openEuler)
python3 pr_statistics.py

# Run issue statistics for BoostKit (requires GITCODE_TOKEN)
COMMUNITY=boostkit python3 issue_statistics.py
```

### Docker

```bash
docker build -t pr-statistics .
docker run -e SMTP_USERNAME=... -e SMTP_PASSWORD=... -e SMTP_HOST=... -e SMTP_PORT=... -e SMTP_SENDER=... pr-statistics
```

## Required Environment Variables

| Variable | Purpose |
|---|---|
| `SMTP_HOST` | SMTP server address |
| `SMTP_PORT` | SMTP server port (465 for SSL, others use STARTTLS) |
| `SMTP_USERNAME` | SMTP auth username |
| `SMTP_PASSWORD` | SMTP auth password |
| `SMTP_SENDER` | Email `From` address |

## Architecture

### Data flow

1. **`setup_community()`** — Loads the active community config from `communities.yaml` (selected by `COMMUNITY`, default `openeuler`), creates and chdirs into a per-community working directory (`openeuler/` or `boostkit/`), and rebinds the logger to that directory's `statistics.log`.
2. **`prepare_env(config)`** — Clones the community repo from `config['community_repo']` to discover SIGs/repositories/maintainers. Creates a `data/` working directory.
3. **`get_sigs(config)`** — Builds the SIG → repositories mapping. With `repo_source: dir_walk` (openEuler) it walks `community/sig/<sig-name>/<org>/` for each org in `config['orgs']` (case-insensitive directory match, canonical org name from config); with `repo_source: sig_info` (BoostKit) it reads the `repositories` list from each SIG's `sig-info.yaml` because the sharded dir yamls lag behind it.
4. **`get_repos_pulls_mapping(config, sigs)`** — Two data sources depending on `config['data_source']`: `ipb` paginates `https://ipb.osinfra.cn/pulls?state=open`; `gitcode_api` calls the GitCode API per repo via `gitcode_open_items()` (403/404 repos are skipped with a warning). Returns `{repo/.../number: pull_data}`.
5. **`get_maintainers()`** / **`get_committers_mapping()`** — Reads `OWNERS` or `sig-info.yaml` per SIG.
6. **`pr_statistics(..., config)`** — Core logic: iterates SIGs → repos → open PRs, annotates each PR with status (draft, CLA failure, CI failure, merge conflict, waiting for update — labels come from the community config), groups by reviewer Gitee ID. Reviewers are always the union of SIG maintainers + repo committers. Per-reviewer CSV → XLSX → HTML → email.
7. **Excel generation** — `csv_to_xlsx()` converts CSV, `excel_optimization()` applies color-coding (PR age severity, status flags), borders, grouped headers per SIG, and exports to HTML via `xlsx2html`.
8. **`all_sigs_compare(sigs_list, config)`** — For communities with `processed_rate: dsapi`, calls `dsapi.osinfra.cn` via `compare_sig_processed_rate()` to compute week-over-week PR processing rate per SIG (displayed in report headers). Communities with `processed_rate: none` (BoostKit) skip this entirely and get empty compare info.
9. **`send_email()`** — Reads the generated HTML, wraps it in an email body (subject/body from the community config), and sends via SMTP.

### Key config

- **`communities.yaml`** — Per-community settings: community repo URL, orgs, data source (`ipb` aggregate API vs `gitcode_api` per-repo GitCode API), CLA/CI/wait-update labels (empty string disables that check), processed-rate mode (`dsapi` vs `none`), skipped SIGs, and mail subject/body texts. Loaded by `load_community_config()` relative to `common.py`'s path, cached per name.
- **`email_controls.yaml`** — Per-user email preferences (which mail types and role parts each gitee_id receives). Users not listed receive everything; see README for the syntax. Path overridable via `EMAIL_CONTROLS_PATH`.
- Maintainers are **always** included as reviewers, regardless of whether a repo has committers. Committers are added as additional reviewers when present.

### Common patterns

- Logging goes to both stdout and `statistics.log` (timed rotation, 3 backups), inside the per-community working directory.
- Working directory is `/work/pr-statistics` in Docker; scripts run from repo root.
- Temporary `data/` and `community/` directories are created at runtime inside the per-community working directory (`openeuler/` or `boostkit/`); `community/` is cloned fresh each run.
