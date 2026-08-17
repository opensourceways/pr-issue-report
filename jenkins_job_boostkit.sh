#!/bin/bash

# Jenkins job script for BoostKit PR/Issue statistics weekly report

# Exit on any error
set -e

# Initialize or update git repository
REPO_URL=https://github.com/opensourceways/pr-issue-report.git
BRANCH=lei_dev
if [[ ! -d .git ]]; then
    git init &> /dev/null
    git remote add origin "$REPO_URL"
    git config http.retry 2
    git fetch --depth=1 origin "$BRANCH" || exit 1
    git checkout -b "$BRANCH" "origin/$BRANCH" || exit 1
else
    git remote set-url origin "$REPO_URL"
    git config http.retry 2
    git fetch origin --recurse-submodules=no --progress --prune
    git reset --hard "origin/$BRANCH"
fi

# Print current commit for debugging
echo "Current commit: $(git rev-parse --short HEAD)"

# Load Python 3.11 environment
source python3.11.env.sh

# Install dependencies with mirror (PyPI blocked on this node)
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple -q

# Select the active community (see communities.yaml)
export COMMUNITY=boostkit

# GitCode API access token (required for the boostkit data source)
if [[ -z "$GITCODE_TOKEN" ]]; then
    echo "ERROR: GITCODE_TOKEN is required (GitCode API access for boostkit)."
    exit 1
fi

# Test mode settings (test_output lives inside the per-community directory)
if [[ "$DRY_RUN" == "true" ]]; then
    rm -rf "$COMMUNITY/test_output"
fi

# Reply-To for unsubscribe emails
export email_reply_to="${email_reply_to:-huanglei227@h-partners.com}"

# Run statistics
python3 pr_statistics.py
python3 issue_statistics.py

# Archive test output when DRY_RUN is enabled
if [[ "$DRY_RUN" == "true" ]]; then
    tar -czf test_output.tar.gz -C "$COMMUNITY" test_output/
    echo "Test HTML files generated in $COMMUNITY/test_output/"
fi
