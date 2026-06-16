#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Issue statistics report — fetches open issues from ipb.osinfra.cn, groups by reviewer
(maintainer/committer), generates XLSX/HTML reports, and sends per-reviewer emails.
"""

import codecs
import csv
import os
import yaml
from common import *


# ---------------------------------------------------------------------------
# Issue data fetching
# ---------------------------------------------------------------------------

def get_repos_issues_mapping():
    """
    Get mappings between repos and issues
    :return: a dict of {repo: issue_data}
    """
    enterprise_issues = []
    page = 1
    while True:
        log.logger.info("=" * 25 + " GET ENTERPRISE ISSUES: PAGE {} ".format(page) + "=" * 25)
        url = 'https://ipb.osinfra.cn/issues'
        params = {
            'state': 'open',
            'direction': 'asc',
            'page': page,
            'per_page': 100
        }
        r = requests.get(url, params=params)
        if r.status_code != 200:
            log.logger.error('Fail to get enterprise issues list.')
            return
        else:
            enterprise_issues += r.json()['data']
        if len(r.json()['data']) < 100:
            break
        page += 1
    return {x['link'].split('/', 3)[3]: x for x in enterprise_issues}


# ---------------------------------------------------------------------------
# Issue statistics
# ---------------------------------------------------------------------------

def issue_statistics(data_dir, sigs, repos_issues_mapping, compare_dict, whitelist, whitelist_active=False):
    """
    :param data_dir: directory to store temporary data
    :param sigs: a dict of every sig and its repositories
    :param repos_issues_mapping: mappings between repos and issues
    :param compare_dict: a dict of every sig and its compare info
    :param whitelist: a list of gitee_ids allowed to receive emails
    :param whitelist_active: whether whitelist filtering is enabled
    """
    log.logger.info('=' * 25 + ' ISSUE STATISTICS ' + '=' * 25)
    email_mappings = get_email_mappings()
    mapping_lists = sorted(list(email_mappings.keys()))
    maintainer_issue_dict = {}
    committer_issue_dict = {}
    open_issue_info = []
    maintainer_set = set()
    for sig in sigs:
        sig_name = sig['name']
        if sig_name == 'Kernel':
            log.logger.info('Skipping Kernel SIG (handled by hulk_robot_test)')
            continue
        sig_repos = sig['repositories']
        log.logger.info('\nStarting to search sig {} for issues'.format(sig_name))
        if not sig_repos:
            continue
        maintainers, sig_info_mark = get_maintainers(sig_name)
        for m in maintainers:
            maintainer_set.add(m)
        for full_repo in sig_repos:
            if full_repo.split('/')[0] not in ['src-openeuler', 'openeuler']:
                continue
            open_issue_list = []
            for mapping_key in repos_issues_mapping.keys():
                if mapping_key.startswith(full_repo + '/'):
                    open_issue_list.append(repos_issues_mapping[mapping_key])
                    log.logger.info('Find open issue: {}'.format(mapping_key))
            if not open_issue_list:
                continue
            members = maintainers
            if sig_info_mark:
                committers_mapping = get_committers_mapping(sig_name)
                members = get_repo_members(maintainers, committers_mapping, full_repo)
            for item in open_issue_list:
                title = item['title']
                html_url = item['link']
                number = '#' + html_url.split('/')[-1]
                created_at = item['created_at']
                issue_type = item.get('issue_type', '')
                issue_state = item.get('issue_state', '')
                assignee = item.get('assignee', '') or ''
                duration = count_duration(created_at)
                link = "<a href='{0}'>{1}</a>".format(html_url, title)
                number_link = "<a href='{0}'>{1}</a>".format(html_url, number)
                if sig_info_mark:
                    issue_committers = committers_mapping.get(full_repo, [])
                else:
                    issue_committers = []
                # Merge issue_type/state/assignee into status column
                status = issue_type
                if issue_state:
                    status = '{} / {}'.format(status, issue_state) if status else issue_state
                if assignee:
                    status = '{} / {}'.format(status, assignee) if status else assignee
                # 6-column format: no branch column for issues
                open_issue_info.append([sig_name, full_repo, number_link, link, status, duration,
                                        ','.join(maintainers), ','.join(issue_committers)])
    no_addresses_id = []
    for issue_info in open_issue_info:
        issue_row = issue_info[:6]              # [sig_name, repo, number, title, status, duration]
        maintainer_ids = issue_info[6]           # SIG maintainers
        committer_ids = issue_info[7]            # repo committers (raw)
        for mid in maintainer_ids.split(','):
            if not mid:
                continue
            if mid not in mapping_lists:
                if mid not in no_addresses_id:
                    log.logger.warning('WARNING! gitee_id {} does not match any email address.'.format(mid))
                    no_addresses_id.append(mid)
            if mid not in maintainer_issue_dict:
                maintainer_issue_dict[mid] = [issue_row]
            else:
                maintainer_issue_dict[mid].append(issue_row)
        for cid in committer_ids.split(','):
            if not cid:
                continue
            if cid not in mapping_lists:
                if cid not in no_addresses_id:
                    log.logger.warning('WARNING! gitee_id {} does not match any email address.'.format(cid))
                    no_addresses_id.append(cid)
            if cid not in committer_issue_dict:
                committer_issue_dict[cid] = [issue_row]
            else:
                committer_issue_dict[cid].append(issue_row)

    committer_extras = {}
    for cid in list(committer_issue_dict.keys()):
        if cid in maintainer_issue_dict:
            committer_extras[cid] = committer_issue_dict[cid]
            del committer_issue_dict[cid]

    email_sent_count = 0

    def write_csv_and_html(issue_list, csv_path, compare_dict):
        f = codecs.open(csv_path, 'w', encoding='utf-8')
        writer = csv.writer(f)
        for row in issue_list:
            writer.writerow(row)
        f.close()
        xlsx_path = csv_to_xlsx(csv_path)
        excel_optimization(xlsx_path, compare_dict, is_issue=True)
        return xlsx_path.replace('.xlsx', '.html')

    def send_issue_email(issue_list, receiver, role, compare_dict):
        nonlocal email_sent_count
        if whitelist_active and receiver not in whitelist:
            log.logger.info('Receiver {} not in whitelist, skipping issue email'.format(receiver))
            return False
        if not issue_list:
            return False
        email_address = email_mappings.get(receiver)
        if not email_address:
            log.logger.warning('Ready to send {} issue stats for {} but cannot find email'.format(role, receiver))
            return False
        ordered = sorted(issue_list, key=(lambda x: int(x[5]) if x[5] else 0), reverse=True)
        ordered_issue_list = []
        issue_sigs = sorted(set([x[0] for x in ordered]))
        for issue_sig in issue_sigs:
            for op in ordered:
                if op[0] == issue_sig:
                    if len(ordered_issue_list) > 0 and op[0] == ordered_issue_list[-1][0] and int(op[-1]) > \
                            int(ordered_issue_list[-1][-1]):
                        ordered_issue_list.insert(-1, op)
                    else:
                        ordered_issue_list.append(op)
        csv_path = '{}/issue_statistics_{}_{}.csv'.format(data_dir, receiver, role)
        html_path = write_csv_and_html(ordered_issue_list, csv_path, compare_dict)
        log.logger.info('Ready to send {} issue stats for {}: {}'.format(role, receiver, email_address))
        return html_path

    for receiver in sorted(maintainer_issue_dict.keys()):
        if whitelist_active and receiver not in whitelist:
            log.logger.info('Maintainer {} not in whitelist, skipping email'.format(receiver))
            continue
        html_m = send_issue_email(maintainer_issue_dict[receiver], receiver, 'maintainer', compare_dict)
        if not html_m:
            continue
        email_address = email_mappings.get(receiver)
        extra_list = committer_extras.get(receiver, [])
        html_c = send_issue_email(extra_list, receiver, 'committer', compare_dict)
        if html_c:
            with open(html_m, 'r', encoding='utf-8') as f:
                body_m = f.read()
            with open(html_c, 'r', encoding='utf-8') as f:
                body_c = f.read()
            body_combined = body_m.replace('</body>',
                                           '<h3 style="margin-top:30px">作为 Committer 的 Issue</h3>' + body_c.split('<body>')[1].split('</body>')[0] + '</body>')
            with open(html_m, 'w', encoding='utf-8') as f:
                f.write(body_combined)
        send_email(html_m.replace('.html', '.xlsx'), receiver, [email_address],
                   'openEuler 待处理Issue汇总',
                   body_text='以下是您参与openEuler社区的SIG仓库下待处理的Issue，烦请您及时跟进')
        email_sent_count += 1
        log.logger.info('Issue email {} of 3 sent'.format(email_sent_count))

    for receiver in sorted(committer_issue_dict.keys()):
        html_c = send_issue_email(committer_issue_dict[receiver], receiver, 'committer', compare_dict)
        if not html_c:
            continue
        email_address = email_mappings.get(receiver)
        send_email(html_c.replace('.html', '.xlsx'), receiver, [email_address],
                   'openEuler 待处理Issue汇总（Committer）',
                   body_text='以下是您参与openEuler社区的SIG仓库下待处理的Issue，烦请您及时跟进')
        email_sent_count += 1
        log.logger.info('Issue email {} of 3 sent'.format(email_sent_count))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    """
    main function for issue statistics
    """
    data_dir = prepare_env()
    sigs, sigs_list = get_sigs()
    compare_dict = all_sigs_compare(sigs_list)
    print('Compare Dict: {}'.format(compare_dict))
    repos_issues_mapping = get_repos_issues_mapping()
    whitelist_active = os.path.exists('email_whitelist.yaml')
    whitelist = []
    if whitelist_active:
        whitelist = yaml.safe_load(open('email_whitelist.yaml', 'r').read()) or []
    issue_statistics(data_dir, sigs, repos_issues_mapping, compare_dict, whitelist, whitelist_active)


if __name__ == '__main__':
    main()
