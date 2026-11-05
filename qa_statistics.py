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

# 闭环趋势表的列：统计区间 / 待处理（区间末未闭环存量）/ 超期 / 严重超期（严重+长期合并）/
# 期间闭环（本区间内闭环的数量）/ 闭环率。
# 闭环率 = 期间闭环 /（期间闭环 + 待处理），两周窗口因此可以直接对比
TREND_HEADERS = ('统计区间', '待处理', '超期', '严重超期', '期间闭环', '闭环率')


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
# Closure trend (the last two Wednesday-to-Wednesday weeks)
# ---------------------------------------------------------------------------

WEDNESDAY = 2   # datetime.date.weekday(): Monday = 0


def last_wednesday(today):
    """Most recent Wednesday on or before today (the anchor of the trend windows)."""
    return today - datetime.timedelta(days=(today.weekday() - WEDNESDAY) % 7)


def trend_windows(today):
    """
    The two seven-day windows compared in the trend, anchored on Wednesdays:
    上上周三~上周三 and 上周三~本周三. The end is exclusive, so an item closed on a
    Wednesday is counted by the window starting that day, never by both.
    :param today: date of the run
    :return: [(start_day, end_day_exclusive)], oldest first
    """
    last_wed = last_wednesday(today)
    return [(last_wed - datetime.timedelta(days=14), last_wed - datetime.timedelta(days=7)),
            (last_wed - datetime.timedelta(days=7), last_wed + datetime.timedelta(days=1))]


def trend_label(today):
    """'10-22~11-04' — first window start to the last window's inclusive end."""
    windows = trend_windows(today)
    return '{}~{}'.format(windows[0][0].strftime('%m-%d'),
                          (windows[-1][1] - datetime.timedelta(days=1)).strftime('%m-%d'))


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
    :return: {link: (created_day, closed_day or None)}
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
    return records


def _rate_text(rate):
    """Render a closure rate as a percentage, or '-' when the denominator is zero."""
    return '-' if rate is None else '{:.1f}%'.format(rate * 100)


def trend_points(records, today=None):
    """
    Build one row per Wednesday-to-Wednesday window (上周三 and 本周三).
    Numbers are measured over that window: backlog/overdue describe its last day,
    period_closed counts the closures inside it, and the rate is based on them, so the
    two rows are directly comparable.
    :param records: list of (created_day, closed_day or None)
    :param today: date of the run, defaults to the current date in UTC+8
    :return: rows; every point carries label, period_closed, rate, rate_text,
             backlog, overdue and severe
    """
    today = today or datetime.datetime.now(GITCODE_TZ).date()
    windows = trend_windows(today)
    overdue_days = DURATION_STAGES[0][0]      # 7
    severe_days = DURATION_STAGES[1][0]       # 30, 严重与长期合并

    def stock_at(day):
        """Open items on that day, split by age."""
        live = [created for created, closed in records
                if created <= day and (closed is None or closed > day)]
        return {
            'backlog': len(live),
            'overdue': sum(1 for created in live if (day - created).days > overdue_days),
            'severe': sum(1 for created in live if (day - created).days > severe_days),
        }

    def summarize(start_day, end_day, suffix):
        period_closed = sum(1 for _, closed in records
                            if closed and start_day <= closed <= end_day)
        point = stock_at(end_day)
        # denominator = what the window contained = closed in it + still open at its end
        denominator = period_closed + point['backlog']
        point.update({
            'period_closed': period_closed,
            'rate': (period_closed / denominator) if denominator else None,
            'label': '{}~{}（{}）'.format(start_day.strftime('%m-%d'),
                                          end_day.strftime('%m-%d'), suffix),
        })
        point['rate_text'] = _rate_text(point['rate'])
        return point

    rows = []
    for index, (start_day, end_excl) in enumerate(windows):
        suffix = '本周' if index == len(windows) - 1 else '上周'
        rows.append(summarize(start_day, end_excl - datetime.timedelta(days=1), suffix))
    return rows


def build_closure_trend(open_items, history_items, today=None):
    """
    Compute the community wide closure trend of one item kind (PR or Issue).
    The denominator of a window is what that window contained: the items closed inside
    it plus the backlog still open at its end, so the two weeks compare like for like.
    :param open_items: items of the current open mapping
    :param history_items: items from gitcode_history_items()
    :param today: date of the run, defaults to the current date in UTC+8
    :return: {'window_label', 'rows'}
    """
    today = today or datetime.datetime.now(GITCODE_TZ).date()
    rows = trend_points(list(item_records(open_items, history_items).values()), today)
    return {'window_label': trend_label(today), 'rows': rows}


def repo_of_link(link):
    """'https://gitcode.com/org/repo/pulls/1' → 'org/repo'."""
    parts = str(link or '').split('/', 3)
    if len(parts) < 4:
        return ''
    return '/'.join(parts[3].split('/')[:2])


def repo_sig_mapping(sigs, config):
    """
    Map every repository to the SIG the reports attribute it to: the first SIG (in the
    order collect_*_rows walks them) that lists it, skipping ignored SIGs and orgs.
    :param sigs: sigs list from get_sigs()
    :param config: community config dict
    :return: {repo: sig_name}
    """
    orgs_lower = [org.lower() for org in config['orgs']]
    mapping = {}
    for sig in sigs:
        if sig['name'] in (config.get('skip_sigs') or []):
            continue
        for repo in sig['repositories']:
            if repo.split('/')[0].lower() not in orgs_lower:
                continue
            mapping.setdefault(repo, sig['name'])
    return mapping


def build_sig_trends(open_items, history_items, repo_sig, today=None):
    """
    Build the same trend per SIG, attributed by repository like the detail table.
    :param open_items: items of the current open mapping
    :param history_items: items from gitcode_history_items()
    :param repo_sig: {repo: sig_name} from repo_sig_mapping()
    :param today: date of the run, defaults to the current date in UTC+8
    :return: {sig_name: {'window_label', 'rows'}}; SIGs without any item in the
             window are omitted
    """
    today = today or datetime.datetime.now(GITCODE_TZ).date()
    grouped = {}
    for link, record in item_records(open_items, history_items).items():
        sig_name = repo_sig.get(repo_of_link(link))
        if sig_name:
            grouped.setdefault(sig_name, []).append(record)
    trends = {}
    for sig_name, records in grouped.items():
        trends[sig_name] = {'window_label': trend_label(today),
                            'rows': trend_points(records, today)}
    return trends


def build_trends(sigs, kind, open_mapping, repo_sig, today=None):
    """
    Fetch the history window once and build both the community wide and the per-SIG trend.
    :param sigs: sigs list from get_sigs()
    :param kind: 'pulls' or 'issues'
    :param open_mapping: current open mapping from get_repos_*_mapping()
    :param repo_sig: {repo: sig_name} from repo_sig_mapping()
    :param today: date of the run, defaults to the current date in UTC+8
    :return: (total_trend, sig_trends)
    """
    today = today or datetime.datetime.now(GITCODE_TZ).date()
    window_start = trend_windows(today)[0][0]
    since = datetime.datetime.combine(window_start, datetime.time.min, tzinfo=GITCODE_TZ)
    history = gitcode_history_items(sigs, kind, since.isoformat())
    open_items = list(open_mapping.values())
    return (build_closure_trend(open_items, history, today),
            build_sig_trends(open_items, history, repo_sig, today))


def _trend_row_html(row, cell_style, name_style):
    """One table row of the trend table, with a bar next to the closure rate."""
    if row['rate'] is None:
        bar = ''
    else:
        width = max(0, min(100, round(row['rate'] * 100)))
        bar = ('<div style="background:#eeeeee;width:70px;height:9px;display:inline-block;'
               'vertical-align:middle;margin-left:6px">'
               '<div style="background:#70AD47;height:9px;width:{}%"></div></div>').format(width)
    return ('<tr><td style="{}">{}</td><td style="{}">{}</td><td style="{}">{}</td>'
            '<td style="{}">{}</td><td style="{}">{}</td>'
            '<td style="{}">{}{}</td></tr>'.format(
                name_style, row['label'], cell_style, row['backlog'], cell_style,
                row['overdue'], cell_style, row['severe'], cell_style,
                row['period_closed'], cell_style, row['rate_text'], bar))


def _trend_head_html(head_style):
    return '<tr>{}</tr>'.format(
        ''.join('<th style="{}">{}</th>'.format(head_style, h) for h in TREND_HEADERS))


def render_closure_trend(trend, label):
    """
    Render the community wide closure trend as an HTML fragment.
    :param trend: result of build_closure_trend()
    :param label: 'PR' or 'Issue'
    :return: HTML string to prepend to the mail body
    """
    border = 'border:1px solid #d0d0d0;'
    head_style = border + 'padding:6px 8px;background:#E2EFDA;font-weight:bold;text-align:center;'
    cell_style = border + 'padding:6px 8px;text-align:center;'
    name_style = border + 'padding:6px 8px;text-align:left;'
    parts = ['<h3 style="margin:18px 0 8px">全社区 {} 闭环趋势（{}）</h3>'.format(
        label, trend['window_label'])]
    parts.append('<table cellspacing="0" cellpadding="0" '
                 'style="border-collapse:collapse;font-size:13px">')
    parts.append(_trend_head_html(head_style))
    for row in trend['rows']:
        parts.append(_trend_row_html(row, cell_style, name_style))
    parts.append('</table>')
    parts.append(_trend_footnote())
    return ''.join(parts)


def render_sig_trends(sig_trends, label):
    """
    Render every SIG's closure trend as one HTML table with a shared header, so the
    column widths stay uniform instead of inheriting the detail table's.
    :param sig_trends: result of build_sig_trends(); empty means nothing to render
    :param label: 'PR' or 'Issue'
    :return: HTML string (empty when there is no per-SIG data)
    """
    if not sig_trends:
        return ''
    border = 'border:1px solid #d0d0d0;'
    head_style = border + 'padding:6px 8px;background:#E2EFDA;font-weight:bold;text-align:center;'
    cell_style = border + 'padding:6px 8px;text-align:center;'
    name_style = border + 'padding:6px 8px;text-align:left;'
    sig_style = (border + 'border-top:2px solid #70AD47;padding:8px;background:#E2EFDA;'
                 'font-weight:bold;text-align:center;font-size:14px;')
    parts = ['<h3 style="margin:18px 0 8px">各 SIG {} 闭环趋势</h3>'.format(label)]
    parts.append('<table cellspacing="0" cellpadding="0" '
                 'style="border-collapse:collapse;font-size:13px">')
    for sig_name in sorted(sig_trends):
        trend = sig_trends[sig_name]
        parts.append('<tr><td colspan="{}" style="{}">{}</td></tr>'.format(
            len(TREND_HEADERS), sig_style, sig_name))
        # repeat the header for every SIG so the columns stay identifiable in a long mail
        parts.append(_trend_head_html(head_style))
        for row in trend['rows']:
            parts.append(_trend_row_html(row, cell_style, name_style))
    parts.append('</table>')
    parts.append(_trend_footnote())
    return ''.join(parts)


def _trend_footnote():
    return ('<p style="font-size:12px;color:#666;margin:6px 0 0">'
            '两行分别是上周三~本周三的两个 7 天窗口（含起始日、不含结束日）。'
            '待处理为该区间末的未闭环存量，期间闭环为本区间内闭环的数量；'
            '闭环率 = 期间闭环 /（期间闭环 + 待处理），两行可直接对比；'
            '超期指开启天数 &gt; 7 天，严重超期指 &gt; 30 天；'
            '仅统计 GitCode API 可见仓库，私有仓库不在其中。</p>')


# ---------------------------------------------------------------------------
# QA statistics
# ---------------------------------------------------------------------------

def closure_trend_enabled(qa_config, now=None):
    """
    The closure trend is only attached to the Wednesday mail; the Friday run keeps the
    plain PR/Issue reminder. QA_CLOSURE_TREND=true forces it on a manual run (a Jenkins
    boolean build parameter), so the trend can be previewed without waiting for Wednesday;
    an unset/false value keeps the weekday rule.
    :param qa_config: the community's qa_report block
    :param now: datetime of the run, defaults to the current time in UTC+8
    :return: bool
    """
    if not qa_config.get('closure_trend'):
        return False
    if os.getenv('QA_CLOSURE_TREND', '').strip().lower() in ('true', '1', 'yes'):
        return True
    now = now or datetime.datetime.now(GITCODE_TZ)
    return now.weekday() == WEDNESDAY


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
    trend_config = qa_config.get('closure_trend')
    trend_enabled = closure_trend_enabled(qa_config)
    if trend_config:
        log.logger.info('Closure trend is {} (Wednesday scheduled runs, '
                        'QA_CLOSURE_TREND=true for a manual preview)'.format(
                            'enabled' if trend_enabled else 'disabled for this run'))
    trend_pr = trend_issue = None
    if trend_enabled:
        repo_sig = repo_sig_mapping(sigs, config)
        total_pr, sig_trends_pr = build_trends(sigs, 'pulls', repos_pulls_mapping, repo_sig)
        total_issue, sig_trends_issue = build_trends(sigs, 'issues', repos_issues_mapping, repo_sig)
        trend_pr = render_closure_trend(total_pr, 'PR') + render_sig_trends(sig_trends_pr, 'PR')
        trend_issue = render_closure_trend(total_issue, 'Issue') + \
            render_sig_trends(sig_trends_issue, 'Issue')
    # 周五的常规提醒邮件不带任何统计（趋势区与明细表的 SIG 汇总行都不渲染）
    sig_stats_enabled = trend_enabled if trend_config else True

    send_community_report(data_dir, 'qa_statistics_qa_pr', 'qa_pr', pr_rows, False,
                          qa_config.get('subject_pr'), qa_config.get('body_pr'),
                          '全社区 PR（按 SIG 分组）', qa_config, config, recipients,
                          controls, compare_dict, trend_html=trend_pr,
                          sig_stats_enabled=sig_stats_enabled)
    send_community_report(data_dir, 'qa_statistics_qa_issue', 'qa_issue', issue_rows, True,
                          qa_config.get('subject_issue'), qa_config.get('body_issue'),
                          '全社区 Issue（按 SIG 分组）', qa_config, config, recipients,
                          controls, compare_dict, trend_html=trend_issue,
                          sig_stats_enabled=sig_stats_enabled)


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
