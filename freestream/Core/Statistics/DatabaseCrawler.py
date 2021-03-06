﻿#Embedded file name: freestream\Core\Statistics\DatabaseCrawler.pyo
import sys
import cPickle
from time import strftime
from freestream.Core.Overlay.SecureOverlay import OLPROTO_VER_SEVENTH, OLPROTO_VER_EIGHTH, OLPROTO_VER_ELEVENTH
from freestream.Core.BitTornado.BT1.MessageID import CRAWLER_DATABASE_QUERY
from freestream.Core.CacheDB.sqlitecachedb import SQLiteCacheDB
from freestream.Core.Utilities.utilities import show_permid, show_permid_short
from freestream.Core.Statistics.Crawler import Crawler
DEBUG = False

class DatabaseCrawler:
    __single = None

    @classmethod
    def get_instance(cls, *args, **kargs):
        if not cls.__single:
            cls.__single = cls(*args, **kargs)
        return cls.__single

    def __init__(self):
        self._sqlite_cache_db = SQLiteCacheDB.getInstance()
        crawler = Crawler.get_instance()
        if crawler.am_crawler():
            self._file = open('databasecrawler.txt', 'a')
            self._file.write(''.join(('# ',
             '*' * 80,
             '\n# ',
             strftime('%Y/%m/%d %H:%M:%S'),
             ' Crawler started\n')))
            self._file.flush()
        else:
            self._file = None

    def query_initiator(self, permid, selversion, request_callback):
        if DEBUG:
            print >> sys.stderr, 'databasecrawler: query_initiator', show_permid_short(permid)
        sql = []
        if selversion >= OLPROTO_VER_SEVENTH:
            sql.extend(("SELECT 'peer_count', count(*) FROM Peer", "SELECT 'torrent_count', count(*) FROM Torrent"))
        if selversion >= OLPROTO_VER_ELEVENTH:
            sql.extend(("SELECT 'my_subscriptions', count(*) FROM VoteCast where voter_id='" + show_permid(permid) + "' and vote=2",
             "SELECT 'my_negative_votes', count(*) FROM VoteCast where voter_id='" + show_permid(permid) + "' and vote=-1",
             "SELECT 'my_channel_files', count(*) FROM ChannelCast where publisher_id='" + show_permid(permid) + "'",
             "SELECT 'all_subscriptions', count(*) FROM VoteCast where vote=2",
             "SELECT 'all_negative_votes', count(*) FROM VoteCast where vote=-1"))
        request_callback(CRAWLER_DATABASE_QUERY, ';'.join(sql), callback=self._after_request_callback)

    def _after_request_callback(self, exc, permid):
        if not exc:
            if DEBUG:
                print >> sys.stderr, 'databasecrawler: request send to', show_permid_short(permid)
            self._file.write('; '.join((strftime('%Y/%m/%d %H:%M:%S'),
             'REQUEST',
             show_permid(permid),
             '\n')))
            self._file.flush()

    def handle_crawler_request(self, permid, selversion, channel_id, message, reply_callback):
        if DEBUG:
            print >> sys.stderr, 'databasecrawler: handle_crawler_request', show_permid_short(permid), message
        try:
            cursor = self._sqlite_cache_db.execute_read(message)
        except Exception as e:
            reply_callback(str(e), error=1)
        else:
            if cursor:
                reply_callback(cPickle.dumps(list(cursor), 2))
            else:
                reply_callback('error', error=2)

    def handle_crawler_reply(self, permid, selversion, channel_id, channel_data, error, message, request_callback):
        if error:
            if DEBUG:
                print >> sys.stderr, 'databasecrawler: handle_crawler_reply', error, message
            self._file.write('; '.join((strftime('%Y/%m/%d %H:%M:%S'),
             '  REPLY',
             show_permid(permid),
             str(error),
             message,
             '\n')))
            self._file.flush()
        else:
            if DEBUG:
                print >> sys.stderr, 'databasecrawler: handle_crawler_reply', show_permid_short(permid), cPickle.loads(message)
            self._file.write('; '.join((strftime('%Y/%m/%d %H:%M:%S'),
             '  REPLY',
             show_permid(permid),
             str(error),
             str(cPickle.loads(message)),
             '\n')))
            self._file.flush()
