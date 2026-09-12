# The egress sink of the cross-harness evaluation. environment.md "The sink"
# states its role. It runs as root inside its container and needs
# NET_ADMIN, because it takes the network's gateway address and redirects
# every flow that reaches it to its recorder.
ARG BASE_IMAGE=ubuntu:24.04@sha256:224a1869083a311ef3f13648a154ba79832fbef6364d31493642ca03082da254
FROM ${BASE_IMAGE}
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 iproute2 iptables \
 && rm -rf /var/lib/apt/lists/* \
 && mkdir -p /var/log/sink
COPY recorder.py /opt/sink/recorder.py
RUN chmod 755 /opt/sink/recorder.py
ENTRYPOINT ["/usr/bin/python3", "/opt/sink/recorder.py"]
CMD ["--hold", "10.219.0.254/24", "--interface", "eth0", "--log", "/var/log/sink/connections.jsonl"]
