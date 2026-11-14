#!/bin/sh
set -eu
mkdir -p /app/data
chown shopbot:shopbot /app/data
exec setpriv --reuid=shopbot --regid=shopbot --init-groups -- python -m shopbot.bootstrap.app
