#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from collections import defaultdict
import os
import datetime
from slack import WebClient
import argparse
import random
import re

slacktoken_file = 'slack_token'

inactive_bound = datetime.timedelta(days=100)
norelay_bound = datetime.timedelta(days=200)
membership_bound = datetime.timedelta(days=550)
interval = datetime.timedelta(days=3)
margin = datetime.timedelta(hours=6)
marginprob = 0.05

excluded_members = {'USLACKBOT'}

relaychannel_name = 'リレー投稿'
channel_name = 'test1' # for logging. To disable, set to ''.
appdir = '/var/relaytools/'
base_dir = os.environ['HOME'] + appdir
presence_dir = base_dir + 'members_presence/'
posthistory_dir = base_dir + 'allpost_history/'
relayhistory_dir = base_dir + 'relaypost_history/'
presence_file_format = '{}' # member ID.
relayhistory_file_format = '{}' # member ID.
posthistory_file_format = '{}' # member ID.
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

お久しぶりです！
あなたはしばらく休眠会員となっていましたが、ただいま指定を解除されました。
戻ってきていただき、ありがとうございます。

リレー投稿の巡回を再開します。わからないことは何でも幹部にお尋ねください。
よろしくお願いします！"""

die_message = """\
<@{0}> さん

会員に必須の活動であるリレー投稿を、規定の18ヶ月間以上、確認できません。
会に留まることを希望される場合、指名に関わらず、すみやかにリレー投稿を行ってください。

よろしくお願いいたします。"""

sleep_log_message = """\
<@{}> さんのリレー投稿・アクティブ状態を長期間確認できません。休眠会員に指定します。"""

wake_log_message = """\
<@{}> さんのアクセスを久しぶりに確認しました。休眠会員の指定を解除します。お帰りなさい！"""

die_log_message = """\
大変残念ですが、規定の18ヶ月間以上、 <@{0}> さんからのリレー投稿がありませんでした。

幹部会は必要な対応を行ってください。
<@{0}> さんは、会に留まることを希望される場合、指名に関わらず、すみやかにリレー投稿を行ってください。

よろしくお願いいたします。"""

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

def get_channel_id(client, channel_name, channel_list=None):
    if channel_list is None:
        channel_list = get_channel_list(client)
    channels = list(filter(lambda x: x['name']==channel_name , channel_list))
    if len(channels):
        return channels[0]['id']
    else:
        return None

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

def tail_b(filename, n=1):
# ref: https://qiita.com/tomatoiscandy/items/02d5c656cc2faa7e35ad
    if n == 1:
        is_list = False
    elif type(n) != int or n < 1:
        raise ValueError('n has to be a positive integer')
    else:
        is_list = True

    chunk_size = 64 * n
    with open(filename, 'rb') as f:
        f.readline()
        left_end = f.tell() - 1
        f.seek(-1, 2)
        while True:
            if f.read(1).strip() != b'':
                right_end = f.tell()
                break
            f.seek(-2, 1)
        unread = right_end - left_end
        num_lines = 0
        line = b''
        while True:
            if unread < chunk_size:
                chunk_size = f.tell() - left_end
            f.seek(-chunk_size, 1)
            chunk = f.read(chunk_size)
            line = chunk + line
            f.seek(-chunk_size, 1)
            unread -= chunk_size
            if b'\n' in chunk:
                num_lines += chunk.count(b'\n')
                if num_lines >= n or not unread:
                    leftmost_blank = re.search(rb'\r?\n', line)
                    line = line[leftmost_blank.end():]
                    line = line.decode()
                    lines = re.split(r'\r?\n', line)
                    result = [list(map(float, line.split(','))) for line in lines[-n:]]
                    if not is_list:
                        return result[-1]
                    else:
                        return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--updatealive', help='update the list of who are alive.',
                        action='store_true')
    parser.add_argument('--checkpresence', help='check the current presences on Slack.',
                        action='store_true')
    parser.add_argument('--checkposts', help='check the posts on Slack (all public channels).',
                        action='store_true')
    parser.add_argument('--checkrelay', help='check the relay posts on Slack.',
                        action='store_true')
    parser.add_argument('--show', help='show the latest presences.',
                        action='store_true')
    parser.add_argument('--showrelay', help='show the latest relay-post time.',
                        action='store_true')
    parser.add_argument('--postlog', help='post logs of changes of status to the channel.',
                        action='store_true')
    parser.add_argument('--judgedead', help='make judgement of complete death.',
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
    relaychannel_name = args.relaychannel

    slacktoken_file_path = base_dir + slacktoken_file
    presence_file_path_format = presence_dir + presence_file_format
    posthistory_file_path_format = posthistory_dir + posthistory_file_format
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
    user_name = dict()
    user_updated = dict()
    for member in all_members:
        if bool(member['is_bot']):
            excluded_members.add(member['id'])
        user_name[member['id']] = member['profile']['display_name'] or member['profile']['real_name']
        user_updated[member['id']] = datetime.datetime.fromtimestamp(float(member['updated']))
    members = set([member['id'] for member in all_members if not bool(member['deleted'])]) - excluded_members
    members_s = sorted(members)

    lastvisit = dict()
    # has_history = defaultdict(bool)
    for member_id in members:
        presence_file_path = presence_file_path_format.format(member_id).format(member_id)
        if os.path.exists(presence_file_path):
            # has_history[member_id] = True
            lastvisit[member_id] = datetime.datetime.fromisoformat(tail_b(presence_file_path).strip())
        else:
            lastvisit[member_id] = user_updated[member_id]
    now_t = datetime.datetime.now()
    now_s = now_t.isoformat()

    if args.touch in members:
        presence_file_path = presence_file_path_format.format(args.touch)
        lastvisit[args.touch] = now_t
        with open(presence_file_path, 'a') as f:
            print(now_s, file=f)

    if args.checkpresence:
        for member_id in members_s:
            presence_file_path = presence_file_path_format.format(member_id)
            inactiveterm = now_t - lastvisit[member_id]
            if inactiveterm >= (interval + margin) or (inactiveterm >= interval and random.random() < marginprob):
                activity = web_client.api_call('users.getPresence', params={'user':member_id})['presence']
                if activity == 'active':
                    lastvisit[member_id] = now_t
                    with open(presence_file_path, 'a') as f:
                        print(now_s, file=f)

    if args.checkposts or args.updatealive:
        firstpost = now_t
        lastpost = defaultdict(lambda: firstpost)
        for member_id in members_s:
            posthistory_file_path = posthistory_file_path_format.format(member_id)
            if os.path.exists(posthistory_file_path):
                with open(posthistory_file_path) as f:
                    head = f.readline().strip()
                    head_t = datetime.datetime.fromisoformat(head)
                    if head_t < firstpost:
                        firstpost = head_t
                tail = tail_b(posthistory_file_path).strip()
                lastpost[member_id] = datetime.datetime.fromisoformat(tail)
        finalpost = max(lastpost.values())

    if args.checkrelay or args.showrelay or args.updatealive:
        firstrelay = now_t
        lastrelay = defaultdict(lambda: firstrelay)
        for member_id in members_s:
            relayhistory_file_path = relayhistory_file_path_format.format(member_id)
            if os.path.exists(relayhistory_file_path):
                # has_history[member_id] = True
                with open(relayhistory_file_path) as f:
                    head = f.readline().strip()
                    head_t = datetime.datetime.fromisoformat(head)
                    if head_t < firstrelay:
                        firstrelay = head_t
                tail = tail_b(relayhistory_file_path).strip()
                lastrelay[member_id] = datetime.datetime.fromisoformat(tail)
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
        inactive = set()
        inactive_level = defaultdict(int)
        if os.path.exists(inactive_members_file_path):
            with open(inactive_members_file_path) as f:
                for line in f.readlines():
                    name, user, ts, level = (line.strip().split('\t') + ['1'])[:4]
                    inactive.add(user)
                    inactive_level[user] = int(level)
        inactive &= members
        for member_id in members_s:
            if args.judgedead and lastrelay[member_id] + membership_bound < now_t: # dead
                if inactive_level[member_id] < 2:
                    if die_message and args.notify:
                        post_message(web_client, member_id, die_message.format(member_id))
                    if channel_id and die_log_message and args.postlog:
                        post_message(web_client, channel_id, die_log_message.format(member_id))
                    inactive.add(member_id)
                    inactive_level[member_id] = 2
            elif lastvisit[member_id] + inactive_bound > now_t or lastrelay[member_id] + norelay_bound > now_t: # alive
                if member_id in inactive:
                    if wake_message and args.notify: 
                        post_message(web_client, member_id, wake_message.format(member_id))
                    if channel_id and wake_log_message and args.postlog:
                        post_message(web_client, channel_id, wake_log_message.format(member_id))
                    inactive.remove(member_id)
                    inactive_level[member_id] = 0
            else: # inactive
                if not member_id in inactive:
                    if sleep_message and args.notify:
                        post_message(web_client, member_id, sleep_message.format(member_id))
                    if channel_id and sleep_log_message and args.postlog:
                        post_message(web_client, channel_id, sleep_log_message.format(member_id))
                    inactive.add(member_id)
                    inactive_level[member_id] = 1
        with open(inactive_members_file_path, 'w') as f:
            for inactive_id in sorted(inactive):
                print(user_name[inactive_id], inactive_id, max(lastvisit[inactive_id],lastrelay[inactive_id]).isoformat(), inactive_level[inactive_id], sep='\t', file=f)

    if args.show:
        for member_id in members_s:
            print(user_name[member_id], member_id, lastvisit[member_id].isoformat(), sep='\t')

    if args.showrelay:
        for member_id in members_s:
            print(user_name[member_id], member_id, lastrelay[member_id].isoformat(), sep='\t')
