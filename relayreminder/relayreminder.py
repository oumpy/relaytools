#!/usr/bin/env python3
#
# RelayReminder
#
# @2023 AtamaokaC
# Python Party of Osaka University Medical School, Japan
#
# Lisence: GNU General Publice Lisence v3
#

from mattermostdriver import Driver
from datetime import datetime, date, timedelta
import argparse
from typing import List, Dict, Optional, Union, Any
import warnings
from bisect import bisect_right
import re
import os
import sys
from distutils.util import strtobool
from dotenv import load_dotenv
import subprocess
from flask import Flask, request, jsonify, g
from dateutil import parser
import requests
import shlex

BASE_TIME = datetime(1,1,3)
BASE_DATE = BASE_TIME.date() # Monday

class either:
    def __init__(self, *values):
        self.values = values

    def __eq__(self, other):
        return other in self.values

    def __contains__(self, item):
        return any(val in item for val in self.values)

    def __iter__(self):
        return iter(self.values)

Anything = object()
class MattermostChannel:
    def __init__(self,
        driver_params: Dict,
        team_name: str = "main",
        channel_name: str = "",
        channel_id: Optional[str] = None,
        after_weeksago: Optional[int] = None,
        stdout_mode: bool = False,
        week_shift_hours: int = 0,
    ):
        self.mm_driver = Driver(driver_params)
        self.mm_driver.login()
        self.headers = {
            "Authorization": "Bearer {}".format(driver_params["token"]),
            "Content-Type": "application/json",
        }
        self.base_url = driver_params.get("scheme","https") + "://" + driver_params["url"] + ":" + str(driver_params.get("port", 433))

        if after_weeksago is None:
            self.after_time = BASE_TIME
        else:
            self.after_time = self.get_start_of_week_n_weeks_ago(after_weeksago)

        if channel_id:
            self.channel_id = channel_id
        else:
            self.team_name = team_name
            self.channel_name = channel_name
            self.channel_id = self._get_channel_id()
        self.team_id = self.mm_driver.channels.get_channel(self.channel_id)["team_id"]
        self.user_ids = self._fetch_user_ids()
        self.users = self._fetch_users()
        self.id2name, self.name2id = self._fetch_usernames_and_ids()
        self.id2dispname = self._fetch_id2dispname()
        self.id2user = self._fetch_id2user()

        self.all_posts = {'order': [], 'posts': {}}
        self._fetch_posts()
        self.stop_data = self._fetch_stop_data()
        self.stdout_mode = stdout_mode
        self.week_shift_hours = week_shift_hours

    def get_week_number(self, target_datetime: Union[datetime, date]) -> int:
        if isinstance(target_datetime, date):
            target_datetime = datetime(target_datetime.year, target_datetime.month, target_datetime.day)

        delta_days = (target_datetime - BASE_TIME - timedelta(hours=self.week_shift_hours)).days
        week_number = delta_days // 7

        return week_number

    def _get_channel_id(self) -> str:
        channel = self.mm_driver.channels.get_channel_by_name_and_team_name(self.team_name, self.channel_name)
        return channel['id']

    def _fetch_users(self) -> Dict:
        users = [ self.mm_driver.users.get_user(user_id) for user_id in self.user_ids]
        return users

    def _fetch_user_ids(self) -> List[str]:
        return [user["user_id"] for user in self.mm_driver.channels.get_channel_members(self.channel_id)]

    def _fetch_usernames_and_ids(self) -> Dict[str, str]:
        """
        Fetch usernames based on user_ids.
        
        Returns:
            A dictionary where keys are user_ids and values are corresponding usernames.
        """
        id2name = {}
        name2id = {}
        for user in self.users:
            user_id = user["id"]
            username = user['username']
            id2name[user_id] = username
            name2id[username] = user_id
        return id2name, name2id

    def _fetch_id2user(self) -> Dict[str, Dict]:
        """        
        Returns:
            A dictionary where keys are user_ids and values are corresponding user data.
        """
        id2user = {}
        for user in self.users:
            user_id = user["id"]
            id2user[user_id] = user
        return id2user

    def get_username_by_id(self, user_id: str) -> Optional[str]:
        """
        Get the username for a given user_id using the self.usernames dictionary.
        
        Returns:
            Username corresponding to the user_id or None if not found.
        """
        return self.id2name.get(user_id, None)

    def get_user_by_id(self, user_id: str) -> Optional[str]:
        """
        Get the user-data for a given user_id using the self.usernames dictionary.
        
        Returns:
            Username corresponding to the user_id or None if not found.
        """
        return self.id2user.get(user_id, None)

    def get_id_by_username(self, username: str) -> Optional[str]:
        """
        Get the username for a given user_id using the self.usernames dictionary.
        
        Returns:
            Username corresponding to the user_id or None if not found.
        """
        return self.name2id.get(username, None)

    def _fetch_id2dispname(self) -> Dict[str, str]:
        """
        Fetch display-names based on user_ids.
        
        Returns:
            A dictionary where keys are user_ids and values are corresponding display-names.
        """
        id2dispname = {}
        for user in self.users:
            user_id = user["id"]
            if user.get("nickname", "").strip():
                id2dispname[user_id] = user["nickname"]
            else:
                name_parts = [user.get("first_name", ""), user.get("last_name", "")]
                full_name = " ".join(part for part in name_parts if part)
                id2dispname[user_id] = full_name if full_name.strip() else "Unknown"

        return id2dispname

    def get_dispname_by_id(self, user_id: str) -> Optional[str]:
        """
        Get the display-name for a given user_id using the self.usernames dictionary.
        
        Returns:
            Username corresponding to the user_id or None if not found.
        """
        return self.id2dispname.get(user_id, None)

    def _fetch_posts(self, page_size=100) -> Dict:
        """
        Fetch all posts in the channel since 'after_time' using pagination.

        Args:
        - page_size (int): Number of posts to fetch in a single request. Default is 100.

        Returns:
        - Dict: Aggregated posts.
        """
        aggregated_posts = {'posts': {}, 'order': []}
        page = 0

        while True:
            posts = self.mm_driver.posts.get_posts_for_channel(
                self.channel_id,
                params={
                    'since': int(self.after_time.timestamp() * 1000),
                    'per_page': page_size,
                    'page': page
                }
            )

            if not posts['posts']:
                break  # No more posts to fetch

            aggregated_posts['posts'].update(posts['posts'])
            aggregated_posts['order'].extend(posts['order'])

            page += 1  # Move to the next page

        if self.after_time <= BASE_TIME and posts['posts']:
            self.after_time = datetime.fromtimestamp(
                aggregated_posts['posts'][aggregated_posts['order'][0]] / 1000
            )

        self.all_posts = aggregated_posts  # Update the all_posts property
        return aggregated_posts

    def get_last_post_datetimes(self, 
        user_ids: Optional[List[str]] = None,
        priority_filter: Optional[str] = None,
        is_thread_head: Optional[bool] = None,
        ignore_deleted_posts: Optional[bool] = True,
        app_name: Optional[str] = None,
        regard_join_as_post: bool = False,
        use_past_record: bool = False,
        use_admin_stop: bool = False,
    ) -> Dict[str, datetime]:

        if user_ids is None:
            user_ids = self.user_ids

        last_post_dates = dict.fromkeys(user_ids, self.after_time)

        if priority_filter:
            priority_filter = priority_filter.lower()

        for post in self.all_posts['posts'].values():
            user_id = post['user_id']
            create_at = datetime.fromtimestamp(post['create_at'] / 1000)
            root_id = post.get('root_id', '')
            priority = post.get('metadata', {}).get('priority', {}).get('priority', 'standard').lower() or 'standard'

            # Skip deleted post (default)
            if ignore_deleted_posts and post['delete_at'] != 0:
                continue
            # Skip joining channel
            # if post.get("type", "") == "system_join_leave":
            #     continue

            if user_id in user_ids:
                if (priority_filter is None or priority == priority_filter) and \
                   (is_thread_head is None or bool(root_id) != is_thread_head):
                    if user_id not in last_post_dates or create_at > last_post_dates[user_id]:
                        last_post_dates[user_id] = create_at

        # Check channel-join-date.
        if (priority_filter is None or priority_filter == 'standard') and \
           (is_thread_head is None or is_thread_head):
            for user_id, post_date in last_post_dates.items():
                if regard_join_as_post and post_date <= self.after_time:
                    join_date = self.get_join_datetime(user_id)
                    if join_date > post_date:
                        post_date = join_date
                if use_past_record and post_date <= self.after_time:
                    record_date = self.get_last_post_datetime_from_record(user_id, app_name)
                    if record_date > post_date:
                        post_date = record_date
                if use_admin_stop:
                    stop_date = self.get_stop_until(user_id)
                    if stop_date > post_date:
                        post_date = stop_date
                last_post_dates[user_id] = post_date

        return last_post_dates

    def get_join_datetime(self, user_id: str) -> datetime:
        """Get the date when a user joined the channel using system messages."""
        criteria = [
            {
                "type": "system_join_channel",
                "user_id": user_id,
            },
            {
                "type": "system_add_to_channel",
                "props": {"addedUserId": user_id,},
            },
        ]
        filtered_posts = [
            self.filter_posts_by_criteria(criterion) for criterion in criteria
        ]

        join_time_milliseconds = max(
            (posts + [{"create_at": 0}])[0]["create_at"] for posts in filtered_posts
        )
        if join_time_milliseconds > 0:
            join_time = datetime.fromtimestamp(join_time_milliseconds / 1000)
        else:
            join_time = self.after_time

        return join_time

    def get_last_post_datetime_from_record(self, user_id: str, app_name: Optional[str] = None) -> datetime:
        criteria = {
            "props": {
                "type": "record",
                "users": [user_id],
            },
        }
        if app_name:
            criteria["props"]["bot_app"] = app_name

        posts = self.filter_posts_by_criteria(criteria)
        if posts:
            last_post_week = posts[-1]["props"]["last_post_week"]
            return self.get_start_of_week(last_post_week)
        else:
            return self.after_time

    def _fetch_stop_data(self, app_name: Optional[str] = None) -> datetime:
        criteria = {
            "props": {
                "type": "relaystop",
                "data": Anything
            },
        }
        if app_name:
            criteria["props"]["bot_app"] = app_name

        posts = self.filter_posts_by_criteria(criteria)
        if posts:
            last_stop_data = posts[-1]["props"]["data"]
            return last_stop_data
        else:
            return dict()

    def get_stop_until(self, user_id: str) -> datetime:
        if user_id in self.stop_data:
            until_date = parser.parse(self.stop_data[user_id])
            return datetime(until_date.year, until_date.month, until_date.day) + timedelta(hours=self.week_shift_hours)
        return self.after_time

    def send_post(self, message: str, props: Optional[Dict] = None, root_id: Optional[str] = None) -> Dict:
        payload = {
            'channel_id': self.channel_id,
            'message': message,
        }

        if props:
            payload['props'] = props

        if root_id:
            payload['root_id'] = root_id
            if not root_id in self.all_posts['posts']:
                warnings.warn(f"Given root_id '{root_id}' does not exist in fetched posts. Posting directly to the channel.")

        if self.stdout_mode:
            print(payload)
            return True
        else:
            try:
                response = self.mm_driver.posts.create_post(payload)
                if 'id' in response:
                    return True
                else:
                    return False
            except Exception as e:
                return False

    def filter_posts_by_criteria(self, criteria: Dict[str, Any], posts: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        if posts is None:
            posts = self.all_posts['posts']

        def match_criteria(data: Dict[str, Any], criteria: Dict[str, Any]) -> bool:
            for key, value in criteria.items():
                try:
                    if key not in data:
                        return False
                except:
                    if key not in list(data.keys()):
                        return False

                if value is Anything:
                    continue
                elif isinstance(value, dict):
                    if not match_criteria(data[key], value):
                        return False
                elif isinstance(value, (set, list)) and isinstance(data[key], list):
                    try:
                        value_set = set(value)
                        if not value_set.issubset(data[key]):
                            return False
                    except:
                        data_key = data[key]
                        if not all(val in data_key for val in value):
                            return False
                elif data[key] != value:
                    return False
            return True

        # Filter the posts based on criteria
        filtered_posts = [post for post in posts.values() if match_criteria(post, criteria)]

        # Sort the posts in ascending order based on the 'create_at' timestamp
        sorted_filtered_posts = sorted(filtered_posts, key=lambda post: post['create_at'])

        return sorted_filtered_posts

    def unfollow_thread_for_users(self, post_id: str, user_ids: Union[list, set, str]):
        """
        Unfollow a thread for a specific user.

        Args:
            post_id (str): The ID of a post within the thread.
            user_ids (list/set/str): The IDs of the users whose follow status should be changed.
        """
        # If in stdout_mode, print the action and return
        if self.stdout_mode:
            print(f"Unfollow action called for post ID '{post_id}' for user IDs '{user_id}'")
        else:
            # Actually perform the unfollow action
            # Get the thread_id from the post_id
            thread_url = os.path.join(self.base_url, "threads", post_id)
            thread_response = requests.get(thread_url, headers=self.headers)
            thread_data = thread_response.json()
            thread_id = thread_data['id']

            # Stop the user following the thread
            if isinstance(user_ids, str):
                user_ids = user_ids.split()

            for user_id in user_ids:
                stop_url = os.path.join(self.base_url, "users", user_id, "teams", self.team_id, "threads", thread_id, "following")
                requests.delete(stop_url, headers=self.headers)
        return

    def get_start_of_week_n_weeks_ago(self, n: int) -> datetime:
        now = datetime.now()
        shifted_now = now - timedelta(hours=self.week_shift_hours)  # Get now
        shifted_today = now.date()
        start_of_this_week = shifted_today - timedelta(days=shifted_now.date().weekday())  # Get Monday of this week
        target_date = start_of_this_week - timedelta(weeks=n)  # Get Monday n-weeks ago
        return datetime(target_date.year, target_date.month, target_date.day) + timedelta(hours=self.week_shift_hours)

    def get_start_of_week(self, n: int) -> datetime:
        target_time = BASE_TIME + timedelta(weeks=n, hours=self.week_shift_hours)  # Get Monday of n-th week
        return target_time


def load_tsv_data(file_path: str) -> Dict[int, str]:
    data = {}
    with open(file_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            # Skip comments and empty lines
            if not line or line.startswith('#'):
                continue

            parts = line.split('\t')
            if len(parts) != 2:
                continue

            # Try converting the first column to an integer (week number)
            try:
                week_number = int(parts[0])
            except ValueError:
                continue

            # Skip if the message is empty
            if not parts[1]:
                continue

            data[week_number] = re.sub(r'\\n', '\n', parts[1])
    return data

def load_envs():
    app_dir = os.path.join(os.path.expanduser("~"), ".relayreminder")
    env_path = os.path.join(app_dir, "env")
    load_dotenv(dotenv_path=env_path)

def parse_args() -> argparse.Namespace:
    """
    Parse command line arguments and return the Namespace object.
    """
    parser = argparse.ArgumentParser(description="RelayReminder: the 3rd-generation bot-system for the relay-posts.")

    # Use os.environ.get() to obtain default values from environment variables for each option
    parser.add_argument("--initialize", action="store_true", help="Post an administration message as preparation.")
    parser.add_argument("--bot-token", type=str, default=os.environ.get("RELAYREMINDER_BOT_TOKEN"), help="Access Token")
    parser.add_argument("--app-name", default=os.environ.get("RELAYREMINDER_APP_NAME", "RelayReminder"), help='Application name')
    parser.add_argument("--mm-url", default=os.environ.get("RELAYREMINDER_MM_URL", "localhost"), help='Mattermost server URL')
    parser.add_argument("--scheme", type=str, default=os.environ.get("RELAYREMINDER_SCHEME", "https"), help="Mattermost URL Scheme")
    parser.add_argument("--port", type=int, default=int(os.environ.get("RELAYREMINDER_PORT", 443)), help="Mattermost Port")
    parser.add_argument("--team", type=str, default=os.environ.get("RELAYREMINDER_TEAM", "main"), help="Team name")
    parser.add_argument("--channel", type=str, default=os.environ.get("RELAYREMINDER_CHANNEL", "RelayPosts"), help="Channel name")
    parser.add_argument("--message-file", type=str, default=os.environ.get("RELAYREMINDER_MESSAGE_FILE", os.path.join(os.path.expanduser("~"), ".relayreminder", "messages.tsv")), help="Path to the TSV file containing week intervals & reminder messages.")
    parser.add_argument("--mention-format", type=str, default=os.environ.get("RELAYREMINDER_MENTION_FORMAT", "{}"), help="Path to the TSV file containing week intervals & reminder messages.")
    parser.add_argument("--stdout-mode", action="store_true", 
                        default=bool(strtobool(os.environ.get("RELAYREMINDER_STDOUT_MODE", "false"))),
                        help="Post to stdout, instead of the Mattermost channel.")
    parser.add_argument("--all-history", action="store_true",
                        default=bool(strtobool(os.environ.get("RELAYREMINDER_ALL_HISTORY", "false"))),
                        help="Search all history of the channel.")
    parser.add_argument("--week-shift-hours", type=int, default=int(os.environ.get("RELAYREMINDER_WEEK_SHIFT_HOURS", 0)), help="Shift the beginning of weeks by n-hours.")

    # slashcommand mode
    parser.add_argument("--slashcommand-mode", action="store_true",
                        default=bool(strtobool(os.environ.get("RELAYREMINDER_SLASHCOMMAND_MODE", "false"))),
                        help="Work in slashcommand-mode.")
    parser.add_argument("--slashcommand-host", default=os.environ.get("RELAYREMINDER_SLASHCOMMAND_HOST", "0.0.0.0"), help="Slash-command listening host (default: %(default)s)")
    parser.add_argument("--slashcommand-port", type=int, default=os.environ.get("RELAYREMINDER_SLASHCOMMAND_PORT", 4500), help='Slash-command listening port')
    parser.add_argument("--gunicorn-path", default=os.environ.get("RELAYREMINDER_GUNICORN_PATH", ""), help="Path to Gunicorn executable (if not provided, Flask built-in server will be used)")
    parser.add_argument("--workers", type=int, default=os.environ.get("RELAYREMINDER_WORKERS", 1), help="Number of Gunicorn worker processes (only applicable if using Gunicorn)")
    parser.add_argument("--timeout", type=int, default=os.environ.get("RELAYREMINDER_TIMEOUT", 30), help="Gunicorn timeout value in seconds (only applicable if using Gunicorn)")
    parser.add_argument("--blacklist-message-min", type=str, default=os.environ.get("RELAYREMINDER_BLACKLIST_MESSAGE_MIN", "No relay-posts >= {} weeks:"), help="Default leading message for /blacklist min")
    parser.add_argument("--blacklist-message-minmax", type=str, default=os.environ.get("RELAYREMINDER_BLACKLIST_MESSAGE_MINMAX", "No relay-posts for {}-{} weeks:"), help="Default leading message for /blacklist min max")
    parser.add_argument("--blacklist-minweek-default", type=int, default=os.environ.get("RELAYREMINDER_BLACKLIST_MINWEEK_DEFAULT", 13), help="Default minweek for /blacklist")
    parser.add_argument("--datetime-format", type=str, default=os.environ.get("RELAYREMINDER_DATETIME_FORMAT", "%Y-%m-%d %H:%M:%S"), help="Display format for datetime.")
    parser.add_argument("--whenmylast-message-format", type=str, default=os.environ.get("RELAYREMINDER_WHENMYLAST_MESSAGE_FORMAT", "{}\\n{}"), help="message format for /whenmylast")
    parser.add_argument("--whenmylast-datetime-never", type=str, default=os.environ.get("RELAYREMINDER_WHENMYLAST_DATETIME_NEVER", "Never"), help="Sign for no message in /whenmylast")

    args = parser.parse_args()

    # Set environment variables based on the provided or defaulted args values
    os.environ["RELAYREMINDER_BOT_TOKEN"] = args.bot_token
    os.environ["RELAYREMINDER_APP_NAME"] = args.app_name
    os.environ["RELAYREMINDER_MM_URL"] = args.mm_url
    os.environ["RELAYREMINDER_SCHEME"] = args.scheme
    os.environ["RELAYREMINDER_PORT"] = str(args.port)
    os.environ["RELAYREMINDER_TEAM"] = args.team
    os.environ["RELAYREMINDER_CHANNEL"] = args.channel
    os.environ["RELAYREMINDER_MESSAGE_FILE"] = args.message_file
    os.environ["RELAYREMINDER_MENTION_FORMAT"] = args.mention_format
    os.environ["RELAYREMINDER_STDOUT_MODE"] = str(args.stdout_mode)
    os.environ["RELAYREMINDER_ALL_HISTORY"] = str(args.all_history)
    os.environ["RELAYREMINDER_WEEK_SHIFT_HOURS"] = str(args.week_shift_hours)

    # slashcommand mode
    os.environ["RELAYREMINDER_SLASHCOMMAND_MODE"] = str(args.slashcommand_mode)
    os.environ["RELAYREMINDER_SLASHCOMMAND_HOST"] = args.slashcommand_host
    os.environ["RELAYREMINDER_SLASHCOMMAND_PORT"] = str(args.slashcommand_port)
    os.environ["RELAYREMINDER_GUNICORN_PATH"] = args.gunicorn_path
    os.environ["RELAYREMINDER_WORKERS"] = str(args.workers)
    os.environ["RELAYREMINDER_TIMEOUT"] = str(args.timeout)
    os.environ["RELAYREMINDER_BLACKLIST_MESSAGE_MIN"] = args.blacklist_message_min
    os.environ["RELAYREMINDER_BLACKLIST_MESSAGE_MINMAX"] = args.blacklist_message_minmax
    os.environ["RELAYREMINDER_BLACKLIST_MINWEEK_DEFAULT"] = str(args.blacklist_minweek_default)
    os.environ["RELAYREMINDER_DATETIME_FORMAT"] = str(args.datetime_format)
    os.environ["RELAYREMINDER_WHENMYLAST_MESSAGE_FORMAT"] = str(args.whenmylast_message_format)
    os.environ["RELAYREMINDER_WHENMYLAST_DATETIME_NEVER"] = str(args.whenmylast_datetime_never)

    return args

def main(args: argparse.Namespace):
    driver_params = {
        "url": args.mm_url,
        "scheme": args.scheme,
        "port": args.port,
        "token": args.bot_token
    }
    # Load message data
    message_data = load_tsv_data(args.message_file)
    message_passed_weeks_list = sorted(message_data.keys())
    max_week_limit = message_passed_weeks_list[-1]

    if args.all_history:
        after_weeksago = None
    else:
        after_weeksago = max_week_limit

    mm_channel = MattermostChannel(
        driver_params,
        args.team,
        args.channel,
        after_weeksago = after_weeksago,
        stdout_mode = args.stdout_mode,
        week_shift_hours = args.week_shift_hours,
    )

    if args.initialize:
        mm_channel.send_post(
            "Administration thread.",
            props={
                "bot_app": args.app_name,
                "type": "relaystop",
                "data": dict(),
            },
        )
        return

    # Get last post dates for all user_ids
    last_post_dates = mm_channel.get_last_post_datetimes(
        priority_filter="standard",
        is_thread_head=True,
        app_name=args.app_name,
        regard_join_as_post = True,
        use_past_record = True,
        use_admin_stop = True,
    )

    # Convert dates to week numbers
    last_post_weeks = {user_id: mm_channel.get_week_number(date) for user_id, date in last_post_dates.items()}
    current_week_number = mm_channel.get_week_number(datetime.now())

    # Find users and message based on last post week number
    users_to_notify = {}
    for user_id, week in last_post_weeks.items():
        if week not in users_to_notify:
            users_to_notify[week] = []
        users_to_notify[week].append(user_id)

    # Post messages
    for week, user_ids in sorted(users_to_notify.items(), key=lambda x: x[0]):
        passed_weeks = current_week_number - week
        criteria = {
            "props": {
                "bot_app": args.app_name,
                "type": "record",
                "last_post_week": week,
            }
        }
        matching_posts = mm_channel.filter_posts_by_criteria(criteria)
        # Find the matching post with maximum weeks passed
        last_passed_weeks = max([post['props']['passed_weeks'] for post in matching_posts], default=-1)

        passed_weeks_k = bisect_right(message_passed_weeks_list, passed_weeks)
        if passed_weeks_k == 0:
            continue
        passed_weeks_to_post = message_passed_weeks_list[passed_weeks_k-1]

        if passed_weeks_to_post > last_passed_weeks or passed_weeks > max(max_week_limit, last_passed_weeks):
            message_start = message_data[passed_weeks_to_post].format(passed_weeks)
            sorted_user_ids = sorted(user_ids, key=lambda uid: (last_post_dates[uid], uid))
            mentions = '\n'.join([args.mention_format.format(f'@{mm_channel.get_username_by_id(user_id)}') for user_id in sorted_user_ids])

            message = f"{message_start}\n{mentions}"

            if matching_posts:
                post = matching_posts[-1]
                post_id = post['id']
                root_id = post.get('root_id', post_id)
                mm_channel.unfollow_thread_for_users(post_id, set(post['props']['users']) - set(sorted_user_ids))
            else:
                root_id = None

            mm_channel.send_post(
                message,
                props={
                    "bot_app": args.app_name,
                    "type": "record",
                    "last_post_week": week,
                    "passed_weeks": passed_weeks,
                    "users": sorted_user_ids,
                },
                root_id=root_id,
            )


relayadmin_help_message = """\
<Usage of /relayadmin>
/relayadmin help : Display this message only for you.
/relayadmin status: Display the stopped user status only for you.
/relayadmin stop *username* *date* : Stop relay-posts for the user until *date*.
/relayadmin cancelstop *username*: Cancel the stopping config for the user.
/relayadmin restart *username*: Restart relay-posts for the user, regarding they posted today.
"""

def create_slashcommand_app(args):
    app = Flask(__name__)

    @app.before_request
    def store_args():
        g.args = args

    @app.route('/blacklist', methods=['POST'])
    def blacklist():
        """Handle incoming /blacklist events from Mattermost."""
        # logging.debug(f"Slash-command received: {request.json}")

        args = g.args
        data = request.form
        token = data.get("token")

        # Verify the slash-command token
        if token != os.environ["MATTERMOST_BLACKLIST_TOKEN"]:
            return jsonify({"text": "Invalid token"}), 403

        try:
            parser = argparse.ArgumentParser()
            parser.add_argument("min-weeks", nargs="?", type=int, default=args.blacklist_minweek_default)
            parser.add_argument("max-weeks", nargs="?", type=int, default=-1)
            parser.add_argument("--important", action="store_true")
            parser.add_argument("--post", action="store_true")
            slash_args = parser.parse_args(shlex.split(data.get("text")))

            min_weeks = getattr(slash_args, "min-weeks")
            if getattr(slash_args, "max-weeks") < 0:
                max_weeks = float("inf")
            else:
                max_weeks = getattr(slash_args, "max-weeks")

            if getattr(slash_args, "important"):
                priority = "important"
            else:
                priority = "standard"

            if getattr(slash_args, "post"):
                response_type = "in_channel"
            else:
                response_type = "ephemeral"

        except:
            return jsonify(
                {
                    "response_type": "ephemeral",
                    "text": "Error: Invalid command syntax",
                    "attachments": [
                        {
                            "title": "Usage",
                            "text": "/blacklist [min_weeks] [max_weeks] [--post]",
                        },
                    ],
                },
            )

        driver_params = {
            "url": args.mm_url,
            "scheme": args.scheme,
            "port": args.port,
            "token": args.bot_token
        }
        mm_channel = MattermostChannel(
            driver_params,
            args.team,
            args.channel,
            stdout_mode = args.stdout_mode,
        )
        last_post_datetimes = mm_channel.get_last_post_datetimes(
            priority_filter="standard",
            is_thread_head=True,
            app_name=args.app_name,
            regard_join_as_post = True,
            use_past_record = True,
            use_admin_stop = True,
        )

        # Convert dates to week numbers
        current_week_number = mm_channel.get_week_number(datetime.now())
        passed_weeks_list = sorted([(user_id, post_time) 
                                    for user_id, post_time in last_post_datetimes.items()
                                    if min_weeks <= current_week_number - mm_channel.get_week_number(post_time) <= max_weeks],
                                    key=lambda x: x[1], reverse=True)

        if max_weeks == float("inf"):
            message = args.blacklist_message_min.format(min_weeks)
        else:
            message = args.blacklist_message_minmax.format(min_weeks, max_weeks)
        message = (
            message + "\n"
            + "\n".join([f"{mm_channel.get_dispname_by_id(user_id)} [{mm_channel.get_username_by_id(user_id)}] ({current_week_number - mm_channel.get_week_number(post_time)})"  for user_id, post_time in passed_weeks_list])
        )

        return jsonify({
            "response_type": response_type,
            "text": message,
            "priority": priority,
            }
        )

    @app.route('/whenmylast', methods=['POST'])
    def whenmylast():
        """Handle incoming /whenmylast events from Mattermost."""
        # logging.debug(f"Slash-command received: {request.json}")

        args = g.args
        data = request.form
        token = data.get("token")

        # Verify the slash-command token
        if token != os.environ["MATTERMOST_WHENMYLAST_TOKEN"]:
            return jsonify({"text": "Invalid token"}), 403

        driver_params = {
            "url": args.mm_url,
            "scheme": args.scheme,
            "port": args.port,
            "token": args.bot_token
        }
        mm_channel = MattermostChannel(
            driver_params,
            channel_id = data.get("channel_id"),
            stdout_mode = args.stdout_mode,
        )
        user_id = data.get("user_id")
        last_post_datetime_all = mm_channel.get_last_post_datetimes(user_ids=[user_id], app_name=args.app_name)[user_id]
        last_post_datetime_standard_channel = mm_channel.get_last_post_datetimes(user_ids=[user_id], priority_filter="standard", is_thread_head=True, app_name=args.app_name)[user_id]

        if last_post_datetime_standard_channel == BASE_TIME:
            last_post_datetime_standard_channel_str = args.whenmylast_datetime_never
        else:
            last_post_datetime_standard_channel_str = last_post_datetime_standard_channel.strftime(args.datetime_format)

        if last_post_datetime_all == BASE_TIME:
            last_post_datetime_all_str = args.whenmylast_datetime_never
        else:
            last_post_datetime_all_str = last_post_datetime_all.strftime(args.datetime_format)

        message = args.whenmylast_message_format.replace("\\n", "\n").format(last_post_datetime_standard_channel_str, last_post_datetime_all_str)

        return jsonify(
            {
                "response_type": "ephemeral",
                "text": message,
            },
        )

    @app.route('/relayadmin', methods=['POST'])
    def relayadmin():
        """Handle incoming /relayadmin events from Mattermost."""
        # logging.debug(f"Slash-command received: {request.json}")

        args = g.args
        data = request.form
        token = data.get("token")
        slash_args = data.get("text").split()

        # Verify the slash-command token
        if token != os.environ["MATTERMOST_RELAYADMIN_TOKEN"]:
            return jsonify({"text": "Invalid token"}), 401

        driver_params = {
            "url": args.mm_url,
            "scheme": args.scheme,
            "port": args.port,
            "token": args.bot_token
        }
        mm_channel = MattermostChannel(
            driver_params,
            team_name = args.team,
            channel_name = args.channel,
            stdout_mode = args.stdout_mode,
        )
        exec_user_id = data.get("user_id")
        # channel_id = mm_channel.channel_id
        # exec_channel_id = data.get("channel_id")

        roles = mm_channel.mm_driver.users.get_user(exec_user_id)["roles"].split()
        if "system_admin" not in roles:
            return jsonify({"response_type": "ephemeral", "text": "You don't have correct permission to execute this command."}), 403

        if len(slash_args) == 0 or slash_args[0].lower() == "help":
            return jsonify({"response_type": "ephemeral", "text": relayadmin_help_message})
        sub_command = slash_args[0].lower()
        sub_args = slash_args[1:]
        
        try:
            stop_records = mm_channel.filter_posts_by_criteria(
                {
                    "props": {
                        "bot_app": args.app_name,
                        "type": "relaystop",
                        "data": Anything,
                    },
                },
            )
            last_record = stop_records[-1]
            stop_data = last_record["props"]["data"]

            if sub_command in {"stop", "cancelstop", "restart"}:
                username = sub_args[0]
                user_id = mm_channel.get_id_by_username(username)
                if user_id is None:
                    return jsonify({"response_type": "ephemeral", "text": f"{username} is not a member of relay-channel."}), 400
                if sub_command == "stop":
                    until_date = parser.parse(sub_args[1]).date()
                    stop_data[user_id] = until_date.strftime("%Y-%m-%d")
                    sub_args = sub_args[:2]
                elif sub_command == "cancelstop":
                    del stop_data[user_id]
                    sub_args = sub_args[:1]
                else: # "restart"
                    until_date = datetime.now().date()
                    stop_data[user_id] = until_date.strftime("%Y-%m-%d")
                    sub_args = sub_args[:1]
                
                root_id = last_record["root_id"]
                if root_id == "":
                    root_id = last_record["id"]
                post_result = mm_channel.send_post(
                    "/relayadmin {} {}".format(sub_command, " ".join(sub_args)),
                    props = {
                        "bot_app": args.app_name,
                        "type": "relaystop",
                        "data": stop_data,
                    },
                    root_id = root_id,
                )
                if post_result:
                    return jsonify(
                        {
                            "response_type": "ephemeral",
                            "text": "Your command has been successfully executed:\n{}".format(
                                "\n".join(
                                    [f"{mm_channel.get_dispname_by_id(user_id)} [{mm_channel.get_username_by_id(user_id)}]: until {until_date}" for user_id, until_date in sorted(stop_data.items(), key=lambda x: (x[1],x[0]))]
                                )
                            )
                        },
                    )
                else:
                    return jsonify(
                        {
                            "response_type": "ephemeral",
                            "text": "Something went wrong when posting your command:\n{}".format(
                                "\n".join(
                                    [f"{mm_channel.get_dispname_by_id(user_id)} [{mm_channel.get_username_by_id(user_id)}]: until {until_date}" for user_id, until_date in sorted(stop_data.items(), key=lambda x: (x[1],x[0]))]
                                )
                            )
                        }
                    )
            elif sub_command == "status":
                return jsonify(
                    {
                        "response_type": "ephemeral",
                        "text": "Relay-posts now stop for:\n{}".format(
                            "\n".join(
                                [f"{mm_channel.get_dispname_by_id(user_id)} [{mm_channel.get_username_by_id(user_id)}]: until {until_date}" for user_id, until_date in sorted(stop_data.items(), key=lambda x: (x[1],x[0]))]
                            )
                        )
                    },
                )
            else:
                return jsonify({"response_type": "ephemeral", "text": relayadmin_help_message})

        except:
            return jsonify({"response_type": "ephemeral", "text": relayadmin_help_message})

    return app


if __name__ == "__main__":
    load_envs()
    args = parse_args()
    if args.slashcommand_mode:
        if args.gunicorn_path:
            subprocess.run([args.gunicorn_path, "--workers", str(args.workers), "--timeout", str(args.timeout), "--bind", f"{args.slashcommand_host}:{args.slashcommand_port}", "relayreminder:app"])
        else:
            app = create_slashcommand_app(args)
            app.run(host=args.slashcommand_host, port=args.slashcommand_port)
    else:
        main(args)
elif __name__ == "relayreminder" and bool(strtobool(os.environ["RELAYREMINDER_SLASHCOMMAND_MODE"])):
    sys.argv = sys.argv[:1]
    args = parse_args()
    app = create_slashcommand_app(args)
