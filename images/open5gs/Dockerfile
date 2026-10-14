FROM ubuntu:22.04
ENV DEBIAN_FRONTEND=noninteractive
# Pinned so every teammate builds the same core (the PPA only serves its newest release;
# if it moves on, this build fails instead of silently installing a different version).
ARG OPEN5GS_VERSION=2.8.0~jammy5
RUN apt-get update && apt-get install -y software-properties-common gnupg iproute2 iptables iputils-ping tcpdump iperf3 \
 && add-apt-repository -y ppa:open5gs/latest \
 && printf 'Package: open5gs*\nPin: version %s\nPin-Priority: 1001\n' "$OPEN5GS_VERSION" > /etc/apt/preferences.d/open5gs \
 && apt-get update && apt-get install -y open5gs \
 && dpkg-query -W -f '${Version}' open5gs-upf | grep -qx "$OPEN5GS_VERSION" \
 && rm -rf /var/lib/apt/lists/*
