"""
Tests for issue_statistics.py — Issue data fetching, statistics, and main entry point.
"""
import datetime
import os
from unittest.mock import MagicMock, patch, ANY

import pytest

from issue_statistics import get_repos_issues_mapping, main, issue_statistics


# ---------------------------------------------------------------------------
# get_repos_issues_mapping
# ---------------------------------------------------------------------------

class TestGetReposIssuesMapping:
    def test_success_single_page(self, monkeypatch):
        """Single page of issue results (< 100 items)."""
        mock_data = {
            'data': [
                {
                    'link': 'https://gitcode.com/openeuler/ai-framework/issues/200',
                    'title': 'Issue Title',
                    'issue_type': '缺陷',
                    'issue_state': '待确认',
                    'assignee': '',
                    'created_at': '2026-01-01 00:00:00',
                }
            ]
        }
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = mock_data
        monkeypatch.setattr('issue_statistics.requests.get', lambda url, params, timeout: mock_resp)

        result = get_repos_issues_mapping()
        assert 'openeuler/ai-framework/issues/200' in result
        item = result['openeuler/ai-framework/issues/200']
        assert item['title'] == 'Issue Title'
        assert item['issue_type'] == '缺陷'
        assert item['issue_state'] == '待确认'

    def test_pagination(self, monkeypatch):
        """Multiple pages until < 100 items."""
        page1 = {'data': [{'link': 'https://gitcode.com/openeuler/r/issues/{}'.format(i),
                           'title': 'I{}'.format(i)} for i in range(100)]}
        page2 = {'data': [{'link': 'https://gitcode.com/openeuler/r/issues/300',
                           'title': 'I300'}]}

        responses = iter([page1, page2])
        def mock_get(url, params, timeout):
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = next(responses)
            return resp

        monkeypatch.setattr('issue_statistics.requests.get', mock_get)
        result = get_repos_issues_mapping()
        assert len(result) == 101

    def test_api_failure(self, monkeypatch):
        """Non-200 status returns None."""
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        monkeypatch.setattr('issue_statistics.requests.get', lambda url, params, timeout: mock_resp)
        assert get_repos_issues_mapping() is None

    def test_empty_result(self, monkeypatch):
        """No open issues returns empty dict."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {'data': []}
        monkeypatch.setattr('issue_statistics.requests.get', lambda url, params, timeout: mock_resp)
        assert get_repos_issues_mapping() == {}

    def test_link_splitting(self, monkeypatch):
        """Verify link parsing produces correct repo key."""
        mock_data = {
            'data': [
                {
                    'link': 'https://gitcode.com/src-openeuler/tools/issues/500',
                    'title': 'Test',
                }
            ]
        }
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = mock_data
        monkeypatch.setattr('issue_statistics.requests.get', lambda url, params, timeout: mock_resp)
        result = get_repos_issues_mapping()
        assert 'src-openeuler/tools/issues/500' in result


# ---------------------------------------------------------------------------
# issue_statistics (core logic)
# ---------------------------------------------------------------------------

class TestIssueStatistics:
    def _make_issue_item(self, link, title='Test Issue', days_ago=5,
                          issue_type='缺陷', issue_state='待确认', assignee=''):
        """Helper to build an issue dict."""
        created = (datetime.datetime.now() - datetime.timedelta(days=days_ago)).strftime(
            '%Y-%m-%d %H:%M:%S')
        return {
            'title': title,
            'link': link,
            'created_at': created,
            'issue_type': issue_type,
            'issue_state': issue_state,
            'assignee': assignee,
        }

    def test_basic_flow(self, tmp_path, monkeypatch,
                        sigs_sample, repos_issues_mapping_sample, compare_dict_sample):
        """End-to-end issue_statistics sends emails."""
        monkeypatch.setattr('issue_statistics.get_email_mappings',
                            lambda: {'maintainer1': 'm1@e.com'})
        monkeypatch.setattr('issue_statistics.get_maintainers',
                            lambda sig: (['maintainer1'], True))
        monkeypatch.setattr('issue_statistics.get_committers_mapping',
                            lambda sig: {})

        monkeypatch.setattr('issue_statistics.csv_to_xlsx',
                            lambda c: c.replace('.csv', '.xlsx'))
        monkeypatch.setattr('issue_statistics.excel_optimization',
                            lambda x, c, is_issue=True: None)
        sent_emails = []
        monkeypatch.setattr('issue_statistics.send_email',
                            lambda x, n, r, s, **kw: sent_emails.append((n, r, s)))

        issue_statistics(str(tmp_path), sigs_sample, repos_issues_mapping_sample,
                         compare_dict_sample)

        assert len(sent_emails) > 0
        assert 'maintainer1' in [e[0] for e in sent_emails]
        # Subject should contain "Issue"
        assert any('Issue' in e[2] for e in sent_emails)

    def test_controls_filter_issue_maintainer_part(self, tmp_path, monkeypatch,
                                                    sigs_sample, repos_issues_mapping_sample,
                                                    compare_dict_sample):
        """When controls disable maintainer part, that part is skipped."""
        monkeypatch.setattr('issue_statistics.get_email_mappings',
                            lambda: {'m1': 'm1@e.com'})
        monkeypatch.setattr('issue_statistics.get_maintainers',
                            lambda sig: (['m1'], True))
        monkeypatch.setattr('issue_statistics.get_committers_mapping',
                            lambda sig: {})

        monkeypatch.setattr('issue_statistics.csv_to_xlsx',
                            lambda c: c.replace('.csv', '.xlsx'))
        monkeypatch.setattr('issue_statistics.excel_optimization',
                            lambda x, c, is_issue=True: None)
        sent_emails = []
        monkeypatch.setattr('issue_statistics.send_email',
                            lambda x, n, r, s, **kw: sent_emails.append((n, r, s)))

        controls = {
            'm1': {
                'pr': {'maintainer': True, 'committer': True},
                'issue': {'maintainer': False, 'committer': True},
            }
        }
        monkeypatch.setattr('issue_statistics.load_email_controls', lambda: controls)

        issue_statistics(str(tmp_path), sigs_sample, repos_issues_mapping_sample,
                         compare_dict_sample)

        names = [e[0] for e in sent_emails]
        assert 'm1' not in names

    def test_status_merging(self, tmp_path, monkeypatch,
                            sigs_sample, compare_dict_sample):
        """Issue type, state, and assignee are merged into status column."""
        issues = {
            'openeuler/ai-framework/feature': self._make_issue_item(
                'https://gitcode.com/openeuler/ai-framework/issues/1',
                issue_type='缺陷', issue_state='进行中', assignee='user1'),
        }
        monkeypatch.setattr('issue_statistics.get_email_mappings',
                            lambda: {'maintainer1': 'm1@e.com'})
        monkeypatch.setattr('issue_statistics.get_maintainers',
                            lambda sig: (['maintainer1'], True))
        monkeypatch.setattr('issue_statistics.get_committers_mapping', lambda sig: {})

        # Capture the CSV rows
        csv_rows = []
        import codecs, csv
        orig_writerow = csv.writer
        monkeypatch.setattr('issue_statistics.csv_to_xlsx',
                            lambda c: c.replace('.csv', '.xlsx'))
        monkeypatch.setattr('issue_statistics.excel_optimization',
                            lambda x, c, is_issue=True: None)
        sent_emails = []
        monkeypatch.setattr('issue_statistics.send_email',
                            lambda x, n, r, s, **kw: sent_emails.append(n))

        issue_statistics(str(tmp_path), sigs_sample, issues,
                         compare_dict_sample)
        assert len(sent_emails) > 0

    def test_skip_kernel_sig(self, tmp_path, monkeypatch, compare_dict_sample):
        """Kernel SIG issues are skipped."""
        kernel_sigs = [{'name': 'Kernel', 'repositories': ['openeuler/kernel']}]
        monkeypatch.setattr('issue_statistics.get_email_mappings', lambda: {})
        monkeypatch.setattr('issue_statistics.get_maintainers',
                            lambda sig: (['m1'], True))
        monkeypatch.setattr('issue_statistics.get_committers_mapping', lambda sig: {})

        sent_emails = []
        monkeypatch.setattr('issue_statistics.send_email',
                            lambda x, n, r, s, **kw: sent_emails.append(n))
        monkeypatch.setattr('issue_statistics.csv_to_xlsx',
                            lambda c: c.replace('.csv', '.xlsx'))
        monkeypatch.setattr('issue_statistics.excel_optimization',
                            lambda x, c, is_issue=True: None)

        issues = {'openeuler/kernel/main': self._make_issue_item(
            'https://gitcode.com/openeuler/kernel/issues/1')}
        issue_statistics(str(tmp_path), kernel_sigs, issues,
                         compare_dict_sample)
        assert len(sent_emails) == 0

    def test_maintainer_committer_one_email_two_parts(self, tmp_path, monkeypatch,
                                                       repos_issues_mapping_sample, compare_dict_sample):
        """Maintainer who is also a committer gets one email with two parts."""
        sigs = [{'name': 'sig-ai',
                 'repositories': [
                     'openeuler/ai-framework',
                     'openeuler/ai-models',
                 ]}]
        monkeypatch.setattr('issue_statistics.get_email_mappings',
                            lambda: {'m1': 'm1@e.com'})
        monkeypatch.setattr('issue_statistics.get_maintainers',
                            lambda sig: (['m1'], True))
        monkeypatch.setattr('issue_statistics.get_committers_mapping',
                            lambda sig: {'openeuler/ai-framework': ['m1'],
                                         'openeuler/ai-models': ['m1']})

        def fake_csv(csv_path):
            return csv_path.replace('.csv', '.xlsx')

        monkeypatch.setattr('issue_statistics.csv_to_xlsx', fake_csv)

        def fake_excel(xlsx_path, compare_dict, is_issue=True):
            html_path = xlsx_path.replace('.xlsx', '.html')
            with open(html_path, 'w', encoding='utf-8') as f:
                f.write('<html><body><table>Issue Table</table></body></html>')

        monkeypatch.setattr('issue_statistics.excel_optimization', fake_excel)
        sent_emails = []
        monkeypatch.setattr('issue_statistics.send_email',
                            lambda x, n, r, s, **kw: sent_emails.append((n, r, s, kw.get('html_content', ''))))

        issue_statistics(str(tmp_path), sigs, repos_issues_mapping_sample,
                         compare_dict_sample)
        # m1 should receive exactly 1 email
        m1_emails = [e for e in sent_emails if e[0] == 'm1']
        assert len(m1_emails) == 1
        assert 'Committer' not in m1_emails[0][2]
        html = m1_emails[0][3]
        assert '作为 Maintainer 的 Issue' in html
        assert '作为 Committer 的 Issue' in html

    def test_pure_committer_gets_one_email(self, tmp_path, monkeypatch,
                                           repos_issues_mapping_sample,
                                           compare_dict_sample):
        """A pure committer gets one email with only committer part."""
        sigs = [{'name': 'sig-ai',
                 'repositories': ['openeuler/ai-framework']}]
        monkeypatch.setattr('issue_statistics.get_email_mappings',
                            lambda: {'c1': 'c1@e.com'})
        monkeypatch.setattr('issue_statistics.get_maintainers',
                            lambda sig: (['m1'], True))
        monkeypatch.setattr('issue_statistics.get_committers_mapping',
                            lambda sig: {'openeuler/ai-framework': ['c1']})

        def fake_csv(csv_path):
            return csv_path.replace('.csv', '.xlsx')

        def fake_excel(xlsx_path, compare_dict, is_issue=True):
            html_path = xlsx_path.replace('.xlsx', '.html')
            with open(html_path, 'w', encoding='utf-8') as f:
                f.write('<html><body><table>Issue Table</table></body></html>')

        monkeypatch.setattr('issue_statistics.csv_to_xlsx', fake_csv)
        monkeypatch.setattr('issue_statistics.excel_optimization', fake_excel)
        sent_emails = []
        monkeypatch.setattr('issue_statistics.send_email',
                            lambda x, n, r, s, **kw: sent_emails.append((n, r, s, kw.get('html_content', ''))))

        issue_statistics(str(tmp_path), sigs, repos_issues_mapping_sample,
                         compare_dict_sample)

        c1_emails = [e for e in sent_emails if e[0] == 'c1']
        assert len(c1_emails) == 1
        assert 'Committer' not in c1_emails[0][2]
        html = c1_emails[0][3]
        # Single part: no role title, but should not contain maintainer title either
        assert '作为 Maintainer 的 Issue' not in html
        assert 'Issue Table' in html

    def test_test_mode_limits_3_emails(self, tmp_path, monkeypatch,
                                        sigs_sample, repos_issues_mapping_sample,
                                        compare_dict_sample):
        """In test mode, only 3 issue emails are sent, all to the test address."""
        monkeypatch.setenv('test_reviever_email', 'test@example.com')
        # Create 5 maintainers, each with issues
        monkeypatch.setattr('issue_statistics.get_email_mappings',
                            lambda: {'m{}'.format(i): 'm{}@e.com'.format(i) for i in range(1, 6)})
        monkeypatch.setattr('issue_statistics.get_maintainers',
                            lambda sig: (['m1', 'm2', 'm3', 'm4', 'm5'], True))
        monkeypatch.setattr('issue_statistics.get_committers_mapping', lambda sig: {})

        monkeypatch.setattr('issue_statistics.csv_to_xlsx',
                            lambda c: c.replace('.csv', '.xlsx'))
        sent_emails = []
        def fake_send(xlsx, nickname, receivers, subject='', **kw):
            sent_emails.append((nickname, receivers, subject))
        monkeypatch.setattr('issue_statistics.send_email', fake_send)
        monkeypatch.setattr('issue_statistics.excel_optimization',
                            lambda x, c, is_issue=True: None)

        # Create 5 maintainer entries with varied data
        issues = {}
        for i in range(1, 6):
            key = 'openeuler/ai-framework/branch{}'.format(i)
            issues[key] = {
                'title': 'Issue {}'.format(i),
                'link': 'https://gitcode.com/openeuler/ai-framework/issues/{}'.format(i),
                'created_at': (datetime.datetime.now() - datetime.timedelta(days=i)).strftime(
                    '%Y-%m-%d %H:%M:%S'),
                'issue_type': '缺陷',
                'issue_state': '待确认',
                'assignee': '',
            }

        issue_statistics(str(tmp_path), sigs_sample, issues,
                         compare_dict_sample)

        assert len(sent_emails) == 3
        for _, receivers, _ in sent_emails:
            assert receivers == ['test@example.com']

    def test_dry_run_generates_local_html(self, tmp_path, monkeypatch,
                                          sigs_sample, repos_issues_mapping_sample,
                                          compare_dict_sample):
        """DRY_RUN generates local HTML files without sending emails."""
        monkeypatch.setenv('DRY_RUN', 'true')
        monkeypatch.chdir(tmp_path)
        (tmp_path / 'data').mkdir()
        monkeypatch.setattr('issue_statistics.get_email_mappings',
                            lambda: {'m1': 'm1@e.com'})
        monkeypatch.setattr('issue_statistics.get_maintainers',
                            lambda sig: (['m1'], True))
        monkeypatch.setattr('issue_statistics.get_committers_mapping', lambda sig: {})

        def fake_csv(csv_path):
            return csv_path.replace('.csv', '.xlsx')

        def fake_excel(xlsx_path, compare_dict, is_issue=True):
            html_path = xlsx_path.replace('.xlsx', '.html')
            with open(html_path, 'w', encoding='utf-8') as f:
                f.write('<html><body><table>Issue Table</table></body></html>')

        monkeypatch.setattr('issue_statistics.csv_to_xlsx', fake_csv)
        monkeypatch.setattr('issue_statistics.excel_optimization', fake_excel)
        sent_emails = []
        monkeypatch.setattr('issue_statistics.send_email',
                            lambda x, n, r, s, **kw: sent_emails.append(n))

        issue_statistics(str(tmp_path / 'data'), sigs_sample, repos_issues_mapping_sample,
                         compare_dict_sample)

        assert len(sent_emails) == 0
        assert (tmp_path / 'test_output' / 'issue_m1.html').exists()


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

class TestMain:
    def test_main_runs(self, monkeypatch):
        """main() orchestrates prepare_env → get_sigs → compare → issues → stats."""
        calls = []
        monkeypatch.setattr('issue_statistics.prepare_env',
                            lambda: calls.append('env') or '/tmp/data')
        monkeypatch.setattr('issue_statistics.get_sigs',
                            lambda: (calls.append('sigs'), ([])))
        monkeypatch.setattr('issue_statistics.all_sigs_compare',
                            lambda s: calls.append('comp') or {})
        monkeypatch.setattr('issue_statistics.get_repos_issues_mapping',
                            lambda: calls.append('issues') or {})
        monkeypatch.setattr('issue_statistics.os.path.exists', lambda p: False)
        monkeypatch.setattr('issue_statistics.issue_statistics',
                            lambda *a: calls.append('stats'))
        monkeypatch.setattr('issue_statistics.yaml', MagicMock())

        main()
        assert 'env' in calls
        assert 'sigs' in calls
        assert 'issues' in calls
        assert 'stats' in calls
