FROM openeuler/openeuler:22.03-lts

MAINTAINER liuqi<469227928@qq.com>

RUN yum install -y python3-pip git

WORKDIR /work/pr-statistics

# install from requirements.txt so the pinned xlsx2html version applies here too
COPY requirements.txt .
RUN pip3 install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

COPY . /work/pr-statistics

ENV TZ=Asia/Shanghai

ENTRYPOINT ["python3", "pr_statistics.py"] 
