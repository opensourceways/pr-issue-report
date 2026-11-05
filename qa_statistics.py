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

import datetime
import os
from common import *
from docs_statistics import (collect_issue_rows, collect_pr_rows, resolve_recipients,
                             send_community_report)
from issue_statistics import get_repos_issues_mapping
from pr_statistics import get_repos_pulls_mapping

# 报表触发日：周三、周五（与 Jenkins 调度一致），趋势按这些日期取点
TREND_WEEKDAYS = (2, 4)


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
# Closure trend (month-to-date)
# ---------------------------------------------------------------------------

def month_trend_window(today):
    """
    Return (window_start, month_start) of the closure trend.
    window_start is the Monday of the previous month's last natural week, clipped to
    that month: with today = 2026-11-05 it is 2026-10-26, because 2026-10-31 is a
    Saturday and its week starts on Monday 10-26.
    :param today: date of the run
    :return: (window_start, month_start) as dates
    """
    month_start = today.replace(day=1)
    prev_month_last = month_start - datetime.timedelta(days=1)
    return prev_month_last - datetime.timedelta(days=prev_month_last.weekday()), month_start


def trigger_days(start, end, weekdays=TREND_WEEKDAYS):
    """
    List the report trigger days inside [start, end].
    :param start: first date (inclusive)
    :param end: last date (inclusive)
    :param weekdays: weekday numbers, 0 = Monday; defaults to Wednesday and Friday
    :return: list of dates
    """
    days = []
    day = start
    while day <= end:
        if day.weekday() in weekdays:
            days.append(day)
        day += datetime.timedelta(days=1)
    return days


def _parse_day(value):
    """Parse an open-mapping created_at ('%Y-%m-%d %H:%M:%S') or a datetime into a date."""
    if not value:
        return None
    if isinstance(value, datetime.datetime):
        return value.date()
    return datetime.datetime.strptime(value, '%Y-%m-%d %H:%M:%S').date()


def item_records(open_items, history_items):
    """
    Merge the current backlog with the history window into one record per item.
    A reopened item is listed as open again, so the open record wins.
    :param open_items: items of the current open mapping
    :param history_items: items from gitcode_history_items()
    :return: list of (created_day, closed_day or None)
    """
    records = {}
    open_links = set()
    for item in open_items:
        link = item.get('link')
        created = _parse_day(item.get('created_at'))
        if not link or not created:
            continue
        open_links.add(link)
        records[link] = (created, None)
    for item in history_items:
        link = item['link']
        if link in open_links or not item['created_at']:
            continue
        records[link] = (item['created_at'].date(),
                         item['closed_at'].date() if item['closed_at'] else None)
    return list(records.values())


def build_closure_trend(open_items, history_items, today=None):
    """
    Compute the cumulative closure trend of one item kind (PR or Issue).
    The denominator is the backlog still open when the period starts plus everything
    created since, so a rate below 100% means the period cannot yet close what it
    inherited.
    :param open_items: items of the current open mapping
    :param history_items: items from gitcode_history_items()
    :param today: date of the run, defaults to the current date in UTC+8
    :return: {'month_label', 'window_row', 'rows'}
    """
    today = today or datetime.datetime.now(GITCODE_TZ).date()
    window_start, month_start = month_trend_window(today)
    records = item_records(open_items, history_items)

    def backlog_at(start_day):
        """Items not closed yet when start_day begins."""
        return sum(1 for created, closed in records
                   if created < start_day and (closed is None or closed >= start_day))

    def summarize(start_day, end_day, base):
        new = sum(1 for created, _ in records if start_day <= created <= end_day)
        closed = sum(1 for _, closed in records
                     if closed and start_day <= closed <= end_day)
        denominator = base + new
        return {
            'new': new,
            'closed': closed,
            'rate': (closed / denominator) if denominator else None,
            'backlog': denominator - closed,
        }

    prev_month_last = month_start - datetime.timedelta(days=1)
    window_row = summarize(window_start, prev_month_last, backlog_at(window_start))
    if window_start == prev_month_last:
        # the previous month ended on a Monday, so its last natural week is a single day
        window_row['label'] = '{}（上月末周）'.format(window_start.strftime('%m-%d'))
    else:
        window_row['label'] = '{}~{}（上月末周）'.format(window_start.strftime('%m-%d'),
                                                      prev_month_last.strftime('%m-%d'))

    days = trigger_days(month_start, today)
    if today not in days:
        # a manual run off schedule still reports up to the current day
        days.append(today)

    rows = []
    base_month = backlog_at(month_start)
    for day in days:
        row = summarize(month_start, day, base_month)
        if day == today:
            row['label'] = '{}（当天）'.format(day.strftime('%m-%d'))
        else:
            row['label'] = '{}（周{}）'.format(day.strftime('%m-%d'), '一二三四五六日'[day.weekday()])
        rows.append(row)

    return {'month_label': '{} 月'.format(month_start.month), 'window_row': window_row, 'rows': rows}


def render_closure_trend(trend, label):
    """
    Render a closure trend section as an HTML fragment (table with CSS bars).
    :param trend: result of build_closure_trend()
    :param label: 'PR' or 'Issue'
    :return: HTML string to prepend to the mail body
    """
    border = 'border:1px solid #d0d0d0;'
    head_style = border + 'padding:6px 8px;background:#E2EFDA;font-weight:bold;text-align:center;'
    cell_style = border + 'padding:6px 8px;text-align:center;'
    name_style = border + 'padding:6px 8px;text-align:left;'
    headers = ('阶段 / 日期', '累计新增', '累计闭环', '闭环率', '未闭环存量')
    parts = ['<h3 style="margin:18px 0 8px">{} {} 闭环趋势</h3>'.format(trend['month_label'], label)]
    parts.append('<table cellspacing="0" cellpadding="0" '
                 'style="border-collapse:collapse;font-size:13px">')
    parts.append('<tr>{}</tr>'.format(
        ''.join('<th style="{}">{}</th>'.format(head_style, h) for h in headers)))
    for row in [trend['window_row']] + trend['rows']:
        if row['rate'] is None:
            rate_text, bar = '-', ''
        else:
            width = max(0, min(100, round(row['rate'] * 100)))
            rate_text = '{:.1f}%'.format(row['rate'] * 100)
            bar = ('<div style="background:#eeeeee;width:70px;height:9px;display:inline-block;'
                   'vertical-align:middle;margin-left:6px">'
                   '<div style="background:#70AD47;height:9px;width:{}%"></div></div>').format(width)
        parts.append('<tr><td style="{}">{}</td><td style="{}">{}</td><td style="{}">{}</td>'
                     '<td style="{}">{}{}</td><td style="{}">{}</td></tr>'.format(
                         name_style, row['label'], cell_style, row['new'], cell_style,
                         row['closed'], cell_style, rate_text, bar, cell_style, row['backlog']))
    parts.append('</table>')
    parts.append('<p style="font-size:12px;color:#666;margin:6px 0 0">'
                 '闭环率 = 累计闭环数 /（统计起点未闭环数 + 累计新增数）；'
                 '仅统计 GitCode API 可见仓库，私有仓库不在其中。</p>')
    return ''.join(parts)


def closure_trend_html(sigs, kind, open_mapping, label, today=None):
    """
    Fetch the history window and render one closure trend section.
    :param sigs: sigs list from get_sigs()
    :param kind: 'pulls' or 'issues'
    :param open_mapping: current open mapping from get_repos_*_mapping()
    :param label: 'PR' or 'Issue'
    :param today: date of the run, defaults to the current date in UTC+8
    :return: HTML string
    """
    today = today or datetime.datetime.now(GITCODE_TZ).date()
    window_start, _ = month_trend_window(today)
    since = datetime.datetime.combine(window_start, datetime.time.min, tzinfo=GITCODE_TZ)
    history = gitcode_history_items(sigs, kind, since.isoformat())
    return render_closure_trend(build_closure_trend(open_mapping.values(), history, today), label)


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
    trend_pr = trend_issue = None
    if qa_config.get('closure_trend'):
        trend_pr = closure_trend_html(sigs, 'pulls', repos_pulls_mapping, 'PR')
        trend_issue = closure_trend_html(sigs, 'issues', repos_issues_mapping, 'Issue')

    send_community_report(data_dir, 'qa_statistics_qa_pr', 'qa_pr', pr_rows, False,
                          qa_config.get('subject_pr'), qa_config.get('body_pr'),
                          '全社区 PR（按 SIG 分组）', qa_config, config, recipients,
                          controls, compare_dict, trend_html=trend_pr)
    send_community_report(data_dir, 'qa_statistics_qa_issue', 'qa_issue', issue_rows, True,
                          qa_config.get('subject_issue'), qa_config.get('body_issue'),
                          '全社区 Issue（按 SIG 分组）', qa_config, config, recipients,
                          controls, compare_dict, trend_html=trend_issue)


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
