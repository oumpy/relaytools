#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import requests
from collections import defaultdict
import os
import datetime
from slack import WebClient
import argparse
import random
from bisect import bisect_right

slacktoken_file = 'slack_token'

noactive_bound = datetime.timedelta(days=180)
interval = datetime.timedelta(days=4)
margin = datetime.timedelta(days=1)
marginprob = 0.01

excluded_members = set()

channel_name = 'test1' # for logging. To disable, set to ''.
appdir = '/var/relaytools/'
base_dir = os.environ['HOME'] + appdir
presence_dir = base_dir + 'members_presence/'
presence_file_format = '{}' # member ID.
excluded_members_file = 'presence_excluded_members.txt'
noactive_members_file = 'noactive_members.txt' # this file is updated automatically.

ADfirst = datetime.datetime(1,1,1) # AD1.1.1 is Monday

sleep_message = """
ここしばらく、あたなの会Slackへの投稿やアクセスを確認できません。
戻ってこられるまでの間、あなたを休眠会員として取り扱います。
また会の活動に復帰していただけることをお待ちしています。
"""
wake_message = """
おひさしぶりです！
あなたはしばらく休眠会員となっていましたが、アクティブ会員に再登録されました。
わからないことは何でも幹部にお尋ねください。
よろしくお願いします！
"""
sleep_log_message = """
<@{}> さんからのアクセスが長期間確認できません。
休眠会員に指定します。
"""
wake_log_message = """
<@{}> さんからのアクセスを久しぶりに確認しました。
休眠会員の指定を解除します。
"""

def get_channel_list(client, limit=200):
    params = {
        'exclude_archived': 'true',
        'types': 'public_channel',
        'limit': str(limit),
        }
    channels = client.api_call('conversations.list', params=params)
    if channels['ok']:
        return channels['channels']
    else:
        return None

def get_channel_id(client, channel_name):
    channels = filter(lambda x: x['name']==channel_name , get_channel_list(client))
    target = None
    for c in channels:
        if target is not None:
            break
        else:
            target = c
    if target is None:
        return None
    else:
        return target['id']

def send_message(client, user, message):
    params={
        'channel': user,
        'text': message,
    }
    response = client.api_call(
        'chat.postMessage',
        params=params
    )
    return response


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--updatealive', help='update the list of who are alive.',
                        action='store_true')
    parser.add_argument('--checkpresence', help='check the current presences on Slack.',
                        action='store_true')
    parser.add_argument('--show', help='show the latest presences.',
                        action='store_true')
    parser.add_argument('-c', '--channel', default=channel_name,
                        help='slack channel to post. Default: \'{}\'.'.format(channel_name))
    parser.add_argument('--slacktoken', default=None,
                        help='slack bot token.')
    args = parser.parse_args()

    channel_name = args.channel

    slacktoken_file_path = base_dir + slacktoken_file
    presence_file_path_format = presence_dir + presence_file_format
    excluded_members_file_path = base_dir + excluded_members_file
    noactive_members_file_path = base_dir + noactive_members_file

    if args.slacktoken:
        token = args.slacktoken
    else:
        with open(slacktoken_file_path, 'r') as f:
            token = f.readline().rstrip()
    web_client = WebClient(token=token)
    if channel_name:
        channel_id = get_channel_id(web_client, channel_name)
    else:
        channel_id = ''

    if os.path.exists(excluded_members_file_path):
        with open(excluded_members_file_path, 'r') as f:
            lines = f.readlines()
            for line in lines:
                excluded_members.add(line.rstrip().split()[1])
    all_members = web_client.api_call('users.list', params={})['members']
    name = dict()
    for member in all_members:
        if bool(member['is_bot']):
            excluded_members.add(member['id'])
        name[member['id']] = member['profile']['display_name'] or member['profile']['real_name']
    members = set([member['id'] for member in all_members if not bool(member['deleted'])]) - excluded_members
    members_s = sorted(members)

    last_stamp = dict()
    has_history = defaultdict(bool)
    for member_id in members:
        presence_file_path = presence_file_path_format.format(member_id)
        if os.path.exists(presence_file_path):
            has_history[member_id] = True
            with open(presence_file_path.format(member_id)) as f:
                last_stamp[member_id] = datetime.datetime.fromisoformat(f.readlines()[-1].strip())
        else:
            last_stamp[member_id] = ADfirst
    now_t = datetime.datetime.now()
    now_s = now_t.isoformat()

    if args.checkpresence:
#        print('checkpresence')
        for member_id in members_s:
            presence_file_path = presence_file_path_format.format(member_id)
            noactiveterm = now_t - last_stamp[member_id]
            if noactiveterm >= (interval + margin) or (noactiveterm >= interval and random.random() < marginprob):
                activity = web_client.api_call('users.getPresence', params={'user':member_id})['presence']
#                print(activity)
                if activity == 'active':
                    last_stamp[member_id] = now_t
                    with open(presence_file_path, 'a') as f:
                        print(now_s, file=f)

    if args.show:
        for member_id in members_s:
            print(name[member_id], member_id, last_stamp[member_id].isoformat(), sep='\t')

    if args.updatealive:
        if os.path.exists(noactive_members_file_path):
            with open(noactive_members_file_path) as f:
                dead = set(map(lambda s: s.split('\t')[1].strip(), f.readlines()))
        else:
            dead = set()
        dead &= members
        for member_id in members_s:
            if last_stamp[member_id] + noactive_bound > now_t: # alive
                if member_id in prev_dead:
                    send_message(web_client, member_id, wake_message)
                    if channel_id:
                        send_message(web_client, channel_id, wake_log_message.format(member_id))
                    dead.remove(member_id)
            elif has_history(member_id): # dead
                if not member_id in dead:
                    send_message(web_client, member_id, sleep_message)
                    if channel_id:
                        send_message(web_client, channel_id, sleep_log_message.format(member_id))
                    dead.add(member_id)
        with open(noactive_members_file_path, 'w') as f:
            for dead_id in sorted(dead):
                print(name[dead_id], dead_id, last_stamp[dead_id].isoformat(), sep='\t', file=f)
