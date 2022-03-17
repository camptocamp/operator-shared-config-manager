FROM ubuntu:20.04 as runtime

RUN apt-get update && \
    apt-get --yes --no-install-recommends install python3-pip && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/
RUN python3 -m pip install --disable-pip-version-check --no-cache-dir --requirement=/tmp/requirements.txt

COPY Pipfile Pipfile.lock /tmp/
RUN cd /tmp && pipenv sync --system --clear && \
    rm --recursive --force /root/.cache/*


FROM runtime as test

RUN cd /tmp && pipenv sync --system --clear --dev && \
    rm --recursive --force /root/.cache/*

WORKDIR /app
COPY * ./


FROM runtime as default

WORKDIR /app
COPY operator.py ./
CMD ["kopf", "run", "operator.py"]
