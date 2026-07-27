#!/bin/bash

# Jenkins job script for openEuler PR/Issue statistics weekly report

# Initialize or update git repository
if [[ ! -d .git ]]; then
    git init &> /dev/null
    git remote add origin https://gitcode.com/lei0308/pr-statistics-report.git
    git config http.retry 2
    git fetch --depth=1 origin migrate-to-gitcode || exit 1
    git checkout migrate-to-gitcode
else
    git remote set-url origin https://gitcode.com/lei0308/pr-statistics-report.git
    git config http.retry 2
    git fetch origin --recurse-submodules=no --progress --prune
    git reset --hard origin/migrate-to-gitcode
fi

# Load Python 3.11 environment
source python3.11.env.sh

# Install dependencies with mirror (PyPI blocked on this node)
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple -q

# Test mode settings
if [[ "$DRY_RUN" == "true" ]]; then
    rm -rf test_output
    mkdir -p test_output
fi

# Reply-To for unsubscribe emails
export email_reply_to="${email_reply_to:-huanglei227@h-partners.com}"

python3 pr_statistics.py
python3 issue_statistics.py

# Archive test output when DRY_RUN
if [[ "$DRY_RUN" == "true" ]]; then
    tar -czf test_output.tar.gz test_output/
    echo "Test HTML files generated in test_output/"
fi
