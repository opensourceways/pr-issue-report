#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
QA statistics report — community wide summary of all open PRs and issues.

Same data collection as the weekly per-reviewer reports, but nothing is filtered out: every
open PR / issue of every SIG is listed, grouped by SIG, and the result is sent to a fixed
receiver list (the QA SIG maintainers) instead of being grouped by reviewer. Two emails are
sent per run: one PR report and one Issue report.

Receivers and mail texts come from the community's qa_report config (see communities.yaml).
"""

import os
from common import *
from docs_statistics import (collect_issue_rows, collect_pr_rows, resolve_recipients,
                             send_community_report)
from issue_statistics import get_repos_issues_mapping
from pr_statistics import get_repos_pulls_mapping


def dedupe_rows(rows, link_col):
    """
    Drop duplicate rows caused by repos registered under more than one SIG
    (e.g. CubeSandbox / MicroservicesBench in BoostKit). The first occurrence wins.
    :param rows: report rows
    :param link_col: index of the cell holding the item link, unique per PR/issue
    :return: list of rows without duplicates
    """
    seen = set()
    unique = []
    for row in rows:
        key = row[link_col]
        if key in seen:
            log.logger.info('Skip duplicated row: {}'.format(key))
            continue
        seen.add(key)
        unique.append(row)
    return unique


# ---------------------------------------------------------------------------
# QA statistics
# ---------------------------------------------------------------------------

def qa_statistics(data_dir, sigs, repos_pulls_mapping, repos_issues_mapping, compare_dict, config=None):
    """
    Build and send the community wide reports (one PR mail, one Issue mail) to the QA receivers.
    :param data_dir: directory to store temporary data
    :param sigs: a dict of every sig and its repositories
    :param repos_pulls_mapping: mappings between repos and pulls
    :param repos_issues_mapping: mappings between repos and issues
    :param compare_dict: a dict of every sig and its compare info
    :param config: community config dict (defaults to the active community)
    """
    config = config or load_community_config()
    qa_config = config.get('qa_report') or {}
    log.logger.info('=' * 25 + ' QA STATISTICS ' + '=' * 25)
    test_email = os.getenv('test_reviever_email', '').strip()
    test_mode = bool(test_email)
    redirect_email = test_email
    dry_run = os.getenv('DRY_RUN', '').strip().lower() == 'true'
    test_user = os.getenv('TEST_USER', '').strip()
    if test_mode:
        log.logger.info('[TEST MODE] QA report emails will be sent to {}'.format(redirect_email))
    if dry_run:
        log.logger.info('[DRY RUN] QA report emails will be generated locally in test_output/, not sent')
    if test_user:
        log.logger.info('[TEST USER] Only processing receiver: {}'.format(test_user))
    controls = load_email_controls()
    recipients = resolve_recipients(config, 'qa_report')
    pr_rows = dedupe_rows(collect_pr_rows(sigs, repos_pulls_mapping, config), 3)
    issue_rows = dedupe_rows(collect_issue_rows(sigs, repos_issues_mapping, config), 2)
    log.logger.info('Found {} PRs and {} issues in the community'.format(len(pr_rows), len(issue_rows)))

    send_community_report(data_dir, 'qa_statistics_qa_pr', 'qa_pr', pr_rows, False,
                          qa_config.get('subject_pr'), qa_config.get('body_pr'),
                          '全社区 PR（按 SIG 分组）', qa_config, config, recipients,
                          controls, compare_dict)
    send_community_report(data_dir, 'qa_statistics_qa_issue', 'qa_issue', issue_rows, True,
                          qa_config.get('subject_issue'), qa_config.get('body_issue'),
                          '全社区 Issue（按 SIG 分组）', qa_config, config, recipients,
                          controls, compare_dict)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    """
    main function for QA statistics
    """
    config = load_community_config()
    qa_config = config.get('qa_report') or {}
    if not qa_config.get('enabled'):
        log.logger.info('qa_report is not enabled for community {}, nothing to do'.format(config['name']))
        return
    config = setup_community(config, workdir=qa_config.get('workdir'))
    data_dir = prepare_env(config)
    sigs, sigs_list = get_sigs(config)
    compare_dict = all_sigs_compare(sigs_list, config)
    repos_pulls_mapping = get_repos_pulls_mapping(config, sigs)
    repos_issues_mapping = get_repos_issues_mapping(config, sigs)
    qa_statistics(data_dir, sigs, repos_pulls_mapping, repos_issues_mapping, compare_dict, config)


if __name__ == '__main__':
    main()
