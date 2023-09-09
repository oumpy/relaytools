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

def get_week_number(target_date: Union[datetime, date]) -> int:
    if isinstance(target_date, datetime):
        target_date = target_date.date()

    delta_days = (target_date - BASE_DATE).days
    week_number = delta_days // 7

    return week_number

Anything = object()
class MattermostChannel:
    def __init__(self,
        driver_params: Dict,
        team_name: str,
        channel_name: str,
        after_time: Optional[datetime] = None,
        stdout_mode: bool = False,
    ):
        self.mm_driver = Driver(driver_params)
        self.mm_driver.login()
        self.team_name = team_name
        self.channel_name = channel_name
        self.channel_id = self._get_channel_id()
        self.members = self.fetch_members()
        self.usernames = self.fetch_usernames()
        self.dispnames = self.fetch_dispnames()
        self.all_posts = {'order': [], 'posts': {}}
        self.after_time = after_time
        if after_time is not None:
            self.fetch_posts()
        self.stdout_mode = stdout_mode

    def _get_channel_id(self) -> str:
        channel = self.mm_driver.channels.get_channel_by_name_and_team_name(self.team_name, self.channel_name)
        return channel['id']

    def fetch_members(self) -> List[str]:
        members = self.mm_driver.channels.get_channel_members(self.channel_id)
        return [member['user_id'] for member in members]

    def fetch_usernames(self) -> Dict[str, str]:
        """
        Fetch usernames based on member user_ids.
        
        Returns:
            A dictionary where keys are user_ids and values are corresponding usernames.
        """
        usernames = {}
        for user_id in self.members:
            user = self.mm_driver.users.get_user(user_id)
            usernames[user_id] = user['username']
        return usernames

    def get_username_by_id(self, user_id: str) -> Optional[str]:
        """
        Get the username for a given user_id using the self.usernames dictionary.
        
        Returns:
            Username corresponding to the user_id or None if not found.
        """
        return self.usernames.get(user_id, None)

    def fetch_dispnames(self) -> Dict[str, str]:
        """
        Fetch display-names based on member user_ids.
        
        Returns:
            A dictionary where keys are user_ids and values are corresponding display-names.
        """
        dispnames = {}
        for user_id in self.members:
            user = self.mm_driver.users.get_user(user_id)
            # nicknameが存在していて、空でない場合
            if user.get("nickname", "").strip():
                dispnames[user_id] = user["nickname"]
            else:
                # last_name と first_name の組み合わせを使用
                name_parts = [user.get("first_name", ""), user.get("last_name", "")]
                full_name = " ".join(part for part in name_parts if part)
                dispnames[user_id] = full_name if full_name.strip() else "Unknown"

        return dispnames

    def get_dispname_by_id(self, user_id: str) -> Optional[str]:
        """
        Get the display-name for a given user_id using the self.usernames dictionary.
        
        Returns:
            Username corresponding to the user_id or None if not found.
        """
        return self.dispnames.get(user_id, None)

    def fetch_posts(self, page_size=100) -> Dict:
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

    def fetch_last_post_datetimes(self, 
        user_ids: Optional[List[str]] = None,
        priority_filter: Optional[str] = None,
        is_thread_head: Optional[bool] = None,
        ignore_deleted_posts: Optional[bool] = True,
        app_name: Optional[str] = None,
    ) -> Dict[str, datetime]:

        if user_ids is None:
            user_ids = self.members

        last_post_dates = dict.fromkeys(user_ids, self.after_time)

        if priority_filter:
            priority_filter = priority_filter.lower()

        for post in self.all_posts['posts'].values():
            user_id = post['user_id']
            create_at = datetime.fromtimestamp(post['create_at'] / 1000)
            root_id = post.get('root_id', '')
            priority = post['metadata'].get('priority', {}).get('priority', 'standard').lower()

            # Skip deleted post (optional)
            if ignore_deleted_posts and post['delete_at'] != 0:
                continue

            if user_id in user_ids:
                if (priority_filter is None or priority == priority_filter) and \
                   (is_thread_head is None or bool(root_id) != is_thread_head):
                    if user_id not in last_post_dates or create_at > last_post_dates[user_id]:
                        last_post_dates[user_id] = create_at

        # Check channel-join-date.
        if (priority_filter is None or priority_filter == 'standard') and \
           (is_thread_head is None or is_thread_head):
            for user_id, post_date in last_post_dates.items():
                if post_date <= self.after_time:
                    post_date = self.fetch_join_datetime(user_id)
                if post_date <= self.after_time:
                    post_date = self.fetch_last_post_datetime_from_record(user_id, app_name)
                last_post_dates[user_id] = post_date

        return last_post_dates

    def fetch_join_datetime(self, user_id: str) -> datetime:
        """Fetch the date when a user joined the channel using system messages."""
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

    def fetch_last_post_datetime_from_record(self, user_id: str, app_name: Optional[str] = None) -> datetime:
        criteria = {
            "props": {
                "users": [user_id],
            },
        }
        if app_name:
            criteria["bot_app"] = app_name

        posts = self.filter_posts_by_criteria(criteria)
        if posts:
            last_post_week = posts[-1]["props"]["last_post_week"]
            return get_start_of_week(last_post_week)
        else:
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
        else:
            self.mm_driver.posts.create_post(payload)
        
        return payload

    def filter_posts_by_criteria(self, criteria: Dict[str, Any], posts: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        if posts is None:
            posts = self.all_posts['posts']

        def match_criteria(data: Dict[str, Any], criteria: Dict[str, Any]) -> bool:
            for key, value in criteria.items():
                if key not in data:
                    return False
                elif value is Anything:
                    continue
                elif isinstance(value, dict):
                    if not match_criteria(data[key], value):
                        return False
                elif isinstance(value, (set, list)) and isinstance(data[key], (set, list)):
                    if not set(value).issubset(data[key]):
                        return False
                elif data[key] != value:
                    return False
            return True

        # Filter the posts based on criteria
        filtered_posts = [post for post in posts.values() if match_criteria(post, criteria)]

        # Sort the posts in ascending order based on the 'create_at' timestamp
        sorted_filtered_posts = sorted(filtered_posts, key=lambda post: post['create_at'])

        return sorted_filtered_posts

    def unfollow_thread_for_user(self, post_id: str, user_id: str):
        """
        Unfollow a thread for a specific user.

        Args:
            post_id (str): The ID of a post within the thread.
            user_id (str): The ID of the user whose follow status should be changed.
        """
        # If in stdout_mode, print the action and return
        if self.stdout_mode:
            print(f"Unfollow action called for post ID '{post_id}' for user ID '{user_id}'")
            return None

        # Actually perform the unfollow action
        result = self.mm_driver.posts.unfollow_post_for_user(user_id, post_id)
        return result


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

def get_start_of_week_n_weeks_ago(n: int) -> datetime:
    today = datetime.now().date()  # Get today
    start_of_this_week = today - timedelta(days=today.weekday())  # Get Monday of this week
    target_date = start_of_this_week - timedelta(weeks=n)  # Get Monday n-weeks ago
    return datetime(target_date.year, target_date.month, target_date.day)

def get_start_of_week(n: int) -> datetime:
    target_time = BASE_TIME + timedelta(weeks=n)  # Get Monday of n-th week
    return target_time

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
        after_time = BASE_TIME
    else:
        after_time = get_start_of_week_n_weeks_ago(max_week_limit),

    mm_channel = MattermostChannel(
        driver_params,
        args.team,
        args.channel,
        after_time = after_time,
        stdout_mode = args.stdout_mode,
    )
    # Fetch all posts
    # mm_channel.fetch_posts()
    # Fetch last post dates for all members
    last_post_dates = mm_channel.fetch_last_post_datetimes(priority_filter="standard", is_thread_head=True, app_name=args.app_name)

    # Convert dates to week numbers
    last_post_weeks = {user_id: get_week_number(date) for user_id, date in last_post_dates.items()}
    current_week_number = get_week_number(datetime.now())
    
    # Find users and message based on last post week number
    users_to_notify = {}
    for user_id, week in last_post_weeks.items():
        if week not in users_to_notify:
            users_to_notify[week] = []
        users_to_notify[week].append(user_id)
    # print(*sorted([(current_week_number - week, [mm_channel.get_username_by_id(userid) for userid in userids]) for week, userids in users_to_notify.items()]),sep='\n')

    # Post messages
    for week, user_ids in sorted(users_to_notify.items(), key=lambda x: x[0]):
        passed_weeks = current_week_number - week
        criteria = {
            "props": {
                "bot_app": args.app_name,
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
                for user_id in set(post['props']['users']) - set(sorted_user_ids):
                    mm_channel.unfollow_thread_for_user(post_id, user_id)
            else:
                root_id = None

            mm_channel.send_post(
                message,
                props={
                    "bot_app": args.app_name,
                    "last_post_week": week,
                    "passed_weeks": passed_weeks,
                    "users": sorted_user_ids,
                },
                root_id=root_id,
            )

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

        slash_args = data.get("text").split()
        try:
            if len(slash_args) == 0:
                min_weeks = args.blacklist_minweek_default
                max_weeks = float("inf")
            elif len(slash_args) == 1:
                min_weeks = int(slash_args[0])
                max_weeks = float("inf")
            else:
                min_weeks = int(slash_args[0])
                max_weeks = int(slash_args[1])
        except:
            return jsonify(
                {
                    "response_type": "ephemeral",
                    "text": "Error: Invalid command syntax",
                    "attachments": [
                        {
                            "title": "Usage",
                            "text": "/blacklist min_weeks [max_weeks]",
                            # "color": "#FF0000",
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
            after_time = BASE_TIME,
            stdout_mode = args.stdout_mode,
        )
        last_post_datetimes = mm_channel.fetch_last_post_datetimes(priority_filter="standard", is_thread_head=True, app_name=args.app_name)

        # Convert dates to week numbers
        current_week_number = get_week_number(datetime.now())
        passed_weeks_list = sorted([(user_id, post_time) 
                                    for user_id, post_time in last_post_datetimes.items()
                                    if min_weeks <= current_week_number - get_week_number(post_time) <= max_weeks],
                                    key=lambda x: x[1], reverse=True)

        if max_weeks == float("inf"):
            message = args.blacklist_message_min.format(min_weeks)
        else:
            message = args.blacklist_message_minmax.format(min_weeks, max_weeks)
        message = (
            message + "\n"
            + "\n".join([f"{mm_channel.get_dispname_by_id(userid)} [{mm_channel.get_username_by_id(userid)}] ({current_week_number - get_week_number(post_time)})"  for userid, post_time in passed_weeks_list])
        )

        return jsonify({
            "response_type": "in_channel",
            "text": message,
            }
        )

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