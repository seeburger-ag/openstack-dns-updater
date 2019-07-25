#FROM python:3.5
FROM my_base:latest

ADD openstack-dns-updater.sh /usr/local/bin/
ADD openstack-dns-updater.py /usr/local/bin/
ADD environment /etc/

#run pip install --upgrade pip
#run pip install os-client-config
#run pip install python-powerdns
#run pip install kombu
#run pip install keystone
#run pip install python-novaclient

CMD [ "/usr/local/bin/openstack-dns-updater.sh" ]


