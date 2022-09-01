#!/usr/bin/env bash
set -Eeuo pipefail

tar zxf SMPPSim.tar.gz
sed -i '' -e 's/HTTP_PORT=88/HTTP_PORT=8080/' SMPPSim/conf/smppsim.props
chmod +x SMPPSim/startsmppsim.sh