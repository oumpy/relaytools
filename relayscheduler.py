#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import requests
from collections import defaultdict
import os
import datetime
from slack import WebClient
import argparse
# from random import randrange
from bisect import bisect_right
import hashlib
import jpholiday

# Example:
# python relayscheduler.py
#

post_to_slack = True
# update_link = True
slacktoken_file = 'slack_token'

lookback_weeks = 8
min_grace = 3 # days: 4の場合、木曜までなら翌週月曜から、それ以後なら翌々週。
relaydays = [0, 1, 2, 3, 4] # cronとは曜日番号が違うので注意。
# 平日に投稿、水曜に発表、月曜にリマインド、を想定。

weekdays = ['月', '火', '水', '木', '金', '土', '日']
custom_holidays = [(1,d) for d in range(1,4)] + [(12,d) for d in range(21,32)]
# 12月21日から1月3日は休日扱い

excluded_members = set()

channel_name = 'リレー投稿'
appdir = '/var/relaytools/'
base_dir = os.environ['HOME'] + appdir
history_dir = base_dir + 'history/'
#memberlist_file = 'memberlist.txt'
ts_file = 'ts-relay'
history_file_format = 'week-{}.txt' # week ID.
excluded_members_file = 'excluded_members.txt'
weeks_str = ['今週', '来週', '再来週']
post_format = {
    'post_header_format' : '＊【{}のリレー投稿 担当者のお知らせ】＊',
    'post_line_format' : '{}月{}日({})：<@{}> さん', # month, day, weekday, writer
    'post_nobody' : '\n{}はお休みです。 :sleeping:', # week_str
    'post_footer' : '\nよろしくお願いします！ :sparkles:', # winner
}
post_format_reminder = {
    'post_header_format' : '*【{}のリレー投稿 リマインダ】*',
}
post_format_list = {
    'post_header_format' : '＊【リレー投稿 {}以降の順番予定】＊',
    'post_line_format' : '<@{}> さん', # month, day, weekday, writer
}

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

def next_writers(members, n, lastwriter):
    def hashf(key):
        return hashlib.sha256(key.encode()).hexdigest()
    hashed_members = [ (hashf(m),m) for m in members ]
    hashed_members.sort()
    N = len(members)
    hashed_lastwriter = (hashf(lastwriter), lastwriter)
    s = bisect_right(hashed_members, hashed_lastwriter)
    return [ hashed_members[(s+i) % N][1] for i in range(n) ]

def to_be_skipped(year, month, day):
    if not args.skipholiday:
        return False
    elif jpholiday.is_holiday(datetime.date(year, month, day)):
        return True
    elif (month, day) in custom_holidays:
        return True


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--noslack', help='do not post to slack.',
                        action='store_true')
    parser.add_argument('-r', '--reminder', help='remind.',
                        action='store_true')
    parser.add_argument('--mute', help='post in thread without showing on channel.',
                        action='store_true')
    parser.add_argument('--solopost',
                        help='post an idependent message out of the thread, not destroying previous thread info',
                        action='store_true')
    parser.add_argument('--list', help='list the future orders.',
                        action='store_true')
    parser.add_argument('--skipholiday', help='skip holidays in Japan.',
                        action='store_true')
    parser.add_argument('-c', '--channel', default=channel_name,
                        help='slack channel to read & post.')
    parser.add_argument('-o', '--outchannel', default=None,
                        help='slack channel to post.')
    parser.add_argument('--slacktoken', default=None,
                        help='slack bot token.')
    parser.add_argument('--date', default=None,
                        help='specify arbitrary date "yyyy-mm-dd" for test.')
    parser.add_argument('--exclude', default=None,
                        help='specify a file or list/tuple of files, containing IDs to be excluded.')
    args = parser.parse_args()

    if args.noslack:
        post_to_slack = False
    channel_name = args.channel

    # memberlist_file_path = base_dir + memberlist_file
    slacktoken_file_path = base_dir + slacktoken_file
    history_file_path_format = history_dir + history_file_format
    excluded_members_files = [excluded_members_file]
    if args.exclude:
        args.exclude = args.exclude.strip()
        if args.exclude[0] in {'(', '['}:
            args.exclude = args.exclude.lstrip('[').lstrip('(').rstrip(']').rstrip(')')
            excluded_members_files += list(map(strip,args.exclude.split(',')))
        elif args.exclude:
            excluded_members_files.append(args.exclude)
    excluded_members_file_paths = list(map(lambda x: base_dir + x, excluded_members_files))

    if args.date:
        today = datetime.date.fromisoformat(args.date)
    else:
        today = datetime.date.today()
    ADfirst = datetime.date(1,1,1) # AD1.1.1 is Monday
    today_id = (today-ADfirst).days
    thisweek_id = today_id // 7
    startday = today + datetime.timedelta(min_grace)
    startday += datetime.timedelta((7-startday.weekday())%7)
    date_id = (startday-ADfirst).days
    week_id = date_id // 7
    history_file_path = history_file_path_format.format(week_id)

    if args.list:
        args.reminder = False
    elif os.path.exists(history_file_path):
        args.reminder = True
    if args.reminder:
        #update_link = False
        for k, v in post_format_reminder.items():
            post_format[k] = v
    elif args.list:
        for k, v in post_format_list.items():
            post_format[k] = v
    for k, v in post_format.items():
        globals()[k] = v

    # read the previous record
    recent_writers = []
    lastweek_id = 0
    for i in range(-lookback_weeks, 1):
        past_id = week_id + i
        hf = history_file_path_format.format(past_id)
        if os.path.exists(hf):
            lastweek_id = past_id
            with open(hf, 'r') as f:
                lines = f.readlines()
                for line in lines:
                    date, person = line.rstrip().split()[:2]
                    recent_writers.append(person)
    if recent_writers:
        last_writer = recent_writers[-1]
    else:
        last_writer = ''

    if args.slacktoken:
        token = args.slacktoken
    else:
        with open(slacktoken_file_path, 'r') as f:
            token = f.readline().rstrip()
    web_client = WebClient(token=token)
    channel_id = get_channel_id(web_client, channel_name)
    my_id = web_client.api_call('auth.test')['user_id']

    writers_dict = dict()
    if args.reminder:
        while week_id >= thisweek_id:
            hf = history_file_path_format.format(week_id)
            if os.path.exists(hf):
                with open(hf, 'r') as f:
                    lines = f.readlines()
                    for line in lines:
                        date, person = line.rstrip().split()[:2]
                        date = int(date)
                        writers_dict[date-date_id] = person
                break
            else:
                week_id -= 1
                date_id -= 7
        else:
            exit()
    else:
        for excluded_members_file_path in excluded_members_file_paths:
            if os.path.exists(excluded_members_file_path):
                with open(excluded_members_file_path, 'r') as f:
                    lines = f.readlines()
                    for line in lines:
                        excluded_members.add(line.rstrip().split('\t')[1])
        channel_members = web_client.api_call('conversations.members', params={'channel':channel_id})['members']
        # ensure I am a member of the channel.
        # channel_info = web_client.api_call('conversations.info', params={'channel':channel_id})['channel']
        # if not channel_info['is_member']:
        #     return
        members = set(channel_members) - excluded_members
        members.discard(my_id)
        if args.list:
            for d, writer in enumerate(next_writers(members, len(members), last_writer)):
                writers_dict[d] = writer
        else:
            writers = next_writers(members, len(relaydays), last_writer)
            i = 0
            for d in relaydays:
                date = startday + datetime.timedelta(d)
                if not to_be_skipped(date.year, date.month, date.day):
                    writers_dict[d] = writers[i]
                    i += 1
            # write the new history
            with open(history_file_path, 'w') as f:
                for d, u in writers_dict.items():
                    print(date_id + d, u, file=f)

    if args.list: week_id = max(week_id, lastweek_id + 1)
    week_str = weeks_str[week_id - thisweek_id]
    post_lines = [post_header_format.format(week_str)]
    if writers_dict:
        for d, writer in writers_dict.items():
            if args.list:
                post_lines.append(post_line_format.format(writer))
            else:
                date = startday + datetime.timedelta(d)
                post_lines.append(post_line_format.format(date.month, date.day, weekdays[d], writer))
        if len(post_lines) > 1:
            post_lines.append(post_footer)
        else:
            post_lines.append(post_nobody.format(week_str))
    else:
        post_lines.append(post_nobody.format(week_str))
    message = '\n'.join(post_lines)

    if post_to_slack:
        if args.outchannel:
            channel_id = get_channel_id(web_client, args.outchannel)
        params={
            'channel': channel_id,
            'text': message,
        }
        os.chdir(history_dir)
        if os.path.isfile(ts_file):
            with open(ts_file, 'r') as f:
                ts = f.readline().rstrip()
                if not args.solopost:
                    params['thread_ts'] = ts
                    if not args.mute:
                        params['reply_broadcast'] = 'True'
        else:
            ts = None
        response = web_client.api_call(
            'chat.postMessage',
            params=params
        )
        posted_data = response.data
        if ts is None:
            ts = posted_data['ts']
            with open(ts_file, 'w') as f:
                print(ts, file=f)
        # elif os.path.isfile(ts_file):
        #     os.remove(ts_file)
    else:
        print('App ID:', my_id)
        print(message)
