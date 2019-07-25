#!/bin/bash

[ -f /etc/environment ] && source /etc/environment
/usr/local/bin/python3 /usr/local/bin/openstack-dns-updater.py
