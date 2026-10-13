FROM ubuntu:22.04 AS build
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y git make gcc g++ cmake libsctp-dev lksctp-tools
RUN git clone --depth 1 --branch v3.2.6 https://github.com/aligungr/UERANSIM /ueransim && cd /ueransim && make -j4

FROM ubuntu:22.04
RUN apt-get update && apt-get install -y libsctp1 iproute2 iputils-ping iperf3 tcpdump && rm -rf /var/lib/apt/lists/*
COPY --from=build /ueransim/build /ueransim
WORKDIR /ueransim
