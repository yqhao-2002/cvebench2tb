#!/bin/sh
# python3-based health probe（镜像无 curl）
python3 -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:9091/health', timeout=3).status==200 else 1)"
