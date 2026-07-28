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
        self.logger = logging.getLogger(filename)
        format_str = logging.Formatter(fmt)
        self.logger.setLevel(self.level_relations.get(level))
        sh = logging.StreamHandler()
        sh.setFormatter(format_str)
        th = handlers.TimedRotatingFileHandler(filename=filename, when=when, backupCount=backCount, encoding='utf-8')
        th.setFormatter(format_str)
        self.logger.addHandler(sh)
        self.logger.addHandler(th)


log = Logger('statistics.log', level='debug')


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------

def prepare_env():
    """
    Prepare repository and directory
    """
    log.logger.info('=' * 25 + ' PREPARE ENVIRONMENT ' + '=' * 25)
    if os.path.exists('community'):
        shutil.rmtree('community')
    subprocess.run(['git', 'clone', 'https://gitcode.com/openeuler/community.git'], check=True)  # nosec B603 B607
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

def get_sigs():
    """
    Get relationship between sigs and repositories
    """
    log.logger.info('=' * 25 + ' GET SIGS INFO ' + '=' * 25)
    sig_path = os.path.join('community', 'sig')
    sigs = []
    sigs_list = []
    for i in sorted(os.listdir(sig_path)):
        if i in ['README.md', 'sig-template', 'sig-recycle', 'create_sig_info_template.py']:
            continue
        if i not in [x['name'] for x in sigs]:
            sigs.append({'name': i, 'repositories': []})
            sigs_list.append(i)
        if 'openeuler' in os.listdir(os.path.join(sig_path, i)):
            for filesdir, _, repos in os.walk(os.path.join(sig_path, i, 'openeuler')):
                for repo in repos:
                    for sig in sigs:
                        if sig['name'] == i:
                            repositories = sig['repositories']
                            repositories.append(os.path.join('openeuler', repo.split('.yaml')[0]))
        if 'src-openeuler' in os.listdir(os.path.join(sig_path, i)):
            for filesdir, _, src_repos in os.walk(os.path.join(sig_path, i, 'src-openeuler')):
                for src_repo in src_repos:
                    for sig in sigs:
                        if sig['name'] == i:
                            repositories = sig['repositories']
                            repositories.append(os.path.join('src-openeuler', src_repo.split('.yaml')[0]))
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

def create_email_mappings():
    """
    Generate mappings between gitee_id and email addresses
    """
    email_mappings = {}
    if not os.path.exists('community'):
        subprocess.run(['git', 'clone', 'https://gitcode.com/openeuler/community.git'], check=True)  # nosec B603 B607
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

def expand_controls(config):
    """
    Expand simplified control syntax into full nested structure.
    :param config: raw control config for a single user (dict, bool or None)
    :return: dict with keys 'pr' and 'issue', each containing 'maintainer' and 'committer'
    """
    result = {
        'pr': {'maintainer': True, 'committer': True},
        'issue': {'maintainer': True, 'committer': True},
    }
    if config is False or config is None:
        return result
    if isinstance(config, dict) and config.get('all') is False:
        result['pr']['maintainer'] = False
        result['pr']['committer'] = False
        result['issue']['maintainer'] = False
        result['issue']['committer'] = False
        return result
    if not isinstance(config, dict):
        return result
    for mail_type in ('pr', 'issue'):
        if mail_type not in config:
            continue
        value = config[mail_type]
        if value is False:
            result[mail_type]['maintainer'] = False
            result[mail_type]['committer'] = False
        elif isinstance(value, dict):
            for role in ('maintainer', 'committer'):
                if role in value:
                    result[mail_type][role] = bool(value[role])
    return result


def load_email_controls(path=None):
    """
    Load email control preferences from email_controls.yaml.
    :param path: optional path to control file, defaults to EMAIL_CONTROLS_PATH env var or 'email_controls.yaml'
    :return: nested dict controls[gitee_id][mail_type][role] = bool
    """
    if path is None:
        path = os.getenv('EMAIL_CONTROLS_PATH', 'email_controls.yaml')
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
        controls[gitee_id] = expand_controls(config)
    log.logger.info('Loaded email controls from {}'.format(path))
    return controls


def should_send(controls, gitee_id, mail_type, role):
    """
    Check whether a user should receive a specific part of emails.
    :param controls: controls dict from load_email_controls()
    :param gitee_id: user gitee_id
    :param mail_type: 'pr' or 'issue'
    :param role: 'maintainer' or 'committer'
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
    if len(bodies) == 1:
        merged_body = bodies[0][1]
    else:
        merged_body = ''
        for title, body in bodies:
            merged_body += '<h3 style="margin-top:30px">{}</h3>\n{}'.format(title, body)
    reply_to = os.getenv('email_reply_to', 'huanglei227@h-partners.com').strip()
    unsubscribe_note = (
        '<p style="font-size:12px;color:#666;">'
        '如需退订，请直接回复本邮件，或发送邮件至 '
        '<b>{}</b>，并注明退订类型：<br>'
        '• 退订 PR 汇总<br>'
        '• 退订 Issue 汇总<br>'
        '• 只退订作为 Maintainer 的部分<br>'
        '• 只退订作为 Committer 的部分<br>'
        '• 完全退订所有邮件<br><br>'
        '管理员将在 1-2 个工作日内处理。'
        '</p>'
    ).format(reply_to)
    merged_body += unsubscribe_note
    return '<html><body>{}</body></html>'.format(merged_body)


def write_dry_run_html(mail_type, gitee_id, html_content):
    """
    Write generated HTML to local test_output directory when DRY_RUN is enabled.
    :param mail_type: 'pr' or 'issue'
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


def excel_optimization(filepath, compare_dict, is_issue=False):
    """
    Adjust styles of the xlsx file
    :param filepath: path of the xlsx file
    :param compare_dict: a dict of every sig and its compare info
    :param is_issue: if True, skip the branch column (issues have no branch)
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
    max_col = 5 if is_issue else 6
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
        if is_issue:
            ws['A' + str(i + 2)] = '仓库'
            ws['B' + str(i + 2)] = '编号'
            ws['C' + str(i + 2)] = '标题'
            ws['D' + str(i + 2)] = '状态'
            ws['E' + str(i + 2)] = '开启天数'
            for col_letter in ('A', 'B', 'C', 'D', 'E'):
                ws[col_letter + str(i + 2)].font = Font(bold=True)
                ws[col_letter + str(i + 2)].alignment = alignment_center
        else:
            ws['A' + str(i + 2)] = '仓库'
            ws['B' + str(i + 2)] = '目标分支'
            ws['C' + str(i + 2)] = '编号'
            ws['D' + str(i + 2)] = '标题'
            ws['E' + str(i + 2)] = '状态'
            ws['F' + str(i + 2)] = '开启天数'
            for col_letter in ('A', 'B', 'C', 'D', 'E', 'F'):
                ws[col_letter + str(i + 2)].font = Font(bold=True)
                ws[col_letter + str(i + 2)].alignment = alignment_center

    # replace the original table header
    ws.insert_rows(5)
    col_letters = ('A', 'B', 'C', 'D', 'E', 'F')
    for idx, col in enumerate(col_letters):
        if is_issue and col == 'F':
            break
        ws[col + '5'] = ws[col + '4'].value
    ws.delete_rows(4)
    # fill for the Duration (last column)
    cells = ws.iter_rows(min_row=3, min_col=max_col, max_col=max_col)
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
    # align center (last column = duration)
    for row in ws.rows:
        row[max_col - 1].alignment = alignment_center
    # add borders
    border = Border(left=Side(border_style='thin', color='000000'),
                    right=Side(border_style='thin', color='000000'),
                    top=Side(border_style='thin', color='000000'),
                    bottom=Side(border_style='thin', color='000000'))
    for row in ws.rows:
        for cell in row:
            cell.border = border
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
    # Fix xlsx2html escaping: restore <a> links so they render as clickable in email
    body_of_email = re.sub(r'&lt;a (.+?)&gt;(.+?)&lt;/a&gt;', r'<a \1>\2</a>', body_of_email)
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


def all_sigs_compare(sigs_list):
    """
    Generate compare info of all sigs
    :param sigs_list: a name list of all sigs
    :return: compare info of all sigs
    """
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
