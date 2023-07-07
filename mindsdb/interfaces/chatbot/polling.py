from threading import Event

import time

from mindsdb_sql.parser.ast import Identifier, Select, Insert

from collections import defaultdict
from mindsdb.utilities import log

from .types import ChatBotMessage, BotException


class BasePolling:
    def __init__(self, chat_task, chat_params):
        self.params = chat_params
        self.chat_task = chat_task

    def start(self):
        raise NotImplementedError

    def stop(self):
        raise NotImplementedError

    def send_message(self, message: ChatBotMessage):
        raise NotImplementedError


class MessageCountPolling(BasePolling):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._to_stop = False
        self.chats_prev = None

    def run(self):

        self.chat_memory = defaultdict(dict)

        while True:
            try:
                chat_ids = self.check_message_count()
                for chat_id in chat_ids:
                    chat_memory = self.chat_task.memory.get_chat(chat_id)

                    message = self.get_last_message(chat_memory)
                    if message:
                        self.chat_task.on_message(chat_memory, message)

            except Exception as e:
                log.logger.error(e)

            if self._to_stop:
                return
            log.logger.debug('running ' + self.chat_task.bot_record.name)
            time.sleep(7)

    def get_last_message(self, chat_memory):
        # retrive from history
        history = chat_memory.get_history(cached=False)
        last_message = history[-1]
        if last_message.user == self.params['bot_username']:
            # the last message is from bot
            return
        return last_message

    def check_message_count(self):
        p_params = self.params['polling']

        chat_ids = []

        id_col = p_params['chat_id_col']
        msgs_col = p_params['count_col']
        # get chats status info
        ast_query = Select(
            targets=[
                Identifier(id_col),
                Identifier(msgs_col)],
            from_table=Identifier(p_params['table'])
        )

        resp = self.chat_task.chat_handler.query(query=ast_query)
        if resp.data_frame is None:
            raise BotException('Error to get count of messages')

        chats = {}
        for row in resp.data_frame.to_dict('records'):
            chat_id = row[id_col]
            msgs = row[msgs_col]

            chats[chat_id] = msgs

        if self.chats_prev is None:
            # first run
            self.chats_prev = chats
        else:
            # compare
            # for new keys
            for chat_id, count_msgs in chats.items():
                if self.chats_prev.get(chat_id) != count_msgs:
                    chat_ids.append(chat_id)

            self.chats_prev = chats
        return chat_ids

    def send_message(self, message: ChatBotMessage):
        chat_id = message.destination
        text = message.text

        t_params = self.params['chat_table']
        ast_query = Insert(
            table=Identifier(t_params['name']),
            columns=[t_params['chat_id_col'], t_params['text_col']],
            values=[
                [chat_id, text],
            ]
        )

        self.chat_task.chat_handler.query(ast_query)

    def stop(self):
        self._to_stop = True


class RealtimePolling(BasePolling):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._stop_event = Event()

    def _callback(self, message: ChatBotMessage):
        chat_id = message.destination

        chat_memory = self.chat_task.memory.get_chat(chat_id)
        self.chat_task.on_message(chat_memory, message)

    def start(self):
        self.chat_task.chat_handler.realtime_subscribe(self._callback)
        self._stop_event.wait()

    def send_message(self, message: ChatBotMessage):
        self.chat_task.chat_handler.realtime_send(message)

    def stop(self):
        self._stop_event.set()