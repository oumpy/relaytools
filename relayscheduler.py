#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import sys
import datetime
from slack_sdk import WebClient
from mattermostdriver import Driver
import argparse
# from random import randrange
from bisect import bisect_right
import hashlib
import jpholiday
import yaml
from collections import defaultdict

# Example:
# python relayscheduler.py
#

post_to_remote = True
# update_link = True
token_file = 'mattermost_token'
config_file = 'relayscheduler_conf.yaml'

lookback_weeks = 8
min_grace = 3 # days: 4の場合、木曜までなら翌週月曜から、それ以後なら翌々週。
relaydays = [0, 1, 2, 3, 4] # cronとは曜日番号が違うので注意。
# 平日に投稿、水曜に発表、月曜にリマインド、を想定。

weekdays = ['月', '火', '水', '木', '金', '土', '日']
custom_holidays = [(1,d) for d in range(1,4)] + [(12,d) for d in range(21,32)]
# 12月21日から1月3日は休日扱い

excluded_members = set()

channel_name = 'リレー投稿'
appdir = 'var/relaytools/'
ts_file = 'ts-relay'
cyclenumber_file = 'cyclenumber'
history_file_format = 'week-{}.tsv' # week ID.
excluded_members_file = 'excluded_members.tsv'
weeks_str = ['今週', '来週', '再来週']
post_format = {
    'post_header_format' : '＊【{}のリレー投稿 担当者のお知らせ】＊',
    'newcycle_line_format' : '({}巡目開始)', # cyclenumber
    'post_line_format' : '{1}月{2}日({3})：<@{0}> さん', # writer, month, day, weekday
    'post_nobody' : '\n{}はお休みです。 :sleeping:', # week_str
    'post_footer' : '\nよろしくお願いします！ :sparkles:', # winner
}
post_format_reminder = {
    'post_header_format' : '*【{}のリレー投稿 リマインダ】*',
}
post_format_list = {
    'post_header_format' : '＊【リレー投稿 {}以降の順番予定】＊',
    'post_line_format' : '<@{}> さん', # writer
}

base_dir = os.path.join(os.environ['HOME'], appdir)
history_dir = os.path.join(base_dir, 'relayorder_history/')

class Manager(object) :
    def __init__(self):
        pass
    def getChannelId(self, team_name, channel_name) :
        return None
    def getTeamId(self, team_name) :
        return None
    def getMyId(self) :
        return None
    def getTeamMembers(self, team_name, channel_name=None) :
        return list()
    def getChannelMembers(self, team_name, channel_name) :
        return list()
    def getAllUsersForTeam(self, team_id) :
        return list()
    def getAllUsersForChannel(self, channel_id) :
        return list()
    def post(self, channel_id, message, **kwargs):
        return None

class MattermostManager(Manager):
    def __init__(self, token, **kwargs):
        options={
            'token' :   token,
        } | kwargs
        self.mmDriver = Driver(options=options)
        # self.mmDriver.users.get_user( user_id='me' )

    def getChannelId(self, channel_name, team_name) :
        # print(channel_name, team_name)
        team_id = self.getTeamId(team_name)
        self.mmDriver.login()
        channel_id = self.mmDriver.channels.get_channel_by_name(team_id, channel_name)['id']
        self.mmDriver.logout()
        return channel_id
        # return self.mmDriver.channels.get_channel_by_name_and_team_name(team_name, channel_name)['id']

    def getTeamId(self, team_name):
        # print(vars(self.mmDriver.teams))
        # print(vars(vars(self.mmDriver.teams)['client']))
        self.mmDriver.login()
        if not self.mmDriver.teams.check_team_exists(team_name):
            return None
        team_id = self.mmDriver.teams.get_team_by_name(team_name)['id']
        self.mmDriver.logout()
        return team_id

    def getMyId(self) :
        self.mmDriver.login()
        my_id = self.mmDriver.users.get_user(user_id='me')['id']
        self.mmDriver.logout()
        return my_id

    def getTeamMembers(self, team_name) :
        # for restricted teams, we need to get the ID first, and
        # for this, we need to have the "name" (as in the URL), not
        # the "display name", as shown in the GUIs:
        team_id = self.getTeamId(team_name)
        self.mmDriver.login()
        team = self.mmDriver.teams.check_team_exists(team_name)
        self.mmDriver.logout()
        if not team['exists'] :
            return None
        users = self._getAllUsersForTeam(team_id)
        return users

    def getChannelMembers(self, channel_name, team_name) :
        # for restricted teams, we need to get the ID first, and
        # for this, we need to have the "name" (as in the URL), not
        # the "display name", as shown in the GUIs:
        channel_id = self.getChannelId(channel_name, team_name)
        users = self.getAllUsersForChannel(channel_id)
        return users

    def getAllUsersForTeam(self, team_id, per_page=200) :
        # get all users for a team
        # with the max of 200 per page, we need to iterate a bit over the pages
        users = []
        pgNo = 0
        def get_users(team_id, pgNo, per_page=per_page):
            self.mmDriver.login()
            users = self.mmDriver.users.get_users(params={
                    'in_team'   :   team_id,
                    'page'      :   str(pgNo),
                    'per_page'  :   per_page,
            })
            self.mmDriver.logout()
            return users
        channelUsers = get_users(team_id, pgNo)
        while channelUsers:
            users += channelUsers
            pgNo += 1
            channelUsers = get_users(team_id, pgNo)
        return users

    def getAllUsersForChannel(self, channel_id, per_page=200) :
        # get all users for a channel
        # with the max of 200 per page, we need to iterate a bit over the pages
        users = []
        pgNo = 0
        def get_users(channel_id, pgNo, per_page=per_page):
            self.mmDriver.login()
            users = self.mmDriver.users.get_users(params={
                    'in_channel':   channel_id,
                    'page'      :   str(pgNo),
                    'per_page'  :   per_page,
            })
            self.mmDriver.logout()
            return users
        channelUsers = get_users(channel_id, pgNo)
        while channelUsers:
            users += channelUsers
            pgNo += 1
            channelUsers = get_users(channel_id, pgNo)
        return users

    def post(self, channel_id, message, **kwargs):
        self.mmDriver.login()
        param = kwargs | {
            'channel_id':   channel_id,
            'message'   :   message,
            }
        response = self.mmDriver.create_post(options=param)
        self.mmDriver.logout()
        return response

class SlackManager(Manager):
    def __init__(self, token):
        self.client = WebClient(token=token)

    def getChannelId(self, channel_name, team_name=None):
        channels = filter(lambda x: x['name']==channel_name , self._get_channel_list())
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

    def _get_channel_list(self, limit=200):
        params = {
            'exclude_archived'  :   'true',
            'types'             :   'public_channel',
            'limit'             :   str(limit),
            }
        channels = self.client.api_call('conversations.list', params=params)
        if channels['ok']:
            return channels['channels']
        else:
            return None

    def getChannelMembers(self, channel_name, team_name=None):
        channel_id = self.getChannelId(channel_name)
        return self.client.api_call('conversations.members', params={'channel':channel_id})['members']

    def getMyId(self) :
        return self.client.api_call('auth.test')['user_id']

    def getAllUsersForChannel(self, channel_id, exclude_bot=True) :
        channel_members = self.client.api_call('conversations.members', params={'channel':channel_id})['members']
        return [ member['id'] for member in channel_members if not (bool(member['is_bot']) and exclude_bot) ]

    def post(self, channel_id, message, **kwargs):
        params={
            'channel'   :   channel_id,
            'text'      :   message,
        }
        ts_file = kwargs['ts_file']
        os.chdir(kwargs['history_dir'])
        if os.path.isfile(ts_file):
            with open(ts_file, 'r') as f:
                ts = f.readline().rstrip()
                if not kwargs['solopost']:
                    params['thread_ts'] = ts
                    if not kwargs['mute']:
                        params['reply_broadcast'] = 'True'
        else:
            ts = None
        response = self.client.api_call(
            'chat.postMessage',
            params=params
        )
        posted_data = response.data
        if ts is None:
            ts = posted_data['ts']
            with open(ts_file, 'w') as f:
                print(ts, file=f)
        return response

def hashf(key):
    return hashlib.sha256(key.encode()).hexdigest()

def hash_members(members, dictionary_file=None):
    transpose = dict()
    if dictionary_file:
        with open(dictionary_file) as f:
            for line in f.readlines():
                if line.strip()[0] != '#':
                    a, b = line.split()[:2]
                    transpose[a] = b
    return sorted([ (hashf(transpose[m] if m in transpose else m),m) for m in members ])

start_userid = ''
start_hash = hashf(start_userid)

def next_writers(members, n, lastwriter, dictionary_file=None):
    N = len(members)
    hashed_members = hash_members(members, dictionary_file)
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

def get_last_writer(week_id, lookback_weeks, history_file_path_format):
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
        last_writer = start_userid

    return last_writer, lastweek_id


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--system', help='slack or mattermost.',
                        default='mattermost')
    parser.add_argument('--local', help='do not post to remote workspace.',
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
    parser.add_argument('--showcycle', help='show cyclenumber when entered a new cycle.',
                        action='store_true')
    parser.add_argument('-o', '--outchannel', default=None,
                        help='channel to post.')
    parser.add_argument('-t', '--team', default=None,
                        help='team to search channel.')
    parser.add_argument('-c', '--channel', default=None,
                        help='channel to read & post.')
    parser.add_argument('--token', default=None,
                        help='bot token.')
    parser.add_argument('--tokenfile', default=os.path.join(base_dir, token_file),
                        help='bot token filename.')
    parser.add_argument('--configfile', default=os.path.join(base_dir, config_file),
                        help='configuration filename to read.')
    parser.add_argument('--date', default=None,
                        help='specify arbitrary date "yyyy-mm-dd" for test.')
    parser.add_argument('--exclude', default=None,
                        help='specify an additional file or list/tuple of files, containing IDs to be excluded.')
    parser.add_argument('--mingrace', type=int, default=min_grace,
                        help='set minimum interval to the starting Monday.')
    parser.add_argument('--appdir', default=appdir,
                        help='Set application directory, as a relative path from $HOME. (default: {})'.format(appdir))
    parser.add_argument('--id-dictionary', default=None,
                        help='Set dictironary file to transpose IDs, to keep the order.')
    args = parser.parse_args()

    if args.local:
        post_to_remote = False
    min_grace = args.mingrace

    # memberlist_file_path = base_dir + memberlist_file
    token_file_path = args.tokenfile
    config_file_path = args.configfile
    history_file_path_format = os.path.join(history_dir, history_file_format)
    cyclenumber_file_path = os.path.join(history_dir, cyclenumber_file)
    excluded_members_files = [excluded_members_file]
    if args.exclude:
        args.exclude = args.exclude.strip()
        if args.exclude[0] in {'(', '['}:
            args.exclude = args.exclude.lstrip('[').lstrip('(').rstrip(']').rstrip(')')
            excluded_members_files += list(map(lambda s: s.strip(), args.exclude.split(',')))
        elif args.exclude:
            excluded_members_files.append(args.exclude)
    excluded_members_file_paths = list(map(lambda x: os.path.join(base_dir, x), excluded_members_files))

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
    with open(cyclenumber_file_path) as f:
        cyclenumber = int(f.readline())

    # read the previous record
    last_writer, lastweek_id = get_last_writer(week_id, lookback_weeks, history_file_path_format)

    if args.token:
        token = args.token
    else:
        with open(token_file_path, 'r') as f:
            token = f.readline().rstrip()
    if args.system.lower() == 'mattermost':    
        if os.path.exists(config_file_path):
            with open(config_file_path, 'r') as f:
                config = yaml.safe_load(f)
        else:
            config = defaultdict(lambda: None)
        if args.team:
            team_name = args.team
        elif 'team' in config:
            team_name = config['team']
        if args.channel:
            channel_name = args.channel
        elif 'channel' in config:
            channel_name = config['channel']
        config.pop('team', None)
        config.pop('channel', None)
        config.pop('token', None)
        # print(config)
        manager = MattermostManager(token, **config)
    else:
        team_name = url = None
        if args.channel:
            channel_name = args.channel
        manager = SlackManager(token)

    channel_id = manager.getChannelId(channel_name, team_name)
    my_id = manager.getMyId()

    writers_dict = dict()
    if args.reminder:
        while week_id >= thisweek_id:
            hf = history_file_path_format.format(week_id)
            if os.path.exists(hf):
                last_writer, _ = get_last_writer(week_id, lookback_weeks, history_file_path_format)
                with open(hf, 'r') as f:
                    cur_hash = hashf(last_writer)
                    delta_cycle = 0
                    lines = f.readlines()
                    for line in lines:
                        date, person = line.rstrip().split()[:2]
                        prev_hash = cur_hash
                        cur_hash = hashf(person)
                        if prev_hash <= start_hash < cur_hash or start_hash < cur_hash < prev_hash:
                            delta_cycle += 1
                        date = int(date)
                        writers_dict[date-date_id] = person
                cyclenumber -= delta_cycle
                break
            else:
                week_id -= 1
                date_id -= 7
                startday -= datetime.timedelta(7)
        else:
            exit()
    else:
        for excluded_members_file_path in excluded_members_file_paths:
            if os.path.exists(excluded_members_file_path):
                with open(excluded_members_file_path, 'r') as f:
                    lines = f.readlines()
                    for line in lines:
                        excluded_members.add(line.rstrip().split('\t')[1])
        channel_members = manager.getAllUsersForChannel(channel_id)
        members = set(channel_members) - excluded_members
        # members.discard(my_id)
        if args.list:
            for d, writer in enumerate(next_writers(members, len(members), last_writer, args.id_dictionary)):
                writers_dict[d] = writer
        else:
            writers = next_writers(members, len(relaydays), last_writer, args.id_dictionary)
            i = 0
            for d in relaydays:
                date = startday + datetime.timedelta(d)
                if not to_be_skipped(date.year, date.month, date.day):
                    writers_dict[d] = writers[i]
                    i += 1

    if args.list: week_id = max(week_id, lastweek_id + 1)
    week_str = weeks_str[week_id - thisweek_id]
    post_lines = [post_header_format.format(week_str)]

    cur_hash = hashf(last_writer)
    if writers_dict:
        for d, writer in sorted(writers_dict.items()):
            # write to history file
            if not (args.list or args.reminder):
                with open(history_file_path, 'a') as f:
                    print(date_id + d, writer, sep='\t', file=f)
            prev_hash = cur_hash
            cur_hash = hashf(writer)
            if prev_hash <= start_hash < cur_hash or start_hash < cur_hash < prev_hash:
                cyclenumber += 1
                if args.showcycle:
                    post_lines.append(newcycle_line_format.format(cyclenumber))
                if not (args.list or args.reminder):
                    with open(cyclenumber_file_path, 'w') as f:
                        print(cyclenumber, file=f)
            date = startday + datetime.timedelta(d)
            post_lines.append(post_line_format.format(writer, date.month, date.day, weekdays[d%7]))
        if len(post_lines) > 1:
            post_lines.append(post_footer)
        else:
            post_lines.append(post_nobody.format(week_str))
    else:
        post_lines.append(post_nobody.format(week_str))
    message = '\n'.join(post_lines)

    if post_to_remote:
        if args.outchannel:
            channel_id = manager.getChannelId(args.outchannel, team_name)
        manager.post(
            channel_id,
            message,
            # for slack below:
            history_dir=history_dir,
            ts_file=ts_file,
            solopost=False,
        )
    else:
        print('App ID:', my_id, file=sys.stderr)
        print(message)
