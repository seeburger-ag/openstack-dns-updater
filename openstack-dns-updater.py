#!/usr/bin/env python

# OpenStack DNS Updater listens on the RabbitMQ message bus. Whenever an
# instance is created or deleted DNS updater creates or removes
# its DNS A record. The name of the instance is directly used as its FQDN.
# Hence instances in OpenStack should be named with their FQDN.
# The IP address stored in DNS is the IP address of the first network interface
# on the private network. You can easily change the script to store floating
# IP address in DNS instead.
#
# OpenStack DNS Updater works well on CentOS 7. You can copy it into your
# /usr/local/bin directory and run it as user "nova". See the accompanying
# systemd script. OpenStack DNS Updater logs into /var/log/nova/dns-updater.log
# by default.

import json
import logging as log
import pprint
from subprocess import Popen, PIPE
from kombu import BrokerConnection
from kombu import Exchange
from kombu import Queue
from kombu.mixins import ConsumerMixin
from keystoneclient.auth.identity import v3
from keystoneclient import session
from novaclient.v2 import client

LOG_FILE="/var/log/nova/dns-updater.log"

EXCHANGE_NAME_NOVA="nova"
EXCHANGE_NAME_NEUTRON="neutron"
ROUTING_KEY="notifications.info"
QUEUE_NAME="dns_updater_queue"
BROKER_URI="amqp://guest:guest@control-rmq:5672//"

OS_AUTH_URL="http://control-public:5000/v3"
OS_USERNAME="guest"
OS_PASSWORD="guest"
OS_TENANT_NAME="demo"
OS_USER_DOMAIN_NAME="Default"
OS_PROJECT_DOMAIN_NAME="Default"

EVENT_CREATE="compute.instance.create.end"
EVENT_DELETE="compute.instance.delete.start"
EVENT_IP_UPDATE="floatingip.update.end"

INTERNAL_DOMAIN="nova"
EXTERNAL_DOMAIN="example.com"

NAMESERVER="localhost"
TTL=1
NSUPDATE_ADD_INTERNAL="\
server {nameserver}\n\
update delete {hostname}.{project}.{internal_domain}. A\n\
update delete {hostname}.{project}.{external_domain}. A\n\
update add {hostname}.{project}.{internal_domain}. {ttl} A {hostaddr}\n\
send"

NSUPDATE_ADD_PUBLIC="\
server {nameserver}\n\
update add {hostname}.{project}.{external_domain}. {ttl} A {hostaddr}\n\
update add {hostname}.{project}.{internal_domain}. {ttl} A {hostaddr_internal}\n\
send"

NSUPDATE_DEL_INTERNAL="\
server {nameserver}\n\
update delete {hostname}.{project}.{internal_domain}. A\n\
update delete {hostname}.{project}.{external_domain}. A\n\
send"

log.basicConfig(filename=LOG_FILE, level=log.INFO, format='%(asctime)s %(message)s')

class DnsUpdater(ConsumerMixin):

    def __init__(self, connection):
        self.connection = connection
        auth = v3.Password(auth_url=OS_AUTH_URL,
                           username=OS_USERNAME,
                           password=OS_PASSWORD,
                           project_name=OS_TENANT_NAME,
                           user_domain_name=OS_USER_DOMAIN_NAME,
                           project_domain_name=OS_PROJECT_DOMAIN_NAME)
        s = session.Session(auth=auth)
        self.nova = client.Client(session=s)
        return

    def get_server_for_ip(self, ip, project_id):
        for server in self.nova.servers.list(search_opts={'all_tenants':1}):
            if server.tenant_id != project_id:
                log.debug("Server tenant doesn't match. Continue...")
                continue
            
            for net, addresses in server.networks.items():
                for a in addresses:
                    if ip == a:
                        return server
        return ""

    def get_consumers(self, consumer, channel):
        exchange_nova = Exchange(EXCHANGE_NAME_NOVA, type="topic", durable=False)
        exchange_neutron = Exchange(EXCHANGE_NAME_NEUTRON, type="topic", durable=False)
        queue_nova = Queue(QUEUE_NAME, exchange_nova, routing_key = ROUTING_KEY, durable=False, auto_delete=True, no_ack=True)
        queue_neutron = Queue(QUEUE_NAME, exchange_neutron, routing_key = ROUTING_KEY, durable=False, auto_delete=True, no_ack=True)
        return [ consumer([queue_nova, queue_neutron], callbacks = [ self.on_message ]) ]

    def on_message(self, body, message):
        try:
            self._handle_message(body)
        except Exception, e:
            log.info(repr(e))

    def _handle_message(self, body):
        log.debug('Body: %r' % body)
        jbody = json.loads(body["oslo.message"])
        event_type = jbody["event_type"]
        log.debug("Event type: {}".format(event_type))
        project = jbody["_context_project_name"]
        project_id = jbody["_context_project_id"]
        hostaddr_internal = ""
        if event_type in [ EVENT_CREATE, EVENT_DELETE, EVENT_IP_UPDATE ]:
            if event_type == EVENT_CREATE:
                hostname = jbody["payload"]["hostname"]
                hostaddr = jbody["payload"]["fixed_ips"][0]["address"]
                nsupdate_script = NSUPDATE_ADD_INTERNAL
                log.info("Adding {}.{}.internal.seeburger.de {}".format(hostname, project, hostaddr))
                user = jbody["_context_user_name"]
                user_id = jbody["_context_user_id"]
                server_id = jbody["payload"]["instance_id"]
                log.debug("Instance ID: {}, User: {}, User ID: {}, Project: {}, Project ID: {}".format(server_id, user, user_id, project, project_id))
                server = self.nova.servers.get(server_id)
                self.nova.servers.set_meta_item(server, "project", project)
                self.nova.servers.set_meta_item(server, "project_id", project_id)
                self.nova.servers.set_meta_item(server, "user", user)
                self.nova.servers.set_meta_item(server, "user_id", user_id)
                self.nova.servers.set_meta_item(server, "ip", hostaddr)
                self.nova.servers.set_meta_item(server, "hostname", hostname)
            elif event_type == EVENT_DELETE:
                hostname = jbody["payload"]["hostname"]
                hostaddr = ""
                nsupdate_script = NSUPDATE_DEL_INTERNAL
                log.info("Deleting {}.{}[.internal].seeburger.de".format(hostname, project))
            elif event_type == EVENT_IP_UPDATE:
                hostaddr = jbody["payload"]["floatingip"]["floating_ip_address"]
                ip = jbody["payload"]["floatingip"]["fixed_ip_address"]
                if ip == None:
                    log.info("Disaccotiated floating ip {}. Do nothing for now...".format(hostaddr))
                    return

                if not hostaddr.isspace():
                    server = self.get_server_for_ip(ip, project_id)
                    hostaddr_internal = ip
                    hostname = server.name
                    nsupdate_script = NSUPDATE_ADD_PUBLIC
                log.info("Adding {}.{}.dev.seeburger.de {}".format(hostname, project, hostaddr))
            else:
                log.error("Unknown event type: {} - Do nothing".format(event_type))
                return
            p = Popen(['nsupdate'], stdin=PIPE)
            input = nsupdate_script.format(nameserver=NAMESERVER, hostname=hostname, ttl=TTL,
                                           internal_domain=INTERNAL_DOMAIN, external_domain=EXTERNAL_DOMAIN,
                                           hostaddr=hostaddr, hostaddr_internal=hostaddr_internal, project=project)
            log.debug("Executing nsupdate {}".format(input))
            p.communicate(input=input)


if __name__ == "__main__":
    log.info("Connecting to broker {}".format(BROKER_URI))
    with BrokerConnection(BROKER_URI) as connection:
        DnsUpdater(connection).run()
