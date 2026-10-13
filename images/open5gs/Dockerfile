FROM ubuntu:22.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y software-properties-common gnupg iproute2 iptables iputils-ping tcpdump iperf3 \
 && add-apt-repository -y ppa:open5gs/latest && apt-get update && apt-get install -y open5gs \
 && rm -rf /var/lib/apt/lists/*
