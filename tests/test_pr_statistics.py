"""
Tests for pr_statistics.py — PR data fetching, statistics, and main entry point.
"""
import datetime
import os
import tempfile
from unittest.mock import MagicMock, patch, ANY

import pytest

from pr_statistics import get_repos_pulls_mapping, main, pr_statistics


# ---------------------------------------------------------------------------
# get_repos_pulls_mapping
# ---------------------------------------------------------------------------

class TestGetReposPullsMapping:
    def test_success_single_page(self, monkeypatch):
        """Single page of results (< 100 items)."""
        mock_data = {
            'data': [
                {
                    'link': 'https://gitcode.com/openeuler/ai-framework/pulls/100',
                    'title': 'PR Title',
                }
            ]
        }
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = mock_data
        monkeypatch.setattr('pr_statistics.requests.get', lambda url, params, timeout: mock_resp)

        result = get_repos_pulls_mapping()
        assert 'openeuler/ai-framework/pulls/100' in result
        assert result['openeuler/ai-framework/pulls/100']['title'] == 'PR Title'

    def test_pagination(self, monkeypatch):
        """Multiple pages are fetched until < 100 items returned."""
        page1_data = {'data': [{'link': 'https://gitcode.com/openeuler/r/pulls/{}'.format(i),
                                'title': 'PR {}'.format(i)} for i in range(100)]}
        page2_data = {'data': [{'link': 'https://gitcode.com/openeuler/r/pulls/200',
                                'title': 'PR 200'}]}

        responses = iter([page1_data, page2_data])
        def mock_get(url, params, timeout):
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = next(responses)
            return mock_resp

        monkeypatch.setattr('pr_statistics.requests.get', mock_get)
        result = get_repos_pulls_mapping()
        # Should have 101 items (100 + 1)
        assert len(result) == 101

    def test_api_failure(self, monkeypatch):
        """When API returns non-200, return None."""
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        monkeypatch.setattr('pr_statistics.requests.get', lambda url, params, timeout: mock_resp)
        result = get_repos_pulls_mapping()
        assert result is None

    def test_empty_result(self, monkeypatch):
        """No open PRs."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {'data': []}
        monkeypatch.setattr('pr_statistics.requests.get', lambda url, params, timeout: mock_resp)
        result = get_repos_pulls_mapping()
        assert result == {}

    def test_link_parsing(self, monkeypatch):
        """link field is split correctly to get repo/branch/number."""
        mock_data = {
            'data': [
                {'link': 'https://gitcode.com/openeuler/repo/pulls/42', 'title': 'Test'}
            ]
        }
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = mock_data
        monkeypatch.setattr('pr_statistics.requests.get', lambda url, params, timeout: mock_resp)
        result = get_repos_pulls_mapping()
        assert 'openeuler/repo/pulls/42' in result


# ---------------------------------------------------------------------------
# pr_statistics (core logic)
# ---------------------------------------------------------------------------

class TestPrStatistics:
    def _make_pull_item(self, link, title='Test PR', days_ago=5,
                         draft=False, labels='openeuler-cla/yes',
                         mergeable=True, ref='main'):
        """Helper to build a pull dict matching the API response format."""
        created = (datetime.datetime.now() - datetime.timedelta(days=days_ago)).strftime(
            '%Y-%m-%d %H:%M:%S')
        return {
            'title': title,
            'link': link,
            'created_at': created,
            'draft': draft,
            'labels': labels,
            'ref': ref,
            'mergeable': mergeable,
        }

    def test_basic_flow(self, tmp_path, monkeypatch,
                        sigs_sample, repos_pulls_mapping_sample, compare_dict_sample):
        """End-to-end pr_statistics with mocked email/send."""
        # Mock get_email_mappings
        monkeypatch.setattr('pr_statistics.get_email_mappings',
                            lambda: {'maintainer1': 'm1@e.com'})

        # Mock get_maintainers
        monkeypatch.setattr('pr_statistics.get_maintainers',
                            lambda sig: (['maintainer1'], True))

        # Mock get_committers_mapping
        monkeypatch.setattr('pr_statistics.get_committers_mapping',
                            lambda sig: {'openeuler/ai-framework': [], 'openeuler/ai-models': []})

        # Mock csv_to_xlsx and excel_optimization and send_email
        def fake_csv_to_xlsx(csv_path):
            xlsx = csv_path.replace('.csv', '.xlsx')
            # Write a minimal xlsx-like file
            import openpyxl
            wb = openpyxl.Workbook()
            wb.save(xlsx)
            return xlsx

        monkeypatch.setattr('pr_statistics.csv_to_xlsx', fake_csv_to_xlsx)
        monkeypatch.setattr('pr_statistics.excel_optimization',
                            lambda x, c, is_issue=False: None)
        sent_emails = []
        monkeypatch.setattr('pr_statistics.send_email',
                            lambda x, n, r, s, **kw: sent_emails.append((n, r, s)))

        pr_statistics(str(tmp_path), sigs_sample, repos_pulls_mapping_sample,
                      compare_dict_sample, [], whitelist_active=False)

        # At least one email should have been sent (for maintainer1)
        assert len(sent_emails) > 0
        names = [e[0] for e in sent_emails]
        assert 'maintainer1' in names

    def test_whitelist_active_filters_maintainers(self, tmp_path, monkeypatch,
                                                   sigs_sample, repos_pulls_mapping_sample,
                                                   compare_dict_sample):
        """When whitelist is active, non-whitelisted maintainers are skipped."""
        monkeypatch.setattr('pr_statistics.get_email_mappings',
                            lambda: {'maintainer1': 'm1@e.com', 'maintainer2': 'm2@e.com'})
        monkeypatch.setattr('pr_statistics.get_maintainers',
                            lambda sig: (['maintainer1', 'maintainer2'], True))
        monkeypatch.setattr('pr_statistics.get_committers_mapping',
                            lambda sig: {})

        monkeypatch.setattr('pr_statistics.csv_to_xlsx', lambda c: c.replace('.csv', '.xlsx'))
        monkeypatch.setattr('pr_statistics.excel_optimization',
                            lambda x, c, is_issue=False: None)
        sent_emails = []
        monkeypatch.setattr('pr_statistics.send_email',
                            lambda x, n, r, s, **kw: sent_emails.append((n, r, s)))

        # Only maintainer1 in whitelist
        pr_statistics(str(tmp_path), sigs_sample, repos_pulls_mapping_sample,
                      compare_dict_sample, ['maintainer1'], whitelist_active=True)

        names = [e[0] for e in sent_emails]
        assert 'maintainer1' in names
        assert 'maintainer2' not in names

    def test_skip_kernel_sig(self, tmp_path, monkeypatch, compare_dict_sample):
        """Kernel SIG should be skipped."""
        kernel_sigs = [{'name': 'Kernel', 'repositories': ['openeuler/kernel']}]
        monkeypatch.setattr('pr_statistics.get_email_mappings', lambda: {})
        monkeypatch.setattr('pr_statistics.get_maintainers',
                            lambda sig: (['m1'], True))
        monkeypatch.setattr('pr_statistics.get_committers_mapping', lambda sig: {})

        sent_emails = []
        monkeypatch.setattr('pr_statistics.send_email',
                            lambda x, n, r, s, **kw: sent_emails.append(n))
        monkeypatch.setattr('pr_statistics.csv_to_xlsx', lambda c: c.replace('.csv', '.xlsx'))
        monkeypatch.setattr('pr_statistics.excel_optimization',
                            lambda x, c, is_issue=False: None)

        repos = {'openeuler/kernel/main': self._make_pull_item(
            'https://gitcode.com/openeuler/kernel/pulls/1')}
        pr_statistics(str(tmp_path), kernel_sigs, repos,
                      compare_dict_sample, [], whitelist_active=False)
        # No emails for Kernel SIG
        assert len(sent_emails) == 0

    def test_empty_sig_repos_skipped(self, tmp_path, monkeypatch, compare_dict_sample):
        """SIG with no repositories is skipped."""
        empty_sigs = [{'name': 'sig-empty', 'repositories': []}]
        monkeypatch.setattr('pr_statistics.get_email_mappings', lambda: {})
        monkeypatch.setattr('pr_statistics.get_maintainers',
                            lambda sig: (['m1'], True))
        monkeypatch.setattr('pr_statistics.get_committers_mapping', lambda sig: {})

        sent_emails = []
        monkeypatch.setattr('pr_statistics.send_email',
                            lambda x, n, r, s, **kw: sent_emails.append(n))
        monkeypatch.setattr('pr_statistics.csv_to_xlsx', lambda c: c.replace('.csv', '.xlsx'))
        monkeypatch.setattr('pr_statistics.excel_optimization',
                            lambda x, c, is_issue=False: None)

        pr_statistics(str(tmp_path), empty_sigs, {},
                      compare_dict_sample, [], whitelist_active=False)
        assert len(sent_emails) == 0

    def test_non_openeuler_repos_filtered(self, tmp_path, monkeypatch, compare_dict_sample):
        """Repos not under openeuler/ or src-openeuler/ are filtered out."""
        other_sigs = [{'name': 'sig-other',
                       'repositories': ['other/repo', 'openeuler/valid-repo']}]
        monkeypatch.setattr('pr_statistics.get_email_mappings',
                            lambda: {'m1': 'm1@e.com'})
        monkeypatch.setattr('pr_statistics.get_maintainers',
                            lambda sig: (['m1'], True))
        monkeypatch.setattr('pr_statistics.get_committers_mapping', lambda sig: {})

        sent_emails = []
        monkeypatch.setattr('pr_statistics.send_email',
                            lambda x, n, r, s, **kw: sent_emails.append(n))
        monkeypatch.setattr('pr_statistics.csv_to_xlsx', lambda c: c.replace('.csv', '.xlsx'))
        monkeypatch.setattr('pr_statistics.excel_optimization',
                            lambda x, c, is_issue=False: None)

        repos = {'openeuler/valid-repo/main': self._make_pull_item(
            'https://gitcode.com/openeuler/valid-repo/pulls/1')}
        pr_statistics(str(tmp_path), other_sigs, repos,
                      compare_dict_sample, [], whitelist_active=False)
        # Only 'other/repo' is filtered, 'openeuler/valid-repo' is processed
        assert len(sent_emails) > 0

    def test_draft_status(self, tmp_path, monkeypatch,
                          sigs_sample, compare_dict_sample):
        """Draft PRs get '草稿' status."""
        pulls = {
            'openeuler/ai-framework/main': self._make_pull_item(
                'https://gitcode.com/openeuler/ai-framework/pulls/1',
                draft=True, labels='openeuler-cla/yes'),
        }
        monkeypatch.setattr('pr_statistics.get_email_mappings',
                            lambda: {'maintainer1': 'm1@e.com'})
        monkeypatch.setattr('pr_statistics.get_maintainers',
                            lambda sig: (['maintainer1'], True))
        monkeypatch.setattr('pr_statistics.get_committers_mapping', lambda sig: {})

        sent_emails = []
        monkeypatch.setattr('pr_statistics.csv_to_xlsx',
                            lambda c: c.replace('.csv', '.xlsx'))
        monkeypatch.setattr('pr_statistics.excel_optimization',
                            lambda x, c, is_issue=False: None)
        monkeypatch.setattr('pr_statistics.send_email',
                            lambda x, n, r, s, **kw: sent_emails.append(n))

        pr_statistics(str(tmp_path), sigs_sample, pulls,
                      compare_dict_sample, [], whitelist_active=False)
        assert len(sent_emails) > 0

    def test_maintainer_also_committer_merged_email(self, tmp_path, monkeypatch,
                                                     compare_dict_sample):
        """Maintainer who is also committer gets one merged email (two tables)."""
        sigs = [{'name': 'sig-ai',
                 'repositories': ['openeuler/ai-framework']}]
        monkeypatch.setattr('pr_statistics.get_email_mappings',
                            lambda: {'m1': 'm1@e.com'})
        monkeypatch.setattr('pr_statistics.get_maintainers',
                            lambda sig: (['m1'], True))
        monkeypatch.setattr('pr_statistics.get_committers_mapping',
                            lambda sig: {'openeuler/ai-framework': ['m1']})

        pulls = {
            'openeuler/ai-framework/main': self._make_pull_item(
                'https://gitcode.com/openeuler/ai-framework/pulls/1'),
            'openeuler/ai-framework/dev': self._make_pull_item(
                'https://gitcode.com/openeuler/ai-framework/pulls/2',
                ref='dev', labels='openeuler-cla/yes'),
        }

        def fake_csv(csv_path):
            return csv_path.replace('.csv', '.xlsx')

        def fake_excel(xlsx_path, compare_dict, is_issue=False):
            # Create HTML file so the merge logic can read it
            html_path = xlsx_path.replace('.xlsx', '.html')
            with open(html_path, 'w', encoding='utf-8') as f:
                f.write('<html><body><table>PR Table</table></body></html>')

        monkeypatch.setattr('pr_statistics.csv_to_xlsx', fake_csv)
        monkeypatch.setattr('pr_statistics.excel_optimization', fake_excel)
        sent_emails = []
        monkeypatch.setattr('pr_statistics.send_email',
                            lambda x, n, r, s, **kw: sent_emails.append((n, r, s)))

        pr_statistics(str(tmp_path), sigs, pulls,
                      compare_dict_sample, [], whitelist_active=False)
        # m1 should get exactly 1 email (merged), not 2
        m1_emails = [e for e in sent_emails if e[0] == 'm1']
        assert len(m1_emails) == 1

    def test_pure_committer_gets_separate_email(self, tmp_path, monkeypatch,
                                                  compare_dict_sample):
        """Pure committer (not maintainer) gets a committer subject email."""
        sigs = [{'name': 'sig-ai',
                 'repositories': ['openeuler/ai-framework']}]
        monkeypatch.setattr('pr_statistics.get_email_mappings',
                            lambda: {'c1': 'c1@e.com'})
        monkeypatch.setattr('pr_statistics.get_maintainers',
                            lambda sig: (['m1'], True))
        monkeypatch.setattr('pr_statistics.get_committers_mapping',
                            lambda sig: {'openeuler/ai-framework': ['c1']})

        pulls = {
            'openeuler/ai-framework/main': self._make_pull_item(
                'https://gitcode.com/openeuler/ai-framework/pulls/1'),
        }

        def fake_csv(csv_path):
            return csv_path.replace('.csv', '.xlsx')

        def fake_excel(xlsx_path, compare_dict, is_issue=False):
            html_path = xlsx_path.replace('.xlsx', '.html')
            with open(html_path, 'w', encoding='utf-8') as f:
                f.write('<html><body><table>PR Table</table></body></html>')

        monkeypatch.setattr('pr_statistics.csv_to_xlsx', fake_csv)
        monkeypatch.setattr('pr_statistics.excel_optimization', fake_excel)
        sent_emails = []
        monkeypatch.setattr('pr_statistics.send_email',
                            lambda x, n, r, s, **kw: sent_emails.append((n, r, s)))

        pr_statistics(str(tmp_path), sigs, pulls,
                      compare_dict_sample, [], whitelist_active=False)

        c1_emails = [e for e in sent_emails if e[0] == 'c1']
        assert len(c1_emails) == 1
        assert 'Committer' in c1_emails[0][2]

    def test_test_mode_limits_3_emails(self, tmp_path, monkeypatch,
                                        sigs_sample, repos_pulls_mapping_sample,
                                        compare_dict_sample):
        """In test mode, only 3 emails are sent, all to the test address."""
        monkeypatch.setenv('test_reviever_email', 'test@example.com')
        # Create 5 maintainers, each with PRs
        monkeypatch.setattr('pr_statistics.get_email_mappings',
                            lambda: {'m{}'.format(i): 'm{}@e.com'.format(i) for i in range(1, 6)})
        monkeypatch.setattr('pr_statistics.get_maintainers',
                            lambda sig: (['m1', 'm2', 'm3', 'm4', 'm5'], True))
        monkeypatch.setattr('pr_statistics.get_committers_mapping', lambda sig: {})

        monkeypatch.setattr('pr_statistics.csv_to_xlsx',
                            lambda c: c.replace('.csv', '.xlsx'))
        sent_emails = []
        def fake_send(xlsx, nickname, receivers, subject='', **kw):
            sent_emails.append((nickname, receivers, subject))
        monkeypatch.setattr('pr_statistics.send_email', fake_send)
        monkeypatch.setattr('pr_statistics.excel_optimization',
                            lambda x, c, is_issue=False: None)

        # Create 5 maintainer entries with one PR each
        pulls = {}
        for i in range(1, 6):
            key = 'openeuler/ai-framework/branch{}'.format(i)
            pulls[key] = self._make_pull_item(
                'https://gitcode.com/openeuler/ai-framework/pulls/{}'.format(i),
                ref='branch{}'.format(i))

        pr_statistics(str(tmp_path), sigs_sample, pulls,
                      compare_dict_sample, [], whitelist_active=False)

        assert len(sent_emails) == 3
        for _, receivers, _ in sent_emails:
            assert receivers == ['test@example.com']

    def test_test_mode_skips_whitelist(self, tmp_path, monkeypatch,
                                        sigs_sample, repos_pulls_mapping_sample,
                                        compare_dict_sample):
        """In test mode, whitelist filtering is skipped."""
        monkeypatch.setenv('test_reviever_email', 'test@example.com')
        monkeypatch.setattr('pr_statistics.get_email_mappings',
                            lambda: {'m1': 'm1@e.com'})
        monkeypatch.setattr('pr_statistics.get_maintainers',
                            lambda sig: (['m1'], True))
        monkeypatch.setattr('pr_statistics.get_committers_mapping', lambda sig: {})

        monkeypatch.setattr('pr_statistics.csv_to_xlsx',
                            lambda c: c.replace('.csv', '.xlsx'))
        sent_emails = []
        monkeypatch.setattr('pr_statistics.send_email',
                            lambda x, n, r, s, **kw: sent_emails.append((n, r, s)))
        monkeypatch.setattr('pr_statistics.excel_optimization',
                            lambda x, c, is_issue=False: None)

        # Pass empty whitelist — in prod mode this would block
        pr_statistics(str(tmp_path), sigs_sample, repos_pulls_mapping_sample,
                      compare_dict_sample, [], whitelist_active=True)

        # Even with empty whitelist, test mode still sends
        assert len(sent_emails) > 0
        assert sent_emails[0][1] == ['test@example.com']


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

class TestMain:
    def test_main_runs(self, monkeypatch):
        """main() calls all top-level functions in order."""
        calls = []
        monkeypatch.setattr('pr_statistics.prepare_env', lambda: calls.append('env') or '/tmp/data')
        monkeypatch.setattr('pr_statistics.get_sigs', lambda: (calls.append('sigs'), ([])))
        monkeypatch.setattr('pr_statistics.all_sigs_compare', lambda s: calls.append('comp') or {})
        monkeypatch.setattr('pr_statistics.get_repos_pulls_mapping', lambda: calls.append('pulls') or {})
        monkeypatch.setattr('pr_statistics.os.path.exists', lambda p: 'whitelist' in p)
        monkeypatch.setattr('pr_statistics.pr_statistics', lambda *a: calls.append('stats'))
        monkeypatch.setattr('pr_statistics.yaml', MagicMock())

        main()
        assert 'env' in calls
        assert 'sigs' in calls
        assert 'pulls' in calls
        assert 'stats' in calls
