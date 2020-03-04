#!/usr/bin/env python3
#
# Copyright (C) 2020 VyOS maintainers and contributors
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 or later as
# published by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import os

from sys import exit
from copy import deepcopy
from subprocess import check_call, CalledProcessError
from vyos.config import Config
from vyos.configdict import list_diff
from vyos import ConfigError

default_config_data = {
    'bind_to_all': 0,
    'deleted': False,
    'vrf_add': [],
    'vrf_existing': [],
    'vrf_remove': []
}

def _cmd(command):
    """
    Run any arbitrary command on the system
    """
    try:
        check_call(command.split())
    except CalledProcessError as e:
        pass
        raise ConfigError(f'Error operationg on VRF: {e}')

def interfaces_with_vrf(match):
    matched = []
    config = Config()
    section = config.get_config_dict('interfaces')
    for type in section:
        interfaces = section[type]
        for name in interfaces:
            interface = interfaces[name]
            if 'vrf' in interface:
                v = interface.get('vrf', '')
                if v == match:
                    matched.append(name)
    return matched

def get_config():
    conf = Config()
    vrf_config = deepcopy(default_config_data)

    cfg_base = ['vrf']
    if not conf.exists(cfg_base):
        # get all currently effetive VRFs and mark them for deletion
        vrf_config['vrf_remove'] = conf.list_effective_nodes(cfg_base + ['name'])
    else:

        # Should services be allowed to bind to all VRFs?
        if conf.exists(['bind-to-all']):
            vrf_config['bind_to_all'] = 1

        # Determine vrf interfaces (currently effective) - to determine which
        # vrf interface is no longer present and needs to be removed
        eff_vrf = conf.list_effective_nodes(cfg_base + ['name'])
        act_vrf = conf.list_nodes(cfg_base + ['name'])
        vrf_config['vrf_remove'] = list_diff(eff_vrf, act_vrf)

        # read in individual VRF definition and build up
        # configuration
        for name in conf.list_nodes(cfg_base + ['name']):
            vrf_inst = {
                'description' : '\0',
                'members': [],
                'name' : name,
                'table' : '',
                'table_mod': False
            }
            conf.set_level(cfg_base + ['name', name])

            if conf.exists(['table']):
                # VRF table can't be changed on demand, thus we need to read in the
                # current and the effective routing table number
                act_table = conf.return_value(['table'])
                eff_table = conf.return_effective_value(['table'])
                vrf_inst['table'] = act_table
                if eff_table and eff_table != act_table:
                    vrf_inst['table_mod'] = True

            if conf.exists(['description']):
                vrf_inst['description'] = conf.return_value(['description'])

            # find member interfaces of this particulat VRF
            vrf_inst['members'] = interfaces_with_vrf(name)

            # append individual VRF configuration to global configuration list
            vrf_config['vrf_add'].append(vrf_inst)

    # check VRFs which need to be removed as they are not allowed to have
    # interfaces attached
    tmp = []
    for name in vrf_config['vrf_remove']:
        vrf_inst = {
            'members': [],
            'name' : name,
        }

        # find member interfaces of this particulat VRF
        vrf_inst['members'] = interfaces_with_vrf(name)

        # append individual VRF configuration to temporary configuration list
        tmp.append(vrf_inst)

    # replace values in vrf_remove with list of dictionaries
    # as we need it in verify() - we can't delete a VRF with members attached
    vrf_config['vrf_remove'] = tmp
    return vrf_config

def verify(vrf_config):
    # ensure VRF is not assigned to any interface
    for vrf in vrf_config['vrf_remove']:
        if len(vrf['members']) > 0:
            raise ConfigError('VRF "{}" can not be deleted as it has active members'.format(vrf['name']))

    # routing table id can't be changed - OS restriction
    for vrf in vrf_config['vrf_add']:
        if vrf['table_mod']:
            raise ConfigError('VRF routing table id modification is not possible')

    # add test to see if routing table already exists or not?

    return None

def generate(vrf_config):
    return None

def apply(vrf_config):
    # https://github.com/torvalds/linux/blob/master/Documentation/networking/vrf.txt

    # set the default VRF global behaviour
    bind_all = vrf_config['bind_to_all']
    _cmd(f'sysctl -wq net.ipv4.tcp_l3mdev_accept={bind_all}')
    _cmd(f'sysctl -wq net.ipv4.udp_l3mdev_accept={bind_all}')

    for vrf_name in vrf_config['vrf_remove']:
        if os.path.isdir(f'/sys/class/net/{vrf_name}'):
            _cmd(f'ip link delete dev {vrf_name}')

    for vrf in vrf_config['vrf_add']:
        name = vrf['name']
        table = vrf['table']

        if not os.path.isdir(f'/sys/class/net/{name}'):
            _cmd(f'ip link add {name} type vrf table {table}')
            _cmd(f'ip link set dev {name} up')
            _cmd(f'ip -4 rule add oif {name} lookup {table}')
            _cmd(f'ip -4 rule add iif {name} lookup {table}')
            _cmd(f'ip -6 rule add oif {name} lookup {table}')
            _cmd(f'ip -6 rule add iif {name} lookup {table}')

        # set VRF description for e.g. SNMP monitoring
        with open(f'/sys/class/net/{name}/ifalias', 'w') as f:
            f.write(vrf['description'])

    return None

if __name__ == '__main__':
    try:
        c = get_config()
        verify(c)
        generate(c)
        apply(c)
    except ConfigError as e:
        print(e)
        exit(1)
