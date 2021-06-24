FROM ubuntu:20.04 as runtime

RUN apt-get update && \
    apt-get --yes install python3-pip
RUN python3 -m pip install kubernetes

COPY requirements.txt /tmp/
RUN python3 -m pip install --disable-pip-version-check --no-cache-dir --requirement=/tmp/requirements.txt

COPY Pipfile Pipfile.lock /tmp/
RUN cd /tmp && pipenv sync --system --clear && \
    rm --recursive --force /root/.cache/*

COPY operator /usr/bin/
CMD ["/usr/bin/operator"]

FROM runtime as test

RUN cd /tmp && pipenv sync --system --clear --dev && \
    rm --recursive --force /root/.cache/*

WORKDIR /app
COPY * ./

FROM runtime as default
RUN rm --recursive --force /tmp/*
