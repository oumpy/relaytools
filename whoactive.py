#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from collections import defaultdict
import os
import datetime
from slack import WebClient
import argparse
import random

slacktoken_file = 'slack_token'

inactive_bound = datetime.timedelta(days=100)
relayhistory_bound = datetime.timedelta(days=200)
interval = datetime.timedelta(days=3)
margin = datetime.timedelta(hours=6)
marginprob = 0.05

excluded_members = {'USLACKBOT'}

relaychannel_name = 'リレー投稿'
channel_name = 'test1' # for logging. To disable, set to ''.
appdir = '/var/relaytools/'
base_dir = os.environ['HOME'] + appdir
presence_dir = base_dir + 'members_presence/'
relayhistory_dir = base_dir + 'post_history/'
presence_file_format = '{}' # member ID.
relayhistory_file_format = '{}' # member ID.
excluded_members_file = 'presence_excluded_members.txt'
inactive_members_file = 'inactive_members.txt' # this file is updated automatically.

# ADfirst = datetime.datetime(1,1,1) # AD1.1.1 is Monday
UNIXorigin = datetime.datetime(1970,1,1)

sleep_message = """\
<@{}> さん

ここしばらく、あたなの会Slackへのリレー投稿や、アクティブなログイン状態を確認できません。
戻ってこられるまでの間、あなたを休眠会員として取り扱います。
（長期にわたる場合、退会ご意向をお尋ねしたり推認させていただく場合があります。）

会の活動にまた復帰していただけることをお待ちしています。"""

wake_message = """\
<@{}> さん

おひさしぶりです！
あなたはしばらく休眠会員となっていましたが、ただいま指定を解除されました。
戻ってきていただき、ありがとうございます。

リレー投稿の巡回を再開します。わからないことは何でも幹部にお尋ねください。
よろしくお願いします！"""

sleep_log_message = """\
<@{}> さんのリレー投稿・アクティブ状態を長期間確認できません。休眠会員に指定します。"""

wake_log_message = """\
<@{}> さんのアクセスを久しぶりに確認しました。休眠会員の指定を解除します。お帰りなさい！"""

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

def post_message(client, channel, message):
    params={
        'channel': channel,
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
    parser.add_argument('--checkrelay', help='check the relay posts on Slack.',
                        action='store_true')
    parser.add_argument('--show', help='show the latest presences.',
                        action='store_true')
    parser.add_argument('--showrelay', help='show the latest relay-post time.',
                        action='store_true')
    parser.add_argument('--postlog', help='post logs of changes of status to the channel.',
                        action='store_true')
    parser.add_argument('-n', '--notify', help='notify change of status to the people concerned.',
                        action='store_true')
    parser.add_argument('-c', '--channel', default=channel_name,
                        help='slack channel to post. Default: \'{}\'.'.format(channel_name))
    parser.add_argument('--relaychannel', default=relaychannel_name,
                        help='slack relay-post channel. Default: \'{}\'.'.format(relaychannel_name))
    parser.add_argument('--touch', default=None,
                        help='record an access from the given user ID now.')
    parser.add_argument('--slacktoken', default=None,
                        help='slack bot token.')
    args = parser.parse_args()

    channel_name = args.channel

    slacktoken_file_path = base_dir + slacktoken_file
    presence_file_path_format = presence_dir + presence_file_format
    relayhistory_file_path_format = relayhistory_dir + relayhistory_file_format
    excluded_members_file_path = base_dir + excluded_members_file
    inactive_members_file_path = base_dir + inactive_members_file

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
    relaychannel_id = get_channel_id(web_client, relaychannel_name)

    if os.path.exists(excluded_members_file_path):
        with open(excluded_members_file_path, 'r') as f:
            lines = f.readlines()
            for line in lines:
                excluded_members.add(line.rstrip().split()[1])
    all_members = web_client.api_call('users.list', params={})['members']
    name = dict()
    user_updated = dict()
    for member in all_members:
        if bool(member['is_bot']):
            excluded_members.add(member['id'])
        name[member['id']] = member['profile']['display_name'] or member['profile']['real_name']
        user_updated[member['id']] = datetime.datetime.fromtimestamp(float(member['updated']))
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
            last_stamp[member_id] = user_updated[member_id]
    now_t = datetime.datetime.now()
    now_s = now_t.isoformat()

    if args.touch in members:
        presence_file_path = presence_file_path_format.format(args.touch)
        last_stamp[args.touch] = now_t
        with open(presence_file_path, 'a') as f:
            print(now_s, file=f)

    if args.checkpresence:
        for member_id in members_s:
            presence_file_path = presence_file_path_format.format(member_id)
            inactiveterm = now_t - last_stamp[member_id]
            if inactiveterm >= (interval + margin) or (inactiveterm >= interval and random.random() < marginprob):
                activity = web_client.api_call('users.getPresence', params={'user':member_id})['presence']
                if activity == 'active':
                    last_stamp[member_id] = now_t
                    with open(presence_file_path, 'a') as f:
                        print(now_s, file=f)

    if args.checkrelay or args.showrelay or args.updatealive:
        lastrelay = dict()
        for member_id in members_s:
            relayhistory_file_path = relayhistory_file_path_format.format(member_id)
            if os.path.exists(relayhistory_file_path):
                has_history[member_id] = True
                with open(relayhistory_file_path.format(member_id)) as f:
                    lastrelay[member_id] = datetime.datetime.fromisoformat(f.readlines()[-1].strip())
            else:
                lastrelay[member_id] = UNIXorigin
        finalrelay = max(lastrelay.values())

    if args.checkrelay:
        params={
            'channel': relaychannel_id,
            'oldest': finalrelay.timestamp(),
            'limit': '1000',
        }
        relay_messages = web_client.api_call('conversations.history', params=params)['messages']
        for message in sorted(relay_messages, key=lambda x: float(x['ts'])):
            if 'user' in message:
                writer = message['user']
                ts = datetime.datetime.fromtimestamp(float(message['ts']))
                if writer in members and ts > lastrelay[writer]:
                    lastrelay[writer] = ts
                    with open(relayhistory_file_path_format.format(writer), 'a') as f:
                        print(ts.isoformat(), file=f)

    if args.updatealive:
        if os.path.exists(inactive_members_file_path):
            with open(inactive_members_file_path) as f:
                dead = set(map(lambda s: s.split('\t')[1].strip(), f.readlines()))
        else:
            dead = set()
        dead &= members
        for member_id in members_s:
            if last_stamp[member_id] + inactive_bound > now_t or lastrelay[member_id] + relayhistory_bound > now_t: # alive
                if member_id in dead:
                    if wake_message and args.notify: 
                        post_message(web_client, member_id, wake_message.format(member_id))
                    if channel_id and wake_log_message and args.postlog:
                        post_message(web_client, channel_id, wake_log_message.format(member_id))
                    dead.remove(member_id)
            else: # dead
                if not member_id in dead:
                    if sleep_message and args.notify:
                        post_message(web_client, member_id, sleep_message.format(member_id))
                    if channel_id and sleep_log_message and args.postlog:
                        post_message(web_client, channel_id, sleep_log_message.format(member_id))
                    dead.add(member_id)
        with open(inactive_members_file_path, 'w') as f:
            for dead_id in sorted(dead):
                print(name[dead_id], dead_id, max(last_stamp[dead_id],lastrelay[dead_id]).isoformat(), sep='\t', file=f)

    if args.show:
        for member_id in members_s:
            print(name[member_id], member_id, last_stamp[member_id].isoformat(), sep='\t')

    if args.showrelay:
        for member_id in members_s:
            print(name[member_id], member_id, lastrelay[member_id].isoformat(), sep='\t')
