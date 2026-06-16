#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PR statistics report — fetches open PRs from ipb.osinfra.cn, groups by reviewer (maintainer/committer),
generates XLSX/HTML reports, and sends per-reviewer emails.
"""

import codecs
import csv
import os
import yaml
from common import *


# ---------------------------------------------------------------------------
# PR data fetching
# ---------------------------------------------------------------------------

def get_repos_pulls_mapping():
    """
    Get mappings between repos and pulls
    :return: a dict of {repo: pulls}
    """
    enterprise_pulls = []
    page = 1
    while True:
        log.logger.info("=" * 25 + " GET ENTERPRISE PULLS: PAGE {} ".format(page) + "=" * 25)
        url = 'https://ipb.osinfra.cn/pulls'
        params = {
            'state': 'open',
            'direction': 'asc',
            'page': page,
            'per_page': 100
        }
        r = requests.get(url, params=params, timeout=30)
        if r.status_code != 200:
            log.logger.error('Fail to get enterprise pulls list.')
            return
        else:
            enterprise_pulls += r.json()['data']
        if len(r.json()['data']) < 100:
            break
        page += 1
    return {x['link'].split('/', 3)[3]: x for x in enterprise_pulls}


# ---------------------------------------------------------------------------
# PR statistics
# ---------------------------------------------------------------------------

def pr_statistics(data_dir, sigs, repos_pulls_mapping, compare_dict, whitelist, whitelist_active=False):
    """
    :param data_dir: directory to store temporary data
    :param sigs: a dict of every sig and its repositories
    :param repos_pulls_mapping: mappings between repos and pulls
    :param compare_dict: a dict of every sig and its compare info
    :param whitelist: a list of gitee_ids allowed to receive emails
    :param whitelist_active: whether whitelist filtering is enabled
    """
    log.logger.info('=' * 25 + ' STATISTICS ' + '=' * 25)
    test_email = os.getenv('test_reviever_email', '').strip()
    test_mode = bool(test_email)
    if test_mode:
        log.logger.info('[TEST MODE] All emails will be sent to {}, max 3 emails'.format(test_email))
    MAX_EMAILS = 3
    email_mappings = get_email_mappings()
    mapping_lists = sorted(list(email_mappings.keys()))
    maintainer_pr_dict = {}
    committer_pr_dict = {}
    open_pr_info = []
    maintainer_set = set()
    for sig in sigs:
        sig_name = sig['name']
        if sig_name == 'Kernel':
            log.logger.info('Skipping Kernel SIG (handled by hulk_robot_test)')
            continue
        sig_repos = sig['repositories']
        log.logger.info('\nStarting to search sig {}'.format(sig_name))
        if not sig_repos:
            log.logger.info('Find no repositories in sig {}, skip'.format(sig_name))
            continue
        maintainers, sig_info_mark = get_maintainers(sig_name)
        for m in maintainers:
            maintainer_set.add(m)
        for full_repo in sig_repos:
            if full_repo.split('/')[0] not in ['src-openeuler', 'openeuler']:
                continue
            open_pr_list = []
            for mapping_key in repos_pulls_mapping.keys():
                if mapping_key.startswith(full_repo + '/'):
                    open_pr_list.append(repos_pulls_mapping[mapping_key])
                    log.logger.info('Find open pr: {}'.format(mapping_key))
            if not open_pr_list:
                continue
            members = maintainers
            if sig_info_mark:
                committers_mapping = get_committers_mapping(sig_name)
                members = get_repo_members(maintainers, committers_mapping, full_repo)
            for item in open_pr_list:
                title = item['title']
                html_url = item['link']
                number = '#' + html_url.split('/')[-1]
                created_at = item['created_at']
                draft = item['draft']
                labels = item['labels'].split(',')
                ref_branch = item.get('ref') or '-'
                status = '待合入'
                if draft:
                    status = fill_status(status, '草稿')
                if 'openeuler-cla/yes' not in labels:
                    status = fill_status(status, 'CLA认证失败')
                if 'ci_failed' in labels:
                    status = fill_status(status, '门禁检查失败')
                if not item['mergeable']:
                    status = fill_status(status, '存在冲突')
                if 'kind/wait_for_update' in labels:
                    status = fill_status(status, '等待更新')
                duration = count_duration(created_at)
                link = "<a href='{0}'>{1}</a>".format(html_url, title)
                number_link = "<a href='{0}'>{1}</a>".format(html_url, number)
                if sig_info_mark:
                    pr_committers = committers_mapping.get(full_repo, [])
                else:
                    pr_committers = []
                open_pr_info.append([sig_name, full_repo, ref_branch, number_link, link, status, duration,
                                     ','.join(maintainers), ','.join(pr_committers)])
    no_addresses_id = []
    for pr_info in open_pr_info:
        pr_row = pr_info[:7]               # [sig_name, repo, branch, number_link, link, status, duration]
        maintainer_ids = pr_info[7]          # SIG maintainers
        committer_ids = pr_info[8]           # repo committers (raw)
        for mid in maintainer_ids.split(','):
            if not mid:
                continue
            if mid not in mapping_lists:
                if mid not in no_addresses_id:
                    log.logger.warning('WARNING! gitee_id {} does not match any email address.'.format(mid))
                    no_addresses_id.append(mid)
            if mid not in maintainer_pr_dict:
                maintainer_pr_dict[mid] = [pr_row]
            else:
                maintainer_pr_dict[mid].append(pr_row)
        for cid in committer_ids.split(','):
            if not cid:
                continue
            if cid not in mapping_lists:
                if cid not in no_addresses_id:
                    log.logger.warning('WARNING! gitee_id {} does not match any email address.'.format(cid))
                    no_addresses_id.append(cid)
            if cid not in committer_pr_dict:
                committer_pr_dict[cid] = [pr_row]
            else:
                committer_pr_dict[cid].append(pr_row)

    # Move committer entries for whitelisted maintainers into a separate dict,
    # so they get one email with two tables instead of two emails.
    # Non-whitelisted maintainers' committer entries stay in committer_pr_dict
    # and are sent via the committer loop instead.
    committer_extras = {}
    for cid in list(committer_pr_dict.keys()):
        if cid in maintainer_pr_dict:
            if not whitelist_active or cid in whitelist:
                committer_extras[cid] = committer_pr_dict[cid]
                del committer_pr_dict[cid]

    email_sent_count = 0

    def write_csv_and_html(pr_list, csv_path, compare_dict):
        """Write PR list to CSV, convert to XLSX/HTML, return HTML file path"""
        f = codecs.open(csv_path, 'w', encoding='utf-8')
        writer = csv.writer(f)
        for row in pr_list:
            writer.writerow(row)
        f.close()
        xlsx_path = csv_to_xlsx(csv_path)
        excel_optimization(xlsx_path, compare_dict)
        return xlsx_path.replace('.xlsx', '.html')

    def send_pr_email(pr_list, receiver, role, compare_dict):
        nonlocal email_sent_count
        if not test_mode and role == 'maintainer' and whitelist_active and receiver not in whitelist:
            log.logger.info('Maintainer {} not in whitelist, skipping email'.format(receiver))
            return False
        if not pr_list:
            return False
        email_address = email_mappings.get(receiver)
        if not email_address:
            log.logger.warning('Ready to send {} statistics for {} but cannot find the email address'.format(role, receiver))
            return False
        ordered = sorted(pr_list, key=(lambda x: int(x[6])), reverse=True)
        ordered_pr_list = []
        pr_sigs = sorted(set([x[0] for x in ordered]))
        for pr_sig in pr_sigs:
            for op in ordered:
                if op[0] == pr_sig:
                    if len(ordered_pr_list) > 0 and op[0] == ordered_pr_list[-1][0] and int(op[-1]) > \
                            int(ordered_pr_list[-1][-1]):
                        ordered_pr_list.insert(-1, op)
                    else:
                        ordered_pr_list.append(op)
        csv_path = '{}/statistics_{}_{}.csv'.format(data_dir, receiver, role)
        html_path = write_csv_and_html(ordered_pr_list, csv_path, compare_dict)
        log.logger.info('Ready to send {} statistics for {} whose email address is {}'.format(role, receiver, email_address))
        return html_path

    for receiver in sorted(maintainer_pr_dict.keys()):
        if test_mode:
            if email_sent_count >= MAX_EMAILS:
                break
        elif whitelist_active and receiver not in whitelist:
            log.logger.info('Maintainer {} not in whitelist, skipping email'.format(receiver))
            continue
        html_m = send_pr_email(maintainer_pr_dict[receiver], receiver, 'maintainer', compare_dict)
        if not html_m:
            continue
        email_address = email_mappings.get(receiver)
        # Check for committer extras
        extra_list = committer_extras.get(receiver, [])
        html_c = send_pr_email(extra_list, receiver, 'committer', compare_dict)
        if html_c:
            # Merge maintainer + committer HTML into one email
            with open(html_m, 'r', encoding='utf-8') as f:
                body_m = f.read()
            with open(html_c, 'r', encoding='utf-8') as f:
                body_c = f.read()
            body_combined = body_m.replace('</body>',
                                           '<h3 style="margin-top:30px">作为 Committer 的 PR</h3>' + body_c.split('<body>')[1].split('</body>')[0] + '</body>')
            with open(html_m, 'w', encoding='utf-8') as f:
                f.write(body_combined)
        actual_receivers = [test_email] if test_mode else [email_address]
        send_email(html_m.replace('.html', '.xlsx'), receiver, actual_receivers,
                   'openEuler 待处理PR汇总')
        email_sent_count += 1
        if test_mode:
            log.logger.info('[TEST MODE] Email {} of {} sent to {}'.format(email_sent_count, MAX_EMAILS, test_email))
        else:
            log.logger.info('Email {} sent to {}'.format(email_sent_count, email_address))

    for receiver in sorted(committer_pr_dict.keys()):
        if test_mode:
            if email_sent_count >= MAX_EMAILS:
                break
        html_c = send_pr_email(committer_pr_dict[receiver], receiver, 'committer', compare_dict)
        if not html_c:
            continue
        email_address = email_mappings.get(receiver)
        actual_receivers = [test_email] if test_mode else [email_address]
        send_email(html_c.replace('.html', '.xlsx'), receiver, actual_receivers,
                   'openEuler 待处理PR汇总（Committer）')
        email_sent_count += 1
        if test_mode:
            log.logger.info('[TEST MODE] Email {} of {} sent to {}'.format(email_sent_count, MAX_EMAILS, test_email))
        else:
            log.logger.info('Email {} sent to {}'.format(email_sent_count, email_address))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    """
    main function
    """
    data_dir = prepare_env()
    sigs, sigs_list = get_sigs()
    compare_dict = all_sigs_compare(sigs_list)
    print('Compare Dict: {}'.format(compare_dict))
    repos_pulls_mapping = get_repos_pulls_mapping()
    whitelist_active = os.path.exists('email_whitelist.yaml')
    whitelist = []
    if whitelist_active:
        whitelist = yaml.safe_load(open('email_whitelist.yaml', 'r').read()) or []
    pr_statistics(data_dir, sigs, repos_pulls_mapping, compare_dict, whitelist, whitelist_active)


if __name__ == '__main__':
    main()
