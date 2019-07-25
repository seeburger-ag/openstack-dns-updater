FROM python:3.5
#FROM test_base:latest

ADD openstack-dns-updater.sh /usr/local/bin/
ADD openstack-dns-updater.py /usr/local/bin/
ADD environment /etc/
ADD ssl/* /etc/ssl/certs/

run pip install --upgrade pip
run pip install os-client-config
run pip install python-powerdns
run pip install kombu
run pip install keystone
run pip install python-novaclient

run apt-get update
run apt-get install multitail
run update-ca-certificates

CMD [ "/usr/local/bin/openstack-dns-updater.sh" ]


