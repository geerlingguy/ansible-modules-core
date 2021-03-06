#!/usr/bin/python
# This file is part of Ansible
#
# Ansible is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Ansible is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Ansible.  If not, see <http://www.gnu.org/licenses/>.

DOCUMENTATION = '''
---
module: iam
short_description: Manage IAM users, groups, roles and keys
description:
     - Allows for the management of IAM users, groups, roles and access keys.
version_added: "2.0"
options:
  iam_type:
    description:
      - Type of IAM resource
    required: true
    default: null
    choices: [ "user", "group", "role"]
  name:
    description:
      - Name of IAM resource to create or identify
    required: true
  new_name:
    description:
      - When state is update, will replace name with new_name on IAM resource
    required: false
    default: null
  new_path:
    description:
      - When state is update, will replace the path with new_path on the IAM resource
    required: false
    default: null
  state:
    description:
      - Whether to create, delete or update the IAM resource. Note, roles cannot be updated.
    required: true
    default: null
    choices: [ "present", "absent", "update" ]
  path:
    description:
      - When creating or updating, specify the desired path of the resource. If state is present, it will replace the current path to match what is passed in when they do not match.
    required: false
    default: "/"
  access_key_state:
    description:
      - When type is user, it creates, removes, deactivates or activates a user's access key(s). Note that actions apply only to keys specified.
    required: false
    default: null
    choices: [ "create", "remove", "active", "inactive"]
  key_count:
    description:
      - When access_key_state is create it will ensure this quantity of keys are present. Defaults to 1.
    required: false
    default: '1'
  access_key_ids:
    description:
      - A list of the keys that you want impacted by the access_key_state paramter.
  groups:
    description:
      - A list of groups the user should belong to. When update, will gracefully remove groups not listed.
    required: false
    default: null
  password:
    description:
      - When type is user and state is present, define the users login password. Also works with update. Note that always returns changed.
    required: false
    default: null
  update_password:
    required: false
    default: always
    choices: ['always', 'on_create']
    description:
     - C(always) will update passwords if they differ.  C(on_create) will only set the password for newly created users.
  aws_secret_key:
    description:
      - AWS secret key. If not set then the value of the AWS_SECRET_KEY environment variable is used.
    required: false
    default: null
    aliases: [ 'ec2_secret_key', 'secret_key' ]
  aws_access_key:
    description:
      - AWS access key. If not set then the value of the AWS_ACCESS_KEY environment variable is used.
    required: false
    default: null
    aliases: [ 'ec2_access_key', 'access_key' ]
notes:
  - 'Currently boto does not support the removal of Managed Policies, the module will error out if your user/group/role has managed policies when you try to do state=absent. They will need to be removed manually.'
author:
    - "Jonathan I. Davila (@defionscode)"
    - "Paul Seiffert (@seiffert)"
extends_documentation_fragment:
    - aws
    - ec2
'''

EXAMPLES = '''
# Basic user creation example
tasks:
- name: Create two new IAM users with API keys
  iam:
    iam_type: user
    name: "{{ item }}"
    state: present
    password: "{{ temp_pass }}"
    access_key_state: create
  with_items:
    - jcleese
    - mpython

# Advanced example, create two new groups and add the pre-existing user
# jdavila to both groups.
task:
- name: Create Two Groups, Mario and Luigi
  iam:
    iam_type: group
    name: "{{ item }}"
    state: present
  with_items:
     - Mario
     - Luigi
  register: new_groups

- name:
  iam:
    iam_type: user
    name: jdavila
    state: update
    groups: "{{ item.created_group.group_name }}"
  with_items: new_groups.results

'''

import json
import itertools
import sys
try:
    import boto
    import boto.iam
    import boto.ec2
    HAS_BOTO = True
except ImportError:
   HAS_BOTO = False

def boto_exception(err):
    '''generic error message handler'''
    if hasattr(err, 'error_message'):
        error = err.error_message
    elif hasattr(err, 'message'):
        error = err.message
    else:
        error = '%s: %s' % (Exception, err)

    return error


def create_user(module, iam, name, pwd, path, key_state, key_count):
    key_qty = 0
    keys = []
    try:
        user_meta = iam.create_user(
            name, path).create_user_response.create_user_result.user
        changed = True
        if pwd is not None:
            pwd = iam.create_login_profile(name, pwd)
        if key_state in ['create']:
            if key_count:
                while key_count > key_qty:
                    keys.append(iam.create_access_key(
                        user_name=name).create_access_key_response.\
                        create_access_key_result.\
                        access_key)
                    key_qty += 1
        else:
            keys = None
    except boto.exception.BotoServerError, err:
        module.fail_json(changed=False, msg=str(err))
    else:
        user_info = dict(created_user=user_meta, password=pwd, access_keys=keys)
        return (user_info, changed)


def delete_user(module, iam, name):
    del_meta = ''
    try:
        current_keys = [ck['access_key_id'] for ck in
            iam.get_all_access_keys(name).list_access_keys_result.access_key_metadata]
        for key in current_keys:
            iam.delete_access_key(key, name)
        try:
            login_profile = iam.get_login_profiles(name).get_login_profile_response
        except boto.exception.BotoServerError, err:
            error_msg = boto_exception(err)
            if ('Cannot find Login Profile') in error_msg:

               del_meta = iam.delete_user(name).delete_user_response
            else:
              iam.delete_login_profile(name)
              del_meta = iam.delete_user(name).delete_user_response
    except Exception as ex:
        module.fail_json(changed=False, msg="delete failed %s" %ex)
        if ('must detach all policies first') in error_msg:
            for policy in iam.get_all_user_policies(name).list_user_policies_result.policy_names:
                iam.delete_user_policy(name, policy)
            try:
                del_meta = iam.delete_user(name)
            except boto.exception.BotoServerError, err:
                error_msg = boto_exception(err)
                if ('must detach all policies first') in error_msg:
                      module.fail_json(changed=changed, msg="All inline polices have been removed. Though it appears"
                                                            "that %s has Managed Polices. This is not "
                                                            "currently supported by boto. Please detach the polices "
                                                            "through the console and try again." % name)
                else:
                    module.fail_json(changed=changed, msg=str(error_msg))
            else:
                changed = True
                return del_meta, name, changed
    else:
        changed = True
        return del_meta, name, changed


def update_user(module, iam, name, new_name, new_path, key_state, key_count, keys, pwd, updated):
    changed = False
    name_change = False
    if updated and new_name:
        name = new_name
    try:
        current_keys, status = \
            [ck['access_key_id'] for ck in
             iam.get_all_access_keys(name).list_access_keys_result.access_key_metadata],\
            [ck['status'] for ck in
                iam.get_all_access_keys(name).list_access_keys_result.access_key_metadata]
        key_qty = len(current_keys)
    except boto.exception.BotoServerError, err:
        error_msg = boto_exception(err)
        if 'cannot be found' in error_msg and updated:
            current_keys, status = \
            [ck['access_key_id'] for ck in
             iam.get_all_access_keys(new_name).list_access_keys_result.access_key_metadata],\
            [ck['status'] for ck in
                iam.get_all_access_keys(new_name).list_access_keys_result.access_key_metadata]
            name = new_name
        else:
            module.fail_json(changed=False, msg=str(err))

    updated_key_list = {}

    if new_name or new_path:
        c_path = iam.get_user(name).get_user_result.user['path']
        if (name != new_name) or (c_path != new_path):
            changed = True
            try:
                if not updated:
                    user = iam.update_user(
                        name, new_user_name=new_name, new_path=new_path).update_user_response.response_metadata
                else:
                    user = iam.update_user(
                        name, new_path=new_path).update_user_response.response_metadata
                user['updates'] = dict(
                    old_username=name, new_username=new_name, old_path=c_path, new_path=new_path)
            except boto.exception.BotoServerError, err:
                error_msg = boto_exception(err)
                module.fail_json(changed=False, msg=str(err))
            else:
                if not updated:
                    name_change = True

    if pwd:
        try:
            iam.update_login_profile(name, pwd)
            changed = True
        except boto.exception.BotoServerError:
            try:
                iam.create_login_profile(name, pwd)
                changed = True
            except boto.exception.BotoServerError, err:
                error_msg = boto_exception(str(err))
                if 'Password does not conform to the account password policy' in error_msg:
                    module.fail_json(changed=False, msg="Passsword doesn't conform to policy")
                else:
                    module.fail_json(msg=error_msg)

    if key_state == 'create':
        try:
            while key_count > key_qty:
                new_key = iam.create_access_key(
                    user_name=name).create_access_key_response.create_access_key_result.access_key
                key_qty += 1
                changed = True

        except boto.exception.BotoServerError, err:
            module.fail_json(changed=False, msg=str(err))

    if keys and key_state:
        for access_key in keys:
            if access_key in current_keys:
                for current_key, current_key_state in zip(current_keys, status):
                    if key_state != current_key_state.lower():
                        try:
                            iam.update_access_key(
                                access_key, key_state.capitalize(), user_name=name)
                        except boto.exception.BotoServerError, err:
                            module.fail_json(changed=False, msg=str(err))
                        else:
                            changed = True

                if key_state == 'remove':
                    try:
                        iam.delete_access_key(access_key, user_name=name)
                    except boto.exception.BotoServerError, err:
                        module.fail_json(changed=False, msg=str(err))
                    else:
                        changed = True

    try:
        final_keys, final_key_status = \
            [ck['access_key_id'] for ck in
             iam.get_all_access_keys(name).
             list_access_keys_result.
             access_key_metadata],\
            [ck['status'] for ck in
                iam.get_all_access_keys(name).
                list_access_keys_result.
                access_key_metadata]
    except boto.exception.BotoServerError, err:
        module.fail_json(changed=changed, msg=str(err))

    for fk, fks in zip(final_keys, final_key_status):
        updated_key_list.update({fk: fks})

    return name_change, updated_key_list, changed


def set_users_groups(module, iam, name, groups, updated=None,
new_name=None):
    """ Sets groups for a user, will purge groups not explictly passed, while
        retaining pre-existing groups that also are in the new list.
    """
    changed = False

    if updated:
        name = new_name

    try:
        orig_users_groups = [og['group_name'] for og in iam.get_groups_for_user(
            name).list_groups_for_user_result.groups]
        remove_groups = [
            rg for rg in frozenset(orig_users_groups).difference(groups)]
        new_groups = [
            ng for ng in frozenset(groups).difference(orig_users_groups)]
    except boto.exception.BotoServerError, err:
        module.fail_json(changed=changed, msg=str(err))
    else:
        if len(orig_users_groups) > 0:
            for new in new_groups:
                iam.add_user_to_group(new, name)
            for rm in remove_groups:
                iam.remove_user_from_group(rm, name)
        else:
            for group in groups:
                try:
                    iam.add_user_to_group(group, name)
                except boto.exception.BotoServerError, err:
                    error_msg = boto_exception(err)
                    if ('The group with name %s cannot be found.' % group) in error_msg:
                        module.fail_json(changed=False, msg="Group %s doesn't exist" % group)


    if len(remove_groups) > 0 or len(new_groups) > 0:
        changed = True

    return (groups, changed)


def create_group(module=None, iam=None, name=None, path=None):
    changed = False
    try:
        iam.create_group(
            name, path).create_group_response.create_group_result.group
    except boto.exception.BotoServerError, err:
        module.fail_json(changed=changed, msg=str(err))
    else:
        changed = True
    return name, changed


def delete_group(module=None, iam=None, name=None):
    changed = False
    try:
        iam.delete_group(name)
    except boto.exception.BotoServerError, err:
        error_msg = boto_exception(err)
        if ('must detach all policies first') in error_msg:
            for policy in iam.get_all_group_policies(name).list_group_policies_result.policy_names:
                iam.delete_group_policy(name, policy)
            try:
                iam.delete_group(name)
            except boto.exception.BotoServerError, err:
                error_msg = boto_exception(err)
                if ('must detach all policies first') in error_msg:
                      module.fail_json(changed=changed, msg="All inline polices have been removed. Though it appears"
                                                            "that %s has Managed Polices. This is not "
                                                            "currently supported by boto. Please detach the polices "
                                                            "through the console and try again." % name)
                else:
                    module.fail_json(changed=changed, msg=str(err))
            else:
                changed = True
    else:
        changed = True
    return changed, name

def update_group(module=None, iam=None, name=None, new_name=None, new_path=None):
    changed = False
    try:
        current_group_path = iam.get_group(
            name).get_group_response.get_group_result.group['path']
        if new_path:
            if current_group_path != new_path:
                iam.update_group(name, new_path=new_path)
                changed = True
        if new_name:
            if name != new_name:
                iam.update_group(name, new_group_name=new_name, new_path=new_path)
                changed = True
                name = new_name
    except boto.exception.BotoServerError, err:
        module.fail_json(changed=changed, msg=str(err))

    return changed, name, new_path, current_group_path


def create_role(module, iam, name, path, role_list, prof_list):
    changed = False
    try:
        if name not in role_list:
            changed = True
            iam.create_role(
                name, path=path).create_role_response.create_role_result.role.role_name

            if name not in prof_list:
                iam.create_instance_profile(name, path=path)
                iam.add_role_to_instance_profile(name, name)
    except boto.exception.BotoServerError, err:
        module.fail_json(changed=changed, msg=str(err))
    else:
        updated_role_list = [rl['role_name'] for rl in iam.list_roles().list_roles_response.
                             list_roles_result.roles]
    return changed, updated_role_list


def delete_role(module, iam, name, role_list, prof_list):
    changed = False
    try:
        if name in role_list:
            cur_ins_prof = [rp['instance_profile_name'] for rp in
                            iam.list_instance_profiles_for_role(name).
                            list_instance_profiles_for_role_result.
                            instance_profiles]
            for profile in cur_ins_prof:
                iam.remove_role_from_instance_profile(profile, name)
            try:
              iam.delete_role(name)
            except boto.exception.BotoServerError, err:
              error_msg = boto_exception(err)
              if ('must detach all policies first') in error_msg:
                for policy in iam.list_role_policies(name).list_role_policies_result.policy_names:
                  iam.delete_role_policy(name, policy)
              try:
                iam.delete_role(name)
              except boto.exception.BotoServerError, err:
                  error_msg = boto_exception(err)
                  if ('must detach all policies first') in error_msg:
                      module.fail_json(changed=changed, msg="All inline polices have been removed. Though it appears"
                                                            "that %s has Managed Polices. This is not "
                                                            "currently supported by boto. Please detach the polices "
                                                            "through the console and try again." % name)
                  else:
                      module.fail_json(changed=changed, msg=str(err))
              else:
                changed = True

            else:
                changed = True

        for prof in prof_list:
            if name == prof:
                iam.delete_instance_profile(name)
    except boto.exception.BotoServerError, err:
        module.fail_json(changed=changed, msg=str(err))
    else:
        updated_role_list = [rl['role_name'] for rl in iam.list_roles().list_roles_response.
                             list_roles_result.roles]
    return changed, updated_role_list


def main():
    argument_spec = ec2_argument_spec()
    argument_spec.update(dict(
        iam_type=dict(
            default=None, required=True, choices=['user', 'group', 'role']),
        groups=dict(type='list', default=None, required=False),
        state=dict(
            default=None, required=True, choices=['present', 'absent', 'update']),
        password=dict(default=None, required=False, no_log=True),
        update_password=dict(default='always', required=False, choices=['always', 'on_create']),
        access_key_state=dict(default=None, required=False, choices=[
            'active', 'inactive', 'create', 'remove',
            'Active', 'Inactive', 'Create', 'Remove']),
        access_key_ids=dict(type='list', default=None, required=False),
        key_count=dict(type='int', default=1, required=False),
        name=dict(default=None, required=False),
        new_name=dict(default=None, required=False),
        path=dict(default='/', required=False),
        new_path=dict(default=None, required=False)
    )
    )

    module = AnsibleModule(
        argument_spec=argument_spec,
        mutually_exclusive=[],
    )

    if not HAS_BOTO:
       module.fail_json(msg='This module requires boto, please install it')

    state = module.params.get('state').lower()
    iam_type = module.params.get('iam_type').lower()
    groups = module.params.get('groups')
    name = module.params.get('name')
    new_name = module.params.get('new_name')
    password = module.params.get('password')
    update_pw = module.params.get('update_password')
    path = module.params.get('path')
    new_path = module.params.get('new_path')
    key_count = module.params.get('key_count')
    key_state = module.params.get('access_key_state')
    if key_state:
        key_state = key_state.lower()
        if any([n in key_state for n in ['active', 'inactive']]) and not key_ids:
            module.fail_json(changed=False, msg="At least one access key has to be defined in order"
                                                " to use 'active' or 'inactive'")
    key_ids = module.params.get('access_key_ids')

    if iam_type == 'user' and module.params.get('password') is not None:
        pwd = module.params.get('password')
    elif iam_type != 'user' and module.params.get('password') is not None:
        module.fail_json(msg="a password is being specified when the iam_type "
                             "is not user. Check parameters")
    else:
        pwd = None

    if iam_type != 'user' and (module.params.get('access_key_state') is not None or
                               module.params.get('access_key_id') is not None):
        module.fail_json(msg="the IAM type must be user, when IAM access keys "
                             "are being modified. Check parameters")

    if iam_type == 'role' and state == 'update':
        module.fail_json(changed=False, msg="iam_type: role, cannot currently be updated, "
                             "please specificy present or absent")

    region, ec2_url, aws_connect_kwargs = get_aws_connection_info(module)

    try:
        if region:
            iam = boto.iam.connect_to_region(region, **aws_connect_kwargs)
        else:
            iam = boto.iam.connection.IAMConnection(**aws_connect_kwargs)
    except boto.exception.NoAuthHandlerFound, e:
        module.fail_json(msg=str(e))

    result = {}
    changed = False

    try:
        orig_group_list = [gl['group_name'] for gl in iam.get_all_groups().
                list_groups_result.
                groups]

        orig_user_list = [ul['user_name'] for ul in iam.get_all_users().
                list_users_result.
                users]

        orig_role_list = [rl['role_name'] for rl in iam.list_roles().list_roles_response.
                list_roles_result.
                roles]

        orig_prof_list = [ap['instance_profile_name'] for ap in iam.list_instance_profiles().
                list_instance_profiles_response.
                list_instance_profiles_result.
                instance_profiles]
    except boto.exception.BotoServerError, err:
        module.fail_json(msg=err.message)

    if iam_type == 'user':
        been_updated = False
        user_groups = None
        user_exists = any([n in [name, new_name] for n in orig_user_list])
        if user_exists:
            current_path = iam.get_user(name).get_user_result.user['path']
            if not new_path and current_path != path:
                new_path = path
                path = current_path

        if state == 'present' and not user_exists and not new_name:
            (meta, changed) = create_user(
                module, iam, name, password, path, key_state, key_count)
            keys = iam.get_all_access_keys(name).list_access_keys_result.\
                access_key_metadata
            if groups:
                (user_groups, changed) = set_users_groups(
                    module, iam, name, groups, been_updated, new_name)
            module.exit_json(
                user_meta=meta, groups=user_groups, keys=keys, changed=changed)

        elif state in ['present', 'update'] and user_exists:
            if update_pw == 'on_create':
                password = None
            if name not in orig_user_list and new_name in orig_user_list:
                been_updated = True
            name_change, key_list, user_changed = update_user(
                module, iam, name, new_name, new_path, key_state, key_count, key_ids, password, been_updated)
            if name_change and new_name:
                orig_name = name
                name = new_name
            if groups:
                user_groups, groups_changed = set_users_groups(
                    module, iam, name, groups, been_updated, new_name)
                if groups_changed == user_changed:
                    changed = groups_changed
                else:
                    changed = True
            else:
                changed = user_changed
            if new_name and new_path:
                module.exit_json(changed=changed, groups=user_groups, old_user_name=orig_name,
                                 new_user_name=new_name, old_path=path, new_path=new_path, keys=key_list)
            elif new_name and not new_path and not been_updated:
                module.exit_json(
                    changed=changed, groups=user_groups, old_user_name=orig_name, new_user_name=new_name, keys=key_list)
            elif new_name and not new_path and been_updated:
                module.exit_json(
                    changed=changed, groups=user_groups, user_name=new_name, keys=key_list, key_state=key_state)
            elif not new_name and new_path:
                module.exit_json(
                    changed=changed, groups=user_groups, user_name=name, old_path=path, new_path=new_path, keys=key_list)
            else:
                module.exit_json(
                    changed=changed, groups=user_groups, user_name=name, keys=key_list)

        elif state == 'update' and not user_exists:
            module.fail_json(
                msg="The user %s does not exit. No update made." % name)

        elif state == 'absent':
            if user_exists:
                try:
                   set_users_groups(module, iam, name, '')
                   del_meta, name, changed = delete_user(module, iam, name)
                   module.exit_json(deleted_user=name, changed=changed)

                except Exception as ex:
                       module.fail_json(changed=changed, msg=str(ex))
            else:
                module.exit_json(
                    changed=False, msg="User %s is already absent from your AWS IAM users" % name)

    elif iam_type == 'group':
        group_exists = name in orig_group_list

        if state == 'present' and not group_exists:
            new_group, changed = create_group(iam=iam, name=name, path=path)
            module.exit_json(changed=changed, group_name=new_group)
        elif state in ['present', 'update'] and group_exists:
            changed, updated_name, updated_path, cur_path = update_group(
                iam=iam, name=name, new_name=new_name, new_path=new_path)

            if new_path and new_name:
                module.exit_json(changed=changed, old_group_name=name,
                                 new_group_name=updated_name, old_path=cur_path,
                                 new_group_path=updated_path)

            if new_path and not new_name:
                module.exit_json(changed=changed, group_name=name,
                                 old_path=cur_path,
                                 new_group_path=updated_path)

            if not new_path and new_name:
                module.exit_json(changed=changed, old_group_name=name,
                                 new_group_name=updated_name, group_path=cur_path)

            if not new_path and not new_name:
                module.exit_json(
                    changed=changed, group_name=name, group_path=cur_path)

        elif state == 'update' and not group_exists:
            module.fail_json(
                changed=changed, msg="Update Failed. Group %s doesn't seem to exit!" % name)

        elif state == 'absent':
            if name in orig_group_list:
                removed_group, changed = delete_group(iam=iam, name=name)
                module.exit_json(changed=changed, delete_group=removed_group)
            else:
                module.exit_json(changed=changed, msg="Group already absent")

    elif iam_type == 'role':
        role_list = []
        if state == 'present':
            changed, role_list = create_role(
                module, iam, name, path, orig_role_list, orig_prof_list)
        elif state == 'absent':
            changed, role_list = delete_role(
                module, iam, name, orig_role_list, orig_prof_list)
        elif state == 'update':
            module.fail_json(
                changed=False, msg='Role update not currently supported by boto.')
        module.exit_json(changed=changed, roles=role_list)

from ansible.module_utils.basic import *
from ansible.module_utils.ec2 import *

main()
