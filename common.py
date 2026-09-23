#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Shared utilities for openEuler PR/issue statistics scripts.

Contains: logging, environment setup, SIG parsing, email mapping,
Excel generation, SMTP sending, and processed-rate comparison.
"""

import codecs
import csv
import datetime
import logging
import openpyxl
import os
import re
import shutil
import pandas as pd
import requests
import smtplib
import subprocess  # nosec B404
import sys
import time
import yaml
from collections import defaultdict
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from logging import handlers
from openpyxl.styles import Alignment, Border, PatternFill, Side, Font
from openpyxl.worksheet.hyperlink import Hyperlink
from xlsx2html import xlsx2html


# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------

class Logger(object):
    level_relations = {
        'debug': logging.DEBUG,
        'info': logging.INFO,
        'warning': logging.WARNING,
        'error': logging.ERROR,
        'crit': logging.CRITICAL
    }

    def __init__(self, filename, level='info', when='D', backCount=3,
                 fmt='%(asctime)s - %(pathname)s[line:%(lineno)d] - %(levelname)s: %(message)s'):
        self.fmt = fmt
        self.logger = logging.getLogger(filename)
        format_str = logging.Formatter(self.fmt)
        self.logger.setLevel(self.level_relations.get(level))
        sh = logging.StreamHandler()
        sh.setFormatter(format_str)
        th = handlers.TimedRotatingFileHandler(filename=filename, when=when, backupCount=backCount, encoding='utf-8')
        th.setFormatter(format_str)
        self.logger.addHandler(sh)
        self.logger.addHandler(th)

    def rebind(self, filename, level='debug'):
        """Re-point the logger's file handler to a new filename (e.g. after chdir)."""
        for handler in list(self.logger.handlers):
            self.logger.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass
        format_str = logging.Formatter(self.fmt)
        self.logger.setLevel(self.level_relations.get(level))
        sh = logging.StreamHandler()
        sh.setFormatter(format_str)
        th = handlers.TimedRotatingFileHandler(filename=filename, when='D', backupCount=3, encoding='utf-8')
        th.setFormatter(format_str)
        self.logger.addHandler(sh)
        self.logger.addHandler(th)


log = Logger('statistics.log', level='debug')


# ---------------------------------------------------------------------------
# Community config
# ---------------------------------------------------------------------------

_COMMUNITIES_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'communities.yaml')
_community_config_cache = {}


def load_community_config(name=None):
    """
    Load community config from communities.yaml (resolved relative to this file).
    :param name: community name, defaults to the COMMUNITY env var or 'openeuler'
    :return: config dict (with 'name' injected)
    """
    if name is None:
        name = os.getenv('COMMUNITY', 'openeuler').strip() or 'openeuler'
    if name in _community_config_cache:
        return _community_config_cache[name]
    with open(_COMMUNITIES_CONFIG_PATH, 'r', encoding='utf-8') as f:
        all_configs = yaml.safe_load(f)
    if name not in all_configs:
        log.logger.error('ERROR! Unknown community {} in {}, exit...'.format(name, _COMMUNITIES_CONFIG_PATH))
        sys.exit(1)
    config = dict(all_configs[name])
    config['name'] = name
    _community_config_cache[name] = config
    return config


def setup_community(config=None, workdir=None):
    """
    Load the active community config and switch into its dedicated working directory.
    Each community gets its own folder containing community/, data/,
    email_mapping.yaml and statistics.log, so runs never interfere.
    :param config: community config dict (defaults to the active community)
    :param workdir: working directory name, defaults to the community name. Pipelines
                    belonging to the same community can run side by side by passing
                    their own directory (e.g. the docs report uses boostkit-docs/)
    :return: config dict
    """
    config = config or load_community_config()
    workdir = workdir or config['name']
    os.makedirs(workdir, exist_ok=True)
    os.chdir(workdir)
    log.rebind('statistics.log')
    log.logger.info('Community: {}, workdir: {}'.format(config['name'], os.getcwd()))
    return config


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------

def prepare_env(config=None):
    """
    Prepare repository and directory
    :param config: community config dict (defaults to the active community)
    """
    config = config or load_community_config()
    log.logger.info('=' * 25 + ' PREPARE ENVIRONMENT ' + '=' * 25)
    if os.path.exists('community'):
        shutil.rmtree('community')
    subprocess.run(['git', 'clone', config['community_repo']], check=True)  # nosec B603 B607
    if not os.path.exists('community'):
        log.logger.error('Fail to clone code, exit...')
        sys.exit(1)
    data_dir = 'data'
    if os.path.exists(data_dir):
        shutil.rmtree(data_dir)
    os.makedirs(data_dir, exist_ok=True)
    if not os.path.exists('data'):
        log.logger.error('Fail to make data directory, exit...')
        sys.exit(1)
    log.logger.info('ENV is already.\n')
    return data_dir


# ---------------------------------------------------------------------------
# SIG parsing
# ---------------------------------------------------------------------------

def get_sigs(config=None):
    """
    Get relationship between sigs and repositories
    :param config: community config dict (defaults to the active community)
    """
    config = config or load_community_config()
    log.logger.info('=' * 25 + ' GET SIGS INFO ' + '=' * 25)
    exclude_entries = ['README.md', 'sig-template', 'sig-recycle', 'create_sig_info_template.py']
    orgs_lower = {org.lower(): org for org in config['orgs']}
    sig_path = os.path.join('community', 'sig')
    sigs = []
    sigs_list = []
    for i in sorted(os.listdir(sig_path)):
        if i in exclude_entries:
            continue
        sig_dir = os.path.join(sig_path, i)
        if not os.path.isdir(sig_dir):
            continue
        if i not in [x['name'] for x in sigs]:
            sigs.append({'name': i, 'repositories': []})
            sigs_list.append(i)
        if config.get('repo_source') == 'sig_info':
            # sig-info.yaml is authoritative (its dir yamls may be stale)
            sig_info_file = os.path.join(sig_dir, 'sig-info.yaml')
            if not os.path.exists(sig_info_file):
                continue
            with open(sig_info_file, 'r', encoding='utf-8') as f:
                sig_info = yaml.safe_load(f)
            for entry in sig_info.get('repositories') or []:
                for repo in entry.get('repo') or []:
                    repo = repo.strip()
                    if repo and repo not in sigs[-1]['repositories']:
                        sigs[-1]['repositories'].append(repo)
            continue
        for subdir in os.listdir(sig_dir):
            canonical_org = orgs_lower.get(subdir.lower())
            if canonical_org is None:
                continue
            org_dir = os.path.join(sig_dir, subdir)
            if not os.path.isdir(org_dir):
                continue
            for filesdir, _, repos in os.walk(org_dir):
                for repo in repos:
                    if not repo.endswith('.yaml'):
                        continue
                    for sig in sigs:
                        if sig['name'] == i:
                            repositories = sig['repositories']
                            repositories.append(os.path.join(canonical_org, repo.split('.yaml')[0]))
    log.logger.info('Get sigs info.\n')
    return sigs, sigs_list


def get_user_id(user_dict):
    """Extract user ID from dict, trying multiple possible field names"""
    return user_dict.get('gitcode_id') or user_dict.get('gitee_id') or user_dict.get('atomgit_id', '')


def get_maintainers(sig):
    """
    Get maintainers of the sig and mark where "maintainers" come from
    :param sig: sig name
    :return: maintainers, sig_info_mark
    """
    owners_file = os.path.join('community', 'sig', sig, 'OWNERS')
    sig_info_file = os.path.join('community', 'sig', sig, 'sig-info.yaml')
    if os.path.exists(owners_file):
        with open(owners_file, 'r', encoding='utf-8') as f:
            maintainers = yaml.safe_load(f.read())['maintainers']
            return maintainers, False
    elif os.path.exists(sig_info_file):
        with open(sig_info_file, 'r', encoding='utf-8') as f:
            sig_info = yaml.safe_load(f.read())
            maintainers = [get_user_id(x) for x in sig_info['maintainers']]
            return maintainers, True
    else:
        log.logger.error('ERROR! Find SIG {} has neither OWNERS file nor sig-info.yaml.'.format(sig))
        sys.exit(1)


def get_committers_mapping(sig):
    """
    Get mappings between repos and committers
    :param sig: sig name
    :return: committers_mapping
    """
    sig_info_file = os.path.join('community', 'sig', sig, 'sig-info.yaml')
    if not os.path.exists(sig_info_file):
        return {}
    with open(sig_info_file, 'r', encoding='utf-8') as f:
        sig_info = yaml.safe_load(f.read())
    repositories = sig_info.get('repositories')
    if not repositories:
        return {}
    committers_mapping = {}
    for i in repositories:
        if 'committers' in i.keys():
            repos = i['repo']
            committers = [get_user_id(x) for x in i['committers']]
            for repo in repos:
                committers_mapping[repo] = committers
    return committers_mapping


def get_repo_members(maintainers, committers_mapping, repo):
    """
    Get reviewers of a repo — always includes maintainers, adds committers if present
    :param maintainers: maintainers of the sig
    :param committers_mapping: mappings between repos and committers
    :param repo: full name of repo
    :return: reviewers
    """
    reviewers = maintainers.copy()
    committers = committers_mapping.get(repo)
    if not committers:
        return reviewers
    for committer in committers:
        if committer not in reviewers:
            reviewers.append(committer)
    return reviewers


# ---------------------------------------------------------------------------
# Email mapping
# ---------------------------------------------------------------------------

def create_email_mappings(config=None):
    """
    Generate mappings between gitee_id and email addresses
    :param config: community config dict (defaults to the active community)
    """
    config = config or load_community_config()
    email_mappings = {}
    if not os.path.exists('community'):
        subprocess.run(['git', 'clone', config['community_repo']], check=True)  # nosec B603 B607
    sig_path = os.path.join('community', 'sig')
    for i in sorted(os.listdir(sig_path)):
        if i in ['README.md', 'sig-template', 'sig-recycle', 'create_sig_info_template.py']:
            continue
        log.logger.info('Starting to get email mappings of sig {}'.format(i))
        owners_file = os.path.join(sig_path, i, 'OWNERS')
        sig_info_file = os.path.join(sig_path, i, 'sig-info.yaml')
        if os.path.exists(owners_file):
            f = open(owners_file, 'r', encoding='utf-8')
            maintainers = yaml.safe_load(f)['maintainers']
            f.close()
            for maintainer in maintainers:
                if maintainer not in email_mappings.keys():
                    email_mappings[maintainer] = ''
        if os.path.exists(sig_info_file):
            f = open(sig_info_file, 'r', encoding='utf-8')
            sig_info = yaml.safe_load(f)
            f.close()
            maintainers = sig_info['maintainers']
            for maintainer in maintainers:
                maintainer_gitee_id = get_user_id(maintainer)
                maintainer_email = maintainer.get('email')
                if maintainer_email in ['null', 'NA'] or not maintainer_email:
                    maintainer_email = ''
                email_mappings[maintainer_gitee_id] = maintainer_email
            repositories = sig_info.get('repositories')
            if not repositories:
                continue
            for r in repositories:
                if 'committers' in r.keys():
                    commtters = r['committers']
                    for committer in commtters:
                        committer_gitee_id = get_user_id(committer)
                        committer_email = committer.get('email')
                        if committer_email in ['null', 'NA'] or not committer_email:
                            committer_email = ''
                        email_mappings[committer_gitee_id] = committer_email
    ready_to_remove = []
    for email_mapping in email_mappings:
        if not email_mappings[email_mapping]:
            ready_to_remove.append(email_mapping)
    for i in ready_to_remove:
        del email_mappings[i]
    # generate email_mapping.yaml
    with open('email_mapping.yaml', 'w', encoding='utf-8') as f:
        yaml.dump(email_mappings, f, default_flow_style=False)


def get_email_mappings():
    """
    Get email_mappings
    :return: email_mappings
    """
    create_email_mappings()
    if not os.path.exists('email_mapping.yaml'):
        log.logger.error('ERROR! Fail to generate email_mappings.')
        return {}
    email_mappings = yaml.safe_load(open('email_mapping.yaml'))
    return email_mappings


# ---------------------------------------------------------------------------
# Email controls (unsubscribe / preference management)
# ---------------------------------------------------------------------------

# mail_type -> roles user can opt out of; register new mail types here
MAIL_TYPES = {
    'pr': ('maintainer', 'committer'),
    'issue': ('maintainer', 'committer'),
    'docs_pr': ('receiver',),
    'docs_issue': ('receiver',),
}

# community wide report types (see docs_statistics.py); the unsubscribe note lists their
# own option only in these mails, so the weekly mails keep their wording unchanged
DOCS_MAIL_TYPES = ('docs_pr', 'docs_issue')


def expand_controls(config):
    """
    Expand simplified control syntax into full nested structure.
    :param config: raw control config for a single user (dict, bool or None)
    :return: dict {mail_type: {role: bool}} covering every type/role in MAIL_TYPES
    """
    result = {mail_type: {role: True for role in roles} for mail_type, roles in MAIL_TYPES.items()}
    if config is False or config is None:
        return result
    if isinstance(config, dict) and config.get('all') is False:
        for roles in result.values():
            for role in roles:
                roles[role] = False
        return result
    if not isinstance(config, dict):
        return result
    for mail_type, roles in MAIL_TYPES.items():
        if mail_type not in config:
            continue
        value = config[mail_type]
        if value is False:
            for role in roles:
                result[mail_type][role] = False
        elif isinstance(value, dict):
            for role in roles:
                if role in value:
                    result[mail_type][role] = bool(value[role])
    return result


def load_email_controls(path=None, community=None):
    """
    Load email control preferences from email_controls.yaml.
    A user's top-level config applies to all communities; an optional
    'communities' mapping overrides it completely for the named community
    (cells not declared there default to True).
    :param path: optional path to control file, defaults to EMAIL_CONTROLS_PATH env var
                 or email_controls.yaml next to this file (repo root, independent of CWD)
    :param community: active community name, defaults to the COMMUNITY env var or 'openeuler'
    :return: nested dict controls[gitee_id][mail_type][role] = bool
    """
    if path is None:
        path = os.getenv('EMAIL_CONTROLS_PATH') or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), 'email_controls.yaml')
    if not os.path.isabs(path):
        # resolve relative paths against the repo root, not the CWD
        # (the process chdirs into the per-community workdir at startup)
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), path)
    if community is None:
        community = os.getenv('COMMUNITY', 'openeuler').strip() or 'openeuler'
    controls = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: True)))
    if not os.path.exists(path):
        log.logger.info('Email controls file {} not found, all users will receive all emails'.format(path))
        return controls
    try:
        raw = yaml.safe_load(open(path, 'r', encoding='utf-8').read()) or {}
    except Exception as e:
        log.logger.error('Failed to parse email controls file {}: {}'.format(path, e))
        return controls
    for gitee_id, config in raw.items():
        expanded = expand_controls(config)
        if isinstance(config, dict):
            per_community = config.get('communities')
            if isinstance(per_community, dict) and community in per_community:
                expanded = expand_controls(per_community[community])
        controls[gitee_id] = expanded
    log.logger.info('Loaded email controls from {} (community: {})'.format(path, community))
    return controls


def should_send(controls, gitee_id, mail_type, role):
    """
    Check whether a user should receive a specific part of emails.
    :param controls: controls dict from load_email_controls()
    :param gitee_id: user gitee_id
    :param mail_type: any key of MAIL_TYPES ('pr', 'issue', 'docs_pr', 'docs_issue')
    :param role: one of the roles registered for that mail type
    :return: bool
    """
    return controls[gitee_id][mail_type][role]


def merge_html_parts(parts, mail_type):
    """
    Merge multiple HTML report parts into one HTML document.
    :param parts: list of tuples (title, html_path)
    :param mail_type: 'pr' or 'issue'
    :return: merged HTML string, or None if no valid parts
    """
    if not parts:
        return None
    bodies = []
    for title, html_path in parts:
        try:
            with open(html_path, 'r', encoding='utf-8') as f:
                content = f.read()
        except (IOError, OSError) as e:
            log.logger.warning('Failed to read HTML part {}: {}'.format(html_path, e))
            continue
        body_match = re.search(r'<body[^>]*>(.*?)</body>', content, re.DOTALL | re.IGNORECASE)
        body = body_match.group(1) if body_match else content
        bodies.append((title, body))
    if not bodies:
        return None
    merged_body = ''
    for title, body in bodies:
        merged_body += '<h3 style="margin-top:30px">{}</h3>\n{}'.format(title, body)
    reply_to = os.getenv('email_reply_to', 'huanglei227@h-partners.com').strip()
    unsubscribe_options = [
        '• 退订 PR 汇总<br>',
        '• 退订 Issue 汇总<br>',
    ]
    if mail_type in DOCS_MAIL_TYPES:
        unsubscribe_options.append('• 退订资料汇总（资料相关 PR / Issue）<br>')
    unsubscribe_options += [
        '• 只退订作为 Maintainer 的部分<br>',
        '• 只退订作为 Committer 的部分<br>',
        '• 完全退订所有邮件<br><br>',
    ]
    unsubscribe_note = (
        '<p style="font-size:12px;color:#666;">'
        '如需退订，请直接回复本邮件，或发送邮件至 '
        '<b>{}</b>，并注明退订类型：<br>'
        '{}'
        '管理员将在 1-2 个工作日内处理。'
        '</p>'
    ).format(reply_to, ''.join(unsubscribe_options))
    merged_body += unsubscribe_note
    return '<html><body>{}</body></html>'.format(merged_body)


def write_dry_run_html(mail_type, gitee_id, html_content):
    """
    Write generated HTML to local test_output directory when DRY_RUN is enabled.
    :param mail_type: any key of MAIL_TYPES ('pr', 'issue', 'docs_pr', 'docs_issue')
    :param gitee_id: user gitee_id
    :param html_content: HTML string to write
    """
    os.makedirs('test_output', exist_ok=True)
    output_path = os.path.join('test_output', '{}_{}.html'.format(mail_type, gitee_id))
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html_content)
    log.logger.info('[DRY RUN] Generated {}'.format(output_path))


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

def count_duration(start_time):
    """
    Count open days of a Pull Request by its start_time
    :param start_time: time when the Pull Request starts
    :return: duration in days
    """
    today = datetime.datetime.today()
    start_date = datetime.datetime.strptime(start_time, '%Y-%m-%d %H:%M:%S')
    duration = str((today - start_date).days)
    return duration


# ---------------------------------------------------------------------------
# Excel / HTML generation
# ---------------------------------------------------------------------------

# Anchor mark-up the row builders put into the link cells, e.g.
# "<a href='https://gitcode.com/<org>/<repo>/merge_requests/12'>#12</a>"
LINK_CELL_RE = re.compile(r"^<a href=['\"]([^'\"]+)['\"]>(.*)</a>$", re.DOTALL)


def linkify_cells(worksheet):
    """
    Convert anchor mark-up in cell values into real Excel hyperlinks.

    The row builders fill the number/title cells with "<a href='url'>text</a>" strings.
    Rendering those verbatim only ever worked because xlsx2html left cell text
    unescaped; from 0.6.4 on it escapes them, so the reports showed the raw mark-up.
    Storing the URL as a hyperlink and the text as the cell value instead makes both
    old and new xlsx2html export a clickable <a> tag (and the xlsx itself gets a
    working link).
    :param worksheet: openpyxl worksheet, converted in place
    """
    for row in worksheet.rows:
        for cell in row:
            if not isinstance(cell.value, str):
                continue
            match = LINK_CELL_RE.match(cell.value)
            if not match:
                continue
            cell.value = match.group(2)
            cell.hyperlink = Hyperlink(ref=cell.coordinate, target=match.group(1))


def csv_to_xlsx(filepath):
    """
    Convert a csv file to a xlsx file
    :param filepath: path of the csv file
    :return: path of the xlsx file
    """
    if not filepath.endswith('.csv'):
        return
    # sorting
    df = pd.read_csv(filepath, encoding='utf-8')
    df.to_csv(filepath, mode='w', index=False)

    csv_file = pd.read_csv(filepath, encoding='utf-8')
    xlsx_filepath = filepath.replace('.csv', '.xlsx')
    csv_file.to_excel(xlsx_filepath, sheet_name='open_pull_requests_statistics')
    if not os.path.exists(xlsx_filepath):
        log.logger.error('ERROR! Fail to generate {}'.format(xlsx_filepath))
        sys.exit(1)
    log.logger.info('Generate {}'.format(filepath.replace('.csv', '.xlsx')))
    return xlsx_filepath


def excel_optimization(filepath, compare_dict, is_issue=False, extra_header=None, extra_fills=None):
    """
    Adjust styles of the xlsx file
    :param filepath: path of the xlsx file
    :param compare_dict: a dict of every sig and its compare info
    :param is_issue: if True, skip the branch column (issues have no branch)
    :param extra_header: optional header of one extra trailing column (the docs report uses it to
                         show the documentation label state). Omitted means the layout is unchanged
    :param extra_fills: optional {cell value: fill color} applied to that extra column
    """
    if not filepath.endswith('.xlsx'):
        return
    html_file = filepath.replace('.xlsx', '.html')
    wb = openpyxl.load_workbook(filepath)
    ws = wb.active
    tmp_list = []
    for row in ws.rows:
        tmp_list.append(row[1].value)
    insert_rows = {}
    for i in tmp_list:
        if i not in insert_rows.keys():
            insert_row = tmp_list.index(i) + 1
            insert_rows[insert_row] = i
    # delete auxiliary column
    ws.delete_cols(1)
    ws.delete_cols(1)
    # insert rows
    duration_col = 5 if is_issue else 6
    max_col = duration_col + 1 if extra_header else duration_col
    headers = ['仓库', '编号', '标题', '状态', '开启天数'] if is_issue else \
        ['仓库', '目标分支', '编号', '标题', '状态', '开启天数']
    if extra_header:
        headers.append(extra_header)
    alignment_center = Alignment(horizontal='center', vertical='center')
    insert_count = 0
    for i in sorted(insert_rows.keys()):
        sig_name = insert_rows[i]
        i += insert_count

        ws.insert_rows(i)
        insert_count += 1
        ws['A' + str(i)] = sig_name
        ws['A' + str(i)].font = Font(name='黑体', size=20, bold=True)
        ws.merge_cells(start_row=i, end_row=i, start_column=1, end_column=max_col)
        ws['A' + str(i)].alignment = alignment_center

        compare_info = single_sig_compare(sig_name, compare_dict)
        ws.insert_rows(i + 1)
        insert_count += 1
        ws['A' + str(i + 1)] = compare_info
        ws['A' + str(i + 1)].font = Font(name='黑体', color='FF0000')
        ws['A' + str(i + 1)].alignment = alignment_center
        ws.merge_cells(start_row=i + 1, end_row=i + 1, start_column=1, end_column=max_col)

        ws.insert_rows(i + 2)
        insert_count += 1
        for idx, header in enumerate(headers):
            cell = ws[chr(65 + idx) + str(i + 2)]
            cell.value = header
            cell.font = Font(bold=True)
            cell.alignment = alignment_center

    # replace the original table header
    ws.insert_rows(5)
    for idx in range(len(headers)):
        col = chr(65 + idx)
        ws[col + '5'] = ws[col + '4'].value
    ws.delete_rows(4)
    # fill for the Duration column
    cells = ws.iter_rows(min_row=3, min_col=duration_col, max_col=duration_col)
    yellow_fill = PatternFill("solid", start_color='FFFF00')
    first_stage_fill = PatternFill('solid', start_color='FFDAB9')
    second_stage_fill = PatternFill('solid', start_color='FF7F50')
    third_stage_fill = PatternFill('solid', start_color='FF4500')
    for i in cells:
        try:
            value = int(i[0].value)
            if 7 < value <= 30:
                i[0].fill = first_stage_fill
            elif 30 < value <= 365:
                i[0].fill = second_stage_fill
            elif value > 365:
                i[0].fill = third_stage_fill
        except (TypeError, ValueError):
            pass
    # fill for the status mark
    status_col = 4 if is_issue else 5   # D for issue, E for PR
    status = ws.iter_rows(min_row=3, min_col=status_col, max_col=status_col)
    for j in status:
        value = j[0].value
        if not value:
            continue
        elif len(value) <= 3 and value != '草稿':
            continue
        else:
            j[0].fill = yellow_fill
    # fill for the extra column (e.g. the docs label state)
    if extra_fills:
        extra_cells = ws.iter_rows(min_row=3, min_col=max_col, max_col=max_col)
        for row in extra_cells:
            color = extra_fills.get(row[0].value)
            if color:
                row[0].fill = PatternFill('solid', start_color=color)
    # align center (duration column and, when present, the extra column)
    for row in ws.rows:
        for col in range(duration_col - 1, max_col):
            row[col].alignment = alignment_center
    # add borders
    border = Border(left=Side(border_style='thin', color='000000'),
                    right=Side(border_style='thin', color='000000'),
                    top=Side(border_style='thin', color='000000'),
                    bottom=Side(border_style='thin', color='000000'))
    for row in ws.rows:
        for cell in row:
            cell.border = border
    # last step on purpose: hyperlinks are keyed by cell coordinate, so they must be
    # attached after all the column/row surgery above has moved the data into place
    linkify_cells(ws)
    wb.save(filepath)
    wb.close()
    # generate html file by the xlsx file
    xlsx2html(filepath, html_file)
    log.logger.info('Generate {}'.format(html_file))


# ---------------------------------------------------------------------------
# Email sending
# ---------------------------------------------------------------------------

def send_email(xlsx_file, nickname, receivers, subject='openEuler 待处理PR汇总',
               body_text='以下是您参与openEuler社区的SIG仓库下待处理的PR，烦请您及时跟进',
               html_content=None):
    """
    Send email to reviewers
    :param xlsx_file: path of the xlsx file
    :param nickname: Gitee ID of the receiver
    :param receivers: where send to
    :param subject: email subject
    :param body_text: greeting text in email body
    :param html_content: optional HTML string; if provided, use it instead of reading from xlsx
    """
    username = os.getenv('email_username', '').strip()
    port = int(os.getenv('smtp_port', '465').strip())
    host = os.getenv('smtp_host', '').strip()
    password = os.getenv('email_password', '').strip()
    sender = os.getenv('email_sender', '').strip()
    reply_to = os.getenv('email_reply_to', 'huanglei227@h-partners.com').strip()
    msg = MIMEMultipart()
    if html_content is not None:
        body_of_email = html_content
    else:
        html_file = xlsx_file.replace('.xlsx', '.html')
        with open(html_file, 'r', encoding='utf-8') as f:
            body_of_email = f.read()
    body_of_email = body_of_email.replace('<body>', '<body><p>Dear {},</p>'
                                                    '<p>{}</p>'.
                                          format(nickname, body_text))
    content = MIMEText(body_of_email, 'html', 'utf-8')
    msg.attach(content)
    msg['Subject'] = subject
    msg['From'] = sender
    msg['To'] = ','.join(receivers)
    msg['Reply-To'] = reply_to
    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=120) as server:
                server.login(username, password)
                server.sendmail(sender, receivers, msg.as_string())
        else:
            with smtplib.SMTP(host, port, timeout=120) as server:
                server.starttls()
                server.login(username, password)
                server.sendmail(sender, receivers, msg.as_string())
        log.logger.info('Sent report email to: {}'.format(receivers))
    except smtplib.SMTPException as e:
        log.logger.error(e)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def fill_status(status, insert_string):
    """
    Change status of the Pull Request
    :param status: a string of current status
    :param insert_string: abnormal status waiting to add
    :return: status
    """
    if status == '待合入':
        status = insert_string
    else:
        status += '、{}'.format(insert_string)
    return status


def sort_rows_by_sig_duration(rows, duration_col):
    """
    Order report rows by sig name (ascending) and, inside each sig, by open days (descending).
    :param rows: report rows, the first column holds the sig name and duration_col holds open days
    :param duration_col: index of the open-days column
    :return: a new ordered list
    """
    return sorted(rows, key=(lambda r: (r[0], -(int(r[duration_col]) if r[duration_col] else 0))))


def clean_env(data_dir):
    """
    Remove the temporary data
    :param data_dir: directory waiting to clean
    """
    shutil.rmtree(data_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Processed-rate comparison
# ---------------------------------------------------------------------------

def cal_sig_processed_rate(sig_name, ts):
    """
    Calculate processed rate of Pull Requests of a sig between now and a week ago
    :param sig_name: sig name
    :param ts: timestamp
    :return: -1, 0 or a two bit float number
    """
    url = 'https://dsapi.osinfra.cn/query/sig/pr/state'
    params = {
        'community': 'openeuler',
        'timestamp': ts,
        'sig': sig_name
    }
    try:
        r = requests.get(url, params=params, timeout=30)
    except requests.exceptions.RequestException as e:
        log.logger.warning('Failed to query dsapi for sig {}: {}'.format(sig_name, e))
        return -1
    if r.status_code != 200:
        processed_rate = -1
    else:
        data = r.json()['data']
        if not data:
            return -1
        merged, closed, op = data['merged'], data['closed'], data['open']
        if merged == 0 and closed == 0 and op == 0:
            return 0
        processed_rate = round((merged + closed) / (merged + closed + op), 2)
    return processed_rate


def cal_compare_timestamp():
    """
    Calculate timestamp at 9:00 on the current day and timestamp a week ago
    :return: timestamp at 9:00 on the current day and timestamp a week ago
    """
    from datetime import datetime
    today = datetime.strftime(datetime.today(), '%Y-%m-%d')
    timestamp_today = int(time.mktime(datetime.strptime(today + ' 09', '%Y-%m-%d %H').timetuple())) * 1000
    timestamp_last = timestamp_today - 3600 * 24 * 7 * 1000
    return timestamp_today, timestamp_last


def all_sigs_compare(sigs_list, config=None):
    """
    Generate compare info of all sigs
    :param sigs_list: a name list of all sigs
    :param config: community config dict (defaults to the active community)
    :return: compare info of all sigs
    """
    config = config or load_community_config()
    if config.get('processed_rate') == 'none':
        return {sig: '' for sig in sigs_list}
    compare_dict = {}
    for sig in sigs_list:
        compare_info = compare_sig_processed_rate(sig)
        compare_dict[sig] = compare_info
    return compare_dict


def single_sig_compare(sig, compare_dict):
    """
    Return compare info of a sig
    :param sig: sig name
    :param compare_dict: a dict of every sig and its compare info
    :return: compare info of a sig
    """
    return compare_dict.get(sig)


def compare_sig_processed_rate(sig_name):
    """
    Compare processed rate of a sig
    :param sig_name: sig name
    :return: compare info
    """
    ts_today, ts_last = cal_compare_timestamp()
    processed_rate_now = cal_sig_processed_rate(sig_name, ts_today)
    processed_rate_last = cal_sig_processed_rate(sig_name, ts_last)
    if processed_rate_now == -1 or processed_rate_last == -1:
        return ""
    else:
        if processed_rate_now == processed_rate_last:
            return 'PR处理率为{}%, 同比上周不变'.format(processed_rate_now * 100)
        elif processed_rate_now > processed_rate_last:
            compare_rate = round(processed_rate_now - processed_rate_last, 2)
            return 'PR处理率为{}%, 同比上周上升{}%'.format(processed_rate_now * 100, compare_rate * 100)
        elif processed_rate_now < processed_rate_last:
            compare_rate = round(processed_rate_last - processed_rate_now, 2)
            return 'PR处理率为{}%, 同比上周下降{}%'.format(processed_rate_now * 100, compare_rate * 100)


# ---------------------------------------------------------------------------
# GitCode API data source
# ---------------------------------------------------------------------------

GITCODE_API_BASE = 'https://gitcode.com/api/v5'


def gitcode_fetch_repo_items(repo, kind, token):
    """
    Fetch all open items (pulls or issues) of one repo from the GitCode API, paginated.
    403/404 (private or missing repos) are logged as warnings and skipped.
    :param repo: full repo name, e.g. 'boostkit/community'
    :param kind: 'pulls' or 'issues'
    :param token: GitCode access token
    :return: list of raw API items
    """
    items = []
    page = 1
    while True:
        url = '{}/repos/{}/{}'.format(GITCODE_API_BASE, repo, kind)
        params = {'state': 'open', 'per_page': 100, 'page': page, 'access_token': token}
        try:
            r = requests.get(url, params=params, timeout=30)
        except requests.exceptions.RequestException as e:
            log.logger.warning('Failed to get {} of {}: {}'.format(kind, repo, e))
            return items
        if r.status_code in (403, 404):
            log.logger.warning('Skip {} {}: HTTP {} (private or not found)'.format(kind, repo, r.status_code))
            return items
        if r.status_code != 200:
            log.logger.error('Fail to get {} of {}: HTTP {}'.format(kind, repo, r.status_code))
            return items
        data = r.json()
        items += data
        if len(data) < 100:
            break
        page += 1
    return items


def _iso_to_datetime_str(ts):
    """Convert ISO 8601 time ('2026-08-14T14:39:27+08:00') to '%Y-%m-%d %H:%M:%S'."""
    return datetime.datetime.fromisoformat(ts).replace(tzinfo=None).strftime('%Y-%m-%d %H:%M:%S')


def adapt_gitcode_pr(item):
    """Adapt a GitCode API pull request to the internal pull format used by ipb data."""
    return {
        'title': item['title'],
        'link': item['html_url'],
        'created_at': _iso_to_datetime_str(item['created_at']),
        'draft': bool(item.get('draft')),
        'labels': ','.join(label.get('name', '') for label in item.get('labels') or []),
        'ref': (item.get('base') or {}).get('ref') or '-',
        'mergeable': bool(item.get('mergeable')),
    }


def adapt_gitcode_issue(item):
    """Adapt a GitCode API issue to the internal issue format used by ipb data."""
    assignees = item.get('assignees') or []
    return {
        'title': item['title'],
        'link': item['html_url'],
        'created_at': _iso_to_datetime_str(item['created_at']),
        'issue_type': item.get('issue_type') or '',
        'issue_state': item.get('issue_state') or '',
        'assignee': ','.join(a.get('login', '') for a in assignees),
    }


def gitcode_open_items(sigs, kind):
    """
    Fetch open pulls/issues for every repo of every sig via the GitCode API.
    :param sigs: sigs list from get_sigs()
    :param kind: 'pulls' or 'issues'
    :return: mapping {org/repo/.../number: adapted_item}, keyed like the ipb source
    """
    token = os.getenv('GITCODE_TOKEN', '').strip()
    if not token:
        log.logger.error('ERROR! GITCODE_TOKEN is required for the gitcode_api data source, exit...')
        sys.exit(1)
    adapter = adapt_gitcode_pr if kind == 'pulls' else adapt_gitcode_issue
    repos = sorted({repo for sig in sigs for repo in sig['repositories']})
    log.logger.info('Fetching {} for {} repos via GitCode API'.format(kind, len(repos)))
    mapping = {}
    for repo in repos:
        for item in gitcode_fetch_repo_items(repo, kind, token):
            adapted = adapter(item)
            mapping[adapted['link'].split('/', 3)[3]] = adapted
    log.logger.info('Got {} open {} via GitCode API'.format(len(mapping), kind))
    return mapping
