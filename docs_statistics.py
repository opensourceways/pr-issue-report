#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Doc statistics report — community-wide summary of documentation-related PRs and issues.

Same data collection as the weekly per-reviewer reports, but the results are filtered down to
documentation-related items and sent to a fixed receiver list instead of being grouped by
maintainer/committer. Two emails are sent per run: one PR report and one Issue report.

Doc criteria come from the community's docs_report config (see communities.yaml):
  - PR: carries any label listed in pr_labels (need-doc-sig-review / doc-sig-reviewed / ...).
        docs-ci-pipeline-* labels must NOT be used: they are per-repo CI status labels, so every
        PR of a repo with a docs pipeline carries them.
  - Issue: title starts with issue_title_prefix, or its issue_type equals the configured type.
"""

import codecs
import csv
import os
from common import *
from issue_statistics import get_repos_issues_mapping
from pr_statistics import get_repos_pulls_mapping


# ---------------------------------------------------------------------------
# Doc filtering
# ---------------------------------------------------------------------------

def is_doc_pr(item, pr_labels):
    """
    Check whether a pull request is documentation-related by its labels.
    :param item: adapted pull item (labels is a comma separated string)
    :param pr_labels: documentation label names from the community config
    :return: bool
    """
    if not pr_labels:
        return False
    labels = [x for x in (item.get('labels') or '').split(',') if x]
    return bool(set(labels) & set(pr_labels))


def is_doc_issue(item, title_prefix, issue_type):
    """
    Check whether an issue is documentation-related: its title starts with the configured
    prefix, or its issue_type matches. Both signals are used because some issues only carry
    the type and others only the title prefix.
    :param item: adapted issue item
    :param title_prefix: e.g. '[资料]:'
    :param issue_type: e.g. '资料'
    :return: bool
    """
    title = (item.get('title') or '').lstrip()
    if title_prefix and title.startswith(title_prefix):
        return True
    return bool(issue_type) and item.get('issue_type') == issue_type


# ---------------------------------------------------------------------------
# Row building — the column layout and the status wording mirror the weekly reports
# ---------------------------------------------------------------------------

def doc_status(item, docs_config):
    """
    Return the documentation label state of a pull request as (text, color).
    Labels are matched in the order declared in docs_report.status_labels so that the first
    (most final) matching label wins, e.g. a reviewed PR that somehow still carries the
    pending label is reported as reviewed.
    :param item: adapted pull item
    :param docs_config: the community's docs_report config block
    :return: (text, color); empty text when the PR carries none of the configured labels
    """
    labels = [x for x in (item.get('labels') or '').split(',') if x]
    for label, status in (docs_config.get('status_labels') or {}).items():
        if label in labels:
            return status.get('text') or label, status.get('color')
    return '', None


def doc_status_fills(docs_config):
    """
    Return {status text: fill color} for the docs label state column.
    :param docs_config: the community's docs_report config block
    """
    return {status.get('text') or label: status.get('color')
            for label, status in (docs_config.get('status_labels') or {}).items()
            if status.get('color')}


def build_pr_row(sig_name, full_repo, item, config, with_doc_status=True):
    """
    Build one report row for a pull request.
    :param with_doc_status: append the doc label state column (docs report only)
    :return: [sig, repo, target branch, number link, title link, status, open days, doc status]
    """
    title = item['title']
    html_url = item['link']
    number_link = "<a href='{0}'>{1}</a>".format(html_url, '#' + html_url.split('/')[-1])
    link = "<a href='{0}'>{1}</a>".format(html_url, title)
    labels = item['labels'].split(',')
    ref_branch = item.get('ref') or '-'
    cla_label = config.get('cla_label')
    ci_failed_label = config.get('ci_failed_label')
    wait_update_label = config.get('wait_update_label')
    status = '待合入'
    if item['draft']:
        status = fill_status(status, '草稿')
    if cla_label and cla_label not in labels:
        status = fill_status(status, 'CLA认证失败')
    if ci_failed_label and ci_failed_label in labels:
        status = fill_status(status, '门禁检查失败')
    if not item['mergeable']:
        status = fill_status(status, '存在冲突')
    if wait_update_label and wait_update_label in labels:
        status = fill_status(status, '等待更新')
    row = [sig_name, full_repo, ref_branch, number_link, link, status,
           count_duration(item['created_at'])]
    if with_doc_status:
        doc_text, _ = doc_status(item, config.get('docs_report') or {})
        row.append(doc_text)
    return row


def build_issue_row(sig_name, full_repo, item):
    """
    Build one report row for a doc related issue (no branch column).
    :return: [sig, repo, number link, title link, status, open days]
    """
    title = item['title']
    html_url = item['link']
    number_link = "<a href='{0}'>{1}</a>".format(html_url, '#' + html_url.split('/')[-1])
    link = "<a href='{0}'>{1}</a>".format(html_url, title)
    status = item.get('issue_type', '')
    for extra in (item.get('issue_state'), item.get('assignee')):
        if extra:
            status = '{} / {}'.format(status, extra) if status else extra
    return [sig_name, full_repo, number_link, link, status, count_duration(item['created_at'])]


# ---------------------------------------------------------------------------
# Collecting doc related rows
# ---------------------------------------------------------------------------

def collect_pr_rows(sigs, repos_pulls_mapping, config, predicate=None, with_doc_status=False):
    """
    Collect open pull requests of every sig.
    :param sigs: sigs list from get_sigs()
    :param repos_pulls_mapping: {repo/.../number: pull item}
    :param config: community config dict
    :param predicate: optional filter, called with the pull item; None keeps every pull
    :param with_doc_status: append the doc label state column (docs report only)
    :return: list of report rows
    """
    orgs_lower = [org.lower() for org in config['orgs']]
    rows = []
    for sig in sigs:
        sig_name = sig['name']
        if sig_name in (config.get('skip_sigs') or []):
            continue
        for full_repo in sig['repositories']:
            if full_repo.split('/')[0].lower() not in orgs_lower:
                continue
            for mapping_key in sorted(repos_pulls_mapping.keys()):
                if not mapping_key.startswith(full_repo + '/'):
                    continue
                item = repos_pulls_mapping[mapping_key]
                if predicate is not None and not predicate(item):
                    continue
                log.logger.info('Find pr: {}'.format(mapping_key))
                rows.append(build_pr_row(sig_name, full_repo, item, config,
                                         with_doc_status=with_doc_status))
    return rows


def collect_issue_rows(sigs, repos_issues_mapping, config, predicate=None):
    """
    Collect open issues of every sig.
    :param sigs: sigs list from get_sigs()
    :param repos_issues_mapping: {repo/.../number: issue item}
    :param config: community config dict
    :param predicate: optional filter, called with the issue item; None keeps every issue
    :return: list of report rows
    """
    orgs_lower = [org.lower() for org in config['orgs']]
    rows = []
    for sig in sigs:
        sig_name = sig['name']
        if sig_name in (config.get('skip_sigs') or []):
            continue
        for full_repo in sig['repositories']:
            if full_repo.split('/')[0].lower() not in orgs_lower:
                continue
            for mapping_key in sorted(repos_issues_mapping.keys()):
                if not mapping_key.startswith(full_repo + '/'):
                    continue
                item = repos_issues_mapping[mapping_key]
                if predicate is not None and not predicate(item):
                    continue
                log.logger.info('Find issue: {}'.format(mapping_key))
                rows.append(build_issue_row(sig_name, full_repo, item))
    return rows


def collect_doc_pr_rows(sigs, repos_pulls_mapping, config):
    """
    Collect doc related open pull requests of every sig.
    :return: list of report rows
    """
    pr_labels = (config.get('docs_report') or {}).get('pr_labels') or []
    return collect_pr_rows(sigs, repos_pulls_mapping, config,
                           predicate=lambda item: is_doc_pr(item, pr_labels),
                           with_doc_status=True)


def collect_doc_issue_rows(sigs, repos_issues_mapping, config):
    """
    Collect doc related open issues of every sig.
    :return: list of report rows
    """
    docs_config = config.get('docs_report') or {}
    title_prefix = docs_config.get('issue_title_prefix')
    issue_type = docs_config.get('issue_type')
    return collect_issue_rows(sigs, repos_issues_mapping, config,
                              predicate=lambda item: is_doc_issue(item, title_prefix, issue_type))


# ---------------------------------------------------------------------------
# Receivers
# ---------------------------------------------------------------------------

def resolve_recipients(config, report_key='docs_report'):
    """
    Resolve the configured community report receivers into (key, email) pairs.
    Members can be resolved dynamically from the cloned community repo (receivers_sig /
    receiver_roles), so sig membership changes need no config edit; explicit receivers
    (an entry containing '@' is an email, otherwise a gitcode_id looked up in the sig-info
    email mapping) are added on top.
    :param config: community config dict
    :param report_key: config block holding the receivers ('docs_report' or 'qa_report')
    :return: list of (key, email); key is what email_controls.yaml is matched against
    """
    report_config = config.get(report_key) or {}
    resolved = []
    sig_name = report_config.get('receivers_sig')
    if sig_name:
        roles = report_config.get('receiver_roles') or ['maintainer']
        resolved.extend(get_sig_members(sig_name, roles))
    receivers = report_config.get('receivers') or []
    email_mappings = None
    for receiver in receivers:
        receiver = str(receiver).strip()
        if not receiver:
            continue
        if '@' in receiver:
            resolved.append((receiver, receiver))
            continue
        if email_mappings is None:
            email_mappings = get_email_mappings()
        email = email_mappings.get(receiver)
        if not email:
            log.logger.warning('WARNING! Cannot find email address of {} receiver {}'.format(report_key, receiver))
            continue
        resolved.append((receiver, email))
    # a member may also be listed as an explicit receiver; keep only the first occurrence
    unique = []
    seen_emails = set()
    for key, email in resolved:
        email_lower = email.lower()
        if email_lower in seen_emails:
            continue
        seen_emails.add(email_lower)
        unique.append((key, email))
    if not unique:
        log.logger.warning('WARNING! No {} receiver could be resolved, no email will be sent'.format(report_key))
    return unique


# ---------------------------------------------------------------------------
# Doc statistics
# ---------------------------------------------------------------------------

def send_community_report(data_dir, csv_name, mail_type, rows, is_issue, subject, body_text,
                          title, report_config, config, recipients, controls, compare_dict,
                          extra_header=None, extra_fills=None):
    """
    Render one community wide report and send it to the resolved receivers.
    Used by both the docs report (docs_statistics.py) and the QA report (qa_statistics.py).
    :param data_dir: directory to store temporary data
    :param csv_name: file name (without extension) of the intermediate csv
    :param mail_type: mail type used by email controls ('docs_pr' / 'qa_pr' / ...)
    :param rows: report rows
    :param is_issue: True for issue rows (no branch column)
    :param subject: mail subject
    :param body_text: mail body text
    :param title: section title shown above the table
    :param report_config: docs_report / qa_report block (nickname)
    :param config: community config dict
    :param recipients: (key, email) pairs from resolve_recipients()
    :param controls: email controls dict from load_email_controls()
    :param compare_dict: week-over-week comparison info
    :param extra_header: optional header of one extra trailing column
    :param extra_fills: optional {cell value: fill color} for that extra column
    """
    if not rows:
        log.logger.info('No rows for {}, skip {} email'.format(mail_type, mail_type))
        return
    test_email = os.getenv('test_reviever_email', '').strip()
    test_mode = bool(test_email)
    dry_run = os.getenv('DRY_RUN', '').strip().lower() == 'true'
    test_user = os.getenv('TEST_USER', '').strip()
    nickname = report_config.get('nickname') or config.get('display_name')
    csv_path = '{}/{}.csv'.format(data_dir, csv_name)
    with codecs.open(csv_path, 'w', encoding='utf-8') as f:
        csv.writer(f).writerows(sort_rows_by_sig_duration(rows, 5 if is_issue else 6))
    xlsx_path = csv_to_xlsx(csv_path)
    excel_optimization(xlsx_path, compare_dict, is_issue=is_issue, extra_header=extra_header,
                       extra_fills=extra_fills)
    merged_html = merge_html_parts([(title, xlsx_path.replace('.xlsx', '.html'))], mail_type)
    log.logger.info('Generated {} report with {} rows'.format(mail_type, len(rows)))
    if dry_run:
        write_dry_run_html(mail_type, 'all', merged_html)
        return
    if test_mode:
        send_email('', nickname, [test_email], subject, body_text=body_text, html_content=merged_html)
        log.logger.info('[TEST MODE] {} email sent to {}'.format(mail_type, test_email))
        return
    wanted = [(key, email) for key, email in recipients
              if (not test_user or key == test_user) and should_send(controls, key, mail_type, 'receiver')]
    if not wanted:
        log.logger.warning('WARNING! No receiver for {} (receivers configured, controls or TEST_USER filtering)'.format(mail_type))
        return
    for key, email in wanted:
        # greet each receiver by its own id so the mail itself shows who it is addressed to;
        # the report content is still the same for everybody
        send_email('', key, [email], subject, body_text=body_text, html_content=merged_html)
        log.logger.info('{} email sent to {}'.format(mail_type, email))


# ---------------------------------------------------------------------------
# Doc statistics
# ---------------------------------------------------------------------------

def docs_statistics(data_dir, sigs, repos_pulls_mapping, repos_issues_mapping, compare_dict, config=None):
    """
    Build and send the community wide documentation reports (one PR mail, one Issue mail).
    :param data_dir: directory to store temporary data
    :param sigs: a dict of every sig and its repositories
    :param repos_pulls_mapping: mappings between repos and pulls
    :param repos_issues_mapping: mappings between repos and issues
    :param compare_dict: a dict of every sig and its compare info
    :param config: community config dict (defaults to the active community)
    """
    config = config or load_community_config()
    docs_config = config.get('docs_report') or {}
    log.logger.info('=' * 25 + ' DOC STATISTICS ' + '=' * 25)
    test_email = os.getenv('test_reviever_email', '').strip()
    test_mode = bool(test_email)
    redirect_email = test_email
    dry_run = os.getenv('DRY_RUN', '').strip().lower() == 'true'
    test_user = os.getenv('TEST_USER', '').strip()
    if test_mode:
        log.logger.info('[TEST MODE] Doc report emails will be sent to {}'.format(redirect_email))
    if dry_run:
        log.logger.info('[DRY RUN] Doc report emails will be generated locally in test_output/, not sent')
    if test_user:
        log.logger.info('[TEST USER] Only processing receiver: {}'.format(test_user))
    controls = load_email_controls()
    recipients = resolve_recipients(config)
    pr_rows = collect_doc_pr_rows(sigs, repos_pulls_mapping, config)
    issue_rows = collect_doc_issue_rows(sigs, repos_issues_mapping, config)
    log.logger.info('Found {} doc related PRs and {} doc related issues'.format(len(pr_rows), len(issue_rows)))

    send_community_report(data_dir, 'doc_statistics_docs_pr', 'docs_pr', pr_rows, False,
                          docs_config.get('subject_pr'), docs_config.get('body_pr'),
                          '资料相关 PR（按 SIG 分组）', docs_config, config, recipients,
                          controls, compare_dict,
                          extra_header=docs_config.get('status_header'),
                          extra_fills=doc_status_fills(docs_config)
                          if docs_config.get('status_header') else None)
    send_community_report(data_dir, 'doc_statistics_docs_issue', 'docs_issue', issue_rows, True,
                          docs_config.get('subject_issue'), docs_config.get('body_issue'),
                          '资料相关 Issue（按 SIG 分组）', docs_config, config, recipients,
                          controls, compare_dict)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    """
    main function for doc statistics
    """
    config = load_community_config()
    docs_config = config.get('docs_report') or {}
    if not docs_config.get('enabled'):
        log.logger.info('docs_report is not enabled for community {}, nothing to do'.format(config['name']))
        return
    config = setup_community(config, workdir=docs_config.get('workdir'))
    data_dir = prepare_env(config)
    sigs, sigs_list = get_sigs(config)
    compare_dict = all_sigs_compare(sigs_list, config)
    repos_pulls_mapping = get_repos_pulls_mapping(config, sigs)
    repos_issues_mapping = get_repos_issues_mapping(config, sigs)
    docs_statistics(data_dir, sigs, repos_pulls_mapping, repos_issues_mapping, compare_dict, config)


if __name__ == '__main__':
    main()
