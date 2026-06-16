# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Cronjob that gathers open pull request and issue statistics from the [openEuler](https://gitcode.com/openeuler) community and emails reports to SIG maintainers/committers.

Entry points:
- **`pr_statistics.py`** — PR statistics. Collects all open PRs, groups by reviewer, generates Excel/HTML reports and emails.
- **`issue_statistics.py`** — Issue statistics. Same flow for issues, with its own column layout (no branch column).

Reference project (not part of this script):
- **`hulk_robot_test/kernel_email_notification/`** — Dedicated Kernel SIG notification system using GitCode API + Jinja2 templates + SQLite. Serves as a reference for future modernization.

## Build & Run

### Local development

```bash
# Install dependencies (system-level packages + pip)
pip3 install requests openpyxl pandas PyYAML xlsx2html -i https://pypi.tuna.tsinghua.edu.cn/simple

# Run main PR statistics report
python3 pr_statistics.py

# Run member-change attention report
python3 members_change_attention.py
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

1. **`prepare_env()`** — Clones `gitcode.com/openeuler/community` to discover SIGs/repositories/maintainers. Creates a `data/` working directory.
2. **`get_sigs()`** — Walks `community/sig/<sig-name>/openeuler/*.yaml` and `src-openeuler/*.yaml` to build a mapping of SIG → repositories.
3. **`get_repos_pulls_mapping()`** — Paginates `https://ipb.osinfra.cn/pulls?state=open` to collect all open PRs. Returns `{repo/branch: pull_data}`.
4. **`get_maintainers()`** / **`get_committers_mapping()`** — Reads `OWNERS` or `sig-info.yaml` per SIG.
5. **`pr_statistics()`** — Core logic: iterates SIGs → repos → open PRs, annotates each PR with status (draft, CLA failure, CI failure, merge conflict, waiting for update), groups by reviewer Gitee ID. Reviewers are always the union of SIG maintainers + repo committers. Per-reviewer CSV → XLSX → HTML → email.
6. **Excel generation** — `csv_to_xlsx()` converts CSV, `excel_optimization()` applies color-coding (PR age severity, status flags), borders, grouped headers per SIG, and exports to HTML via `xlsx2html`.
7. **`compare_sig_processed_rate()`** — Calls `dsapi.osinfra.cn` to compute week-over-week PR processing rate for each SIG (displayed in report headers).
8. **`send_email()`** — Reads the generated HTML, wraps it in an email body, and sends via SMTP.

### Key config

- **`email_whitelist.yaml`** — List of gitee_ids allowed to receive emails. If the file is empty or missing, all emails are skipped (safety measure). Add gitee_ids to enable delivery.
- Maintainers are **always** included as reviewers, regardless of whether a repo has committers. Committers are added as additional reviewers when present.

### Common patterns

- Logging goes to both stdout and `statistics.log` (timed rotation, 3 backups).
- Working directory is `/work/pr-statistics` in Docker; scripts run from repo root.
- Temporary `data/` and `community/` directories are created at runtime; `community/` is cloned fresh each run.
- The `hulk_robot_test/kernel_email_notification/` directory contains a reference implementation for the Kernel SIG that uses GitCode API directly, Jinja2 templates, and SQLite storage.
