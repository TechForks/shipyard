# Copyright 2013 Evan Hazlett and contributors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
from ansi2html import Ansi2HTMLConverter
from django.conf import settings
from hashlib import md5
from queue.models import QUEUE_KEY
import redis
import uuid
import time
import json

def get_redis_connection():
    host = settings.REDIS_HOST
    port = settings.REDIS_PORT
    db = settings.REDIS_DB
    password = settings.REDIS_PASSWORD
    return redis.Redis(host=host, port=port, db=db, password=password)

def get_short_id(container_id):
    return container_id[:12]

def convert_ansi_to_html(text, full=False):
    converted = ''
    try:
        conv = Ansi2HTMLConverter(markup_lines=True, linkify=False, escaped=False)
        converted = conv.convert(text.replace('\n', ' <br/>'), full=full)
    except Exception, e:
        converted = text
    return converted

def generate_console_session(host, container):
    session_id = md5(str(uuid.uuid4())).hexdigest()
    rds = get_redis_connection()
    key = 'console:{0}'.format(session_id)
    docker_host = '{0}:{1}'.format(host.hostname, host.port)
    attach_path = '/v1.3/containers/{0}/attach/ws'.format(container.container_id)
    rds.hmset(key, { 'host': docker_host, 'path': attach_path })
    rds.expire(key, 120)
    return session_id

def update_hipache(app_id=None):
    from applications.models import Application
    if getattr(settings, 'HIPACHE_ENABLED'):
        app = Application.objects.get(id=app_id)
        rds = get_redis_connection()
        with rds.pipeline() as pipe:
            domain_key = 'frontend:{0}'.format(app.domain_name)
            # remove existing
            pipe.delete(domain_key)
            pipe.rpush(domain_key, app.id)
            # add upstreams
            for c in app.containers.all():
                port_proto = "{0}/tcp".format(app.backend_port)
                host_interface = app.host_interface or '0.0.0.0'
                hostname = c.host.public_hostname or \
                        c.host.hostname if host_interface == '0.0.0.0' else \
                        host_interface
                # check for unix socket
                port = c.get_ports()[port_proto][host_interface]
                upstream = '{0}://{1}:{2}'.format(app.protocol, hostname, port)
                pipe.rpush(domain_key, upstream)
            pipe.execute()
            return True
    return False

def remove_hipache_config(domain_name=None):
    if getattr(settings, 'HIPACHE_ENABLED'):
        rds = get_redis_connection()
        domain_key = 'frontend:{0}'.format(domain_name)
        # remove existing
        rds.delete(domain_key)

def queue_host_task(host_id=None, command=None, params={}):
    """
    Queues a host task

    """
    id = str(uuid.uuid4())
    data = {
        'id': id,
        'date': int(time.time()),
        'host_id': host_id,
        'command': command,
        'params': json.dumps(params), # dump to json since redis can't do nested
        'ack': '0'
    }
    rds = get_redis_connection()
    key = QUEUE_KEY.format(id)
    rds.hmset(key, data)
    # set the ttl
    rds.expire(key, settings.HOST_TASK_TTL)
