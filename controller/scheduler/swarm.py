import re
import time

from django.conf import settings
from docker import Client

from . import AbstractSchedulerClient
from .states import JobState


MATCH = re.compile(
    r'(?P<app>[a-z0-9-]+)_?(?P<version>v[0-9]+)?\.?(?P<c_type>[a-z-_]+)?.(?P<c_num>[0-9]+)')


class SwarmClient(AbstractSchedulerClient):

    def __init__(self, target, auth, options, pkey):
        super(SchedulerClient, self).__init__(target, auth, options, pkey)
        self.target = settings.SWARM_HOST
        # single global connection
        self.registry = settings.REGISTRY_HOST + ':' + settings.REGISTRY_PORT
        self.docker_cli = Client("tcp://{}:2395".format(self.target),
                                 timeout=1200, version='1.17')

    def create(self, name, image, command='', template=None, **kwargs):
        """Create a new container."""
        cimage = self.registry + '/' + image
        affinity = "affinity:container!=~/{}*/".format(re.split(r'_v\d.', name)[0])
        l = locals().copy()
        l.update(re.match(MATCH, name).groupdict())
        mem = kwargs.get('memory', {}).get(l['c_type'])
        if mem:
            mem = mem.lower()
            if mem[-2:-1].isalpha() and mem[-1].isalpha():
                mem = mem[:-1]
        cpu = kwargs.get('cpu', {}).get(l['c_type'])
        self.docker_cli.create_container(image=cimage, name=name,
                                         command=command.encode('utf-8'),
                                         mem_limit=mem,
                                         cpu_shares=cpu,
                                         environment=[affinity],
                                         host_config={'PublishAllPorts': True})

    def start(self, name):
        """Start a container."""
        self.docker_cli.start(name)

    def stop(self, name):
        """Stop a container."""
        self.docker_cli.stop(name)

    def destroy(self, name):
        """Destroy a container."""
        self.stop(name)
        self.docker_cli.remove_container(name)

    def run(self, name, image, entrypoint, command):
        """Run a one-off command."""
        cimage = self.registry + '/' + image
        # use affinity for nodes that already have the image
        affinity = "affinity:image==~{}".format(cimage)
        self.docker_cli.create_container(image=cimage, name=name,
                                         command=command.encode('utf-8'),
                                         environment=[affinity],
                                         entrypoint=[entrypoint])
        time.sleep(2)
        self.start(name)
        rc = 0
        while (True):
            if self._get_container_state(name) == JobState.created:
                break
            time.sleep(1)
        try:
            output = self.docker_cli.logs(name)
            return rc, output
        except:
            rc = 1
            return rc, output

    def _get_container_state(self, name):
        try:
            if self.docker_cli.inspect_container(name)['State']['Running']:
                return JobState.up
            else:
                return JobState.created
        except:
            return JobState.destroyed

    def state(self, name):
        """Display the given job's running state."""
        try:
            for _ in xrange(30):
                return self._get_container_state(name)
                time.sleep(1)
            # FIXME (smothiki): should be able to send JobState.crashed
        except KeyError:
            return JobState.error
        except RuntimeError:
            return JobState.destroyed

    def _get_hostname(self, application_name):
        hostname = settings.UNIT_HOSTNAME
        if hostname == 'default':
            return ''
        elif hostname == 'application':
            # replace underscore with dots, since underscore is not valid in DNS hostnames
            dns_name = application_name.replace('_', '.')
            return dns_name
        elif hostname == 'server':
            raise NotImplementedError
        else:
            raise RuntimeError('Unsupported hostname: ' + hostname)

    def _get_portbindings(self, image):
        dictports = self.docker_cli.inspect_image(image)['ContainerConfig']['ExposedPorts']
        for port in dictports:
            dictports[port] = None
        return dictports

    def _get_ports(self, image):
        dictports = self.docker_cli.inspect_image(image)['ContainerConfig']['ExposedPorts']
        return [int(port.split('/')[0]) for port in dictports]

SchedulerClient = SwarmClient
