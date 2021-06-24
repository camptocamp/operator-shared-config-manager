FROM ubuntu:20.04

RUN apt-get update && \
    apt-egt --yes install python3-pip
RUN python3 -m pip install kubernetes

COPY requirements.txt /tmp/
RUN python3 -m pip install --disable-pip-version-check --no-cache-dir --requirement=/tmp/requirements.txt && \
    rm --recursive --force /tmp/*

COPY Pipfile Pipfile.lock /tmp/
RUN cd /tmp && pipenv sync --system --clear && \
    rm --recursive --force /tmp/* /root/.cache/*

COPY operator /usr/bin/
CMD ["/usr/bin/operator"]
