﻿#Embedded file name: freestream\Core\BitTornado\BT1\track.pyo
import sys, os
import signal
import re
import pickle
from threading import Event, Thread
from urllib import quote, unquote
from urlparse import urlparse
from os.path import exists
from cStringIO import StringIO
from traceback import print_exc
from time import time, gmtime, strftime, localtime
from random import shuffle, seed
from types import StringType, IntType, LongType, DictType
from binascii import unhexlify, b2a_hex
from freestream.Core.simpledefs import *
from freestream.Core.BitTornado.parseargs import parseargs, formatDefinitions
from freestream.Core.BitTornado.RawServer import RawServer
from freestream.Core.BitTornado.HTTPHandler import HTTPHandler, months
from freestream.Core.BitTornado.parsedir import parsedir
from NatCheck import NatCheck
from T2T import T2TList
from Filter import Filter
from freestream.Core.BitTornado.subnetparse import IP_List, ipv6_to_ipv4, to_ipv4, is_valid_ip, is_ipv4
from freestream.Core.BitTornado.iprangeparse import IP_List as IP_Range_List
from freestream.Core.BitTornado.torrentlistparse import parsetorrentlist
from freestream.Core.BitTornado.bencode import bencode, bdecode, Bencached
from freestream.Core.BitTornado.zurllib import urlopen
from freestream.Core.BitTornado.clock import clock
from freestream.Core.BitTornado.__init__ import version_short, createPeerID
from freestream.Core.simpledefs import TRIBLER_TORRENT_EXT
from freestream.Core.Utilities.logger import log, log_exc
try:
    True
except:
    True = 1
    False = 0

DEBUG = False
from freestream.Core.defaults import trackerdefaults
defaults = []
for k, v in trackerdefaults.iteritems():
    defaults.append((k, v, 'See triblerAPI'))

def statefiletemplate(x):
    if type(x) != DictType:
        raise ValueError
    for cname, cinfo in x.items():
        if cname == 'peers':
            for y in cinfo.values():
                if type(y) != DictType:
                    raise ValueError
                for id, info in y.items():
                    if len(id) != 20:
                        raise ValueError
                    if type(info) != DictType:
                        raise ValueError
                    if type(info.get('ip', '')) != StringType:
                        raise ValueError
                    port = info.get('port')
                    if type(port) not in (IntType, LongType) or port < 0:
                        raise ValueError
                    left = info.get('left')
                    if type(left) not in (IntType, LongType) or left < 0:
                        raise ValueError

        elif cname == 'completed':
            if type(cinfo) != DictType:
                raise ValueError
            for y in cinfo.values():
                if type(y) not in (IntType, LongType):
                    raise ValueError

        elif cname == 'allowed':
            if type(cinfo) != DictType:
                raise ValueError
            if x.has_key('allowed_dir_files'):
                adlist = [ z[1] for z in x['allowed_dir_files'].values() ]
                for y in cinfo.keys():
                    if y not in adlist:
                        raise ValueError

        elif cname == 'allowed_dir_files':
            if type(cinfo) != DictType:
                raise ValueError
            dirkeys = {}
            for y in cinfo.values():
                if not y[1]:
                    continue
                if not x['allowed'].has_key(y[1]):
                    raise ValueError
                if dirkeys.has_key(y[1]):
                    raise ValueError
                dirkeys[y[1]] = 1


alas = 'your file may exist elsewhere in the universe\nbut alas, not here\n'
local_IPs = IP_List()
local_IPs.set_intranet_addresses()

def isotime(secs = None):
    if secs == None:
        secs = time()
    return strftime('%Y-%m-%d %H:%M UTC', gmtime(secs))


http_via_filter = re.compile(' for ([0-9.]+)\\Z')

def _get_forwarded_ip(headers):
    if headers.has_key('http_x_forwarded_for'):
        header = headers['http_x_forwarded_for']
        try:
            x, y = header.split(',')
        except:
            return header

        if not local_IPs.includes(x):
            return x
        return y
    if headers.has_key('http_client_ip'):
        return headers['http_client_ip']
    if headers.has_key('http_via'):
        x = http_via_filter.search(headers['http_via'])
        try:
            return x.group(1)
        except:
            pass

    if headers.has_key('http_from'):
        return headers['http_from']


def get_forwarded_ip(headers):
    x = _get_forwarded_ip(headers)
    if not is_valid_ip(x) or local_IPs.includes(x):
        return None
    return x


def compact_peer_info(ip, port):
    try:
        s = ''.join([ chr(int(i)) for i in ip.split('.') ]) + chr((port & 65280) >> 8) + chr(port & 255)
        if len(s) != 6:
            raise ValueError
    except:
        s = ''

    return s


def compact_ip(ip):
    try:
        return ''.join([ chr(int(i)) for i in ip.split('.') ])
    except:
        return None


def decompact_ip(cip):
    return '.'.join([ str(ord(i)) for i in cip ])


class Tracker():

    def __init__(self, config, rawserver):
        self.config = config
        self.response_size = config['tracker_response_size']
        self.dfile = config['tracker_dfile']
        self.natcheck = config['tracker_nat_check']
        favicon = config['tracker_favicon']
        self.parse_dir_interval = config['tracker_parse_dir_interval']
        self.favicon = None
        if favicon:
            try:
                h = open(favicon, 'rb')
                self.favicon = h.read()
                h.close()
            except:
                print '**warning** specified favicon file -- %s -- does not exist.' % favicon

        self.rawserver = rawserver
        self.cached = {}
        self.cached_t = {}
        self.times = {}
        self.state = {}
        self.seedcount = {}
        self.allowed_IPs = None
        self.banned_IPs = None
        if config['tracker_allowed_ips'] or config['tracker_banned_ips']:
            self.allowed_ip_mtime = 0
            self.banned_ip_mtime = 0
            self.read_ip_lists()
        self.only_local_override_ip = config['tracker_only_local_override_ip']
        if self.only_local_override_ip == 2:
            self.only_local_override_ip = not config['tracker_nat_check']
        if exists(self.dfile):
            try:
                h = open(self.dfile, 'rb')
                if self.config['tracker_dfile_format'] == ITRACKDBFORMAT_BENCODE:
                    ds = h.read()
                    tempstate = bdecode(ds)
                else:
                    tempstate = pickle.load(h)
                h.close()
                if not tempstate.has_key('peers'):
                    tempstate = {'peers': tempstate}
                statefiletemplate(tempstate)
                self.state = tempstate
            except:
                print '**warning** statefile ' + self.dfile + ' corrupt; resetting'

        self.downloads = self.state.setdefault('peers', {})
        self.completed = self.state.setdefault('completed', {})
        self.becache = {}
        for infohash, ds in self.downloads.items():
            self.seedcount[infohash] = 0
            for x, y in ds.items():
                ip = y['ip']
                if self.allowed_IPs and not self.allowed_IPs.includes(ip) or self.banned_IPs and self.banned_IPs.includes(ip):
                    del ds[x]
                    continue
                if not y['left']:
                    self.seedcount[infohash] += 1
                if y.get('nat', -1):
                    continue
                gip = y.get('given_ip')
                if is_valid_ip(gip) and (not self.only_local_override_ip or local_IPs.includes(ip)):
                    ip = gip
                self.natcheckOK(infohash, x, ip, y['port'], y['left'])

        for x in self.downloads.keys():
            self.times[x] = {}
            for y in self.downloads[x].keys():
                self.times[x][y] = 0

        self.trackerid = createPeerID('-T-')
        seed(self.trackerid)
        self.reannounce_interval = config['tracker_reannounce_interval']
        self.save_dfile_interval = config['tracker_save_dfile_interval']
        self.show_names = config['tracker_show_names']
        rawserver.add_task(self.save_state, self.save_dfile_interval)
        self.prevtime = clock()
        self.timeout_downloaders_interval = config['tracker_timeout_downloaders_interval']
        rawserver.add_task(self.expire_downloaders, self.timeout_downloaders_interval)
        self.logfile = None
        self.log = None
        if config['tracker_logfile'] and config['tracker_logfile'] != '-':
            try:
                self.logfile = config['tracker_logfile']
                self.log = open(self.logfile, 'a')
                sys.stdout = self.log
                print '# Log Started: ', isotime()
            except:
                print '**warning** could not redirect stdout to log file: ', sys.exc_info()[0]

        if config['tracker_hupmonitor']:

            def huphandler(signum, frame, self = self):
                try:
                    self.log.close()
                    self.log = open(self.logfile, 'a')
                    sys.stdout = self.log
                    print '# Log reopened: ', isotime()
                except:
                    print '**warning** could not reopen logfile'

            signal.signal(signal.SIGHUP, huphandler)
        self.allow_get = config['tracker_allow_get']
        self.t2tlist = T2TList(config['tracker_multitracker_enabled'], self.trackerid, config['tracker_multitracker_reannounce_interval'], config['tracker_multitracker_maxpeers'], config['tracker_multitracker_http_timeout'], self.rawserver)
        if config['tracker_allowed_list']:
            if config['tracker_allowed_dir']:
                print '**warning** allowed_dir and allowed_list options cannot be used together'
                print '**warning** disregarding allowed_dir'
                config['tracker_allowed_dir'] = ''
            self.allowed = self.state.setdefault('allowed_list', {})
            self.allowed_list_mtime = 0
            self.parse_allowed()
            self.remove_from_state('allowed', 'allowed_dir_files')
            if config['tracker_multitracker_allowed'] == ITRACKMULTI_ALLOW_AUTODETECT:
                config['tracker_multitracker_allowed'] = ITRACKMULTI_ALLOW_NONE
            config['tracker_allowed_controls'] = 0
        elif config['tracker_allowed_dir']:
            self.allowed = self.state.setdefault('allowed', {})
            self.allowed_dir_files = self.state.setdefault('allowed_dir_files', {})
            self.allowed_dir_blocked = {}
            self.parse_allowed()
            self.remove_from_state('allowed_list')
        else:
            self.allowed = None
            self.remove_from_state('allowed', 'allowed_dir_files', 'allowed_list')
            if config['tracker_multitracker_allowed'] == ITRACKMULTI_ALLOW_AUTODETECT:
                config['tracker_multitracker_allowed'] = ITRACKMULTI_ALLOW_NONE
            config['tracker_allowed_controls'] = 0
        self.uq_broken = unquote('+') != ' '
        self.keep_dead = config['tracker_keep_dead']
        self.Filter = Filter(rawserver.add_task)
        aggregator = config['tracker_aggregator']
        if aggregator == 0:
            self.is_aggregator = False
            self.aggregator_key = None
        else:
            self.is_aggregator = True
            if aggregator == 1:
                self.aggregator_key = None
            else:
                self.aggregator_key = aggregator
            self.natcheck = False
        send = config['tracker_aggregate_forward']
        if not send:
            self.aggregate_forward = None
        else:
            try:
                self.aggregate_forward, self.aggregate_password = send
            except:
                self.aggregate_forward = send
                self.aggregate_password = None

        self.cachetime = 0
        self.track_cachetimeupdate()

    def track_cachetimeupdate(self):
        self.cachetime += 1
        self.rawserver.add_task(self.track_cachetimeupdate, 1)

    def aggregate_senddata(self, query):
        url = self.aggregate_forward + '?' + query
        if self.aggregate_password is not None:
            url += '&password=' + self.aggregate_password
        rq = Thread(target=self._aggregate_senddata, args=[url])
        rq.setName('AggregateSendData' + rq.getName())
        rq.setDaemon(True)
        rq.start()

    def _aggregate_senddata(self, url):
        try:
            h = urlopen(url)
            h.read()
            h.close()
        except:
            return

    def get_infopage(self):
        try:
            if not self.config['tracker_show_infopage']:
                return (404,
                 'Not Found',
                 {'Content-Type': 'text/plain',
                  'Pragma': 'no-cache'},
                 alas)
            red = self.config['tracker_infopage_redirect']
            if red:
                return (302,
                 'Found',
                 {'Content-Type': 'text/html',
                  'Location': red},
                 '<A HREF="' + red + '">Click Here</A>')
            s = StringIO()
            s.write('<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" "http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">\n<html><head><title>freestream Tracker Statistics</title>\n')
            if self.favicon is not None:
                s.write('<link rel="shortcut icon" href="/favicon.ico">\n')
            s.write('</head>\n<body>\n<h3>freestream Tracker Statistics</h3>\n')
            if self.config['tracker_allowed_dir']:
                if self.show_names:
                    names = [ (self.allowed[hash]['name'], hash) for hash in self.allowed.keys() ]
                else:
                    names = [ (None, hash) for hash in self.allowed.keys() ]
            else:
                names = [ (None, hash) for hash in self.downloads.keys() ]
            if not names:
                s.write('<p>Not tracking any files yet...</p>\n')
            else:
                names.sort()
                tn = 0
                tc = 0
                td = 0
                tt = 0
                ts = 0
                nf = 0
                if self.config['tracker_allowed_dir'] and self.show_names:
                    s.write('<table summary="files" border="1">\n<tr><th>info hash</th><th>torrent name</th><th align="right">size</th><th align="right">complete</th><th align="right">downloading</th><th align="right">downloaded</th><th align="right">transferred</th></tr>\n')
                else:
                    s.write('<table summary="files">\n<tr><th>info hash</th><th align="right">complete</th><th align="right">downloading</th><th align="right">downloaded</th></tr>\n')
                for name, hash in names:
                    l = self.downloads[hash]
                    n = self.completed.get(hash, 0)
                    tn = tn + n
                    c = self.seedcount[hash]
                    tc = tc + c
                    d = len(l) - c
                    td = td + d
                    if self.config['tracker_allowed_dir'] and self.show_names:
                        if self.allowed.has_key(hash):
                            nf = nf + 1
                            sz = self.allowed[hash]['length']
                            ts = ts + sz
                            szt = sz * n
                            tt = tt + szt
                            if self.allow_get == 1:
                                url = self.allowed[hash].get('url')
                                if url:
                                    linkname = '<a href="' + url + '">' + name + '</a>'
                                else:
                                    linkname = '<a href="/file?name=' + quote(name) + '">' + name + '</a>'
                            else:
                                linkname = name
                            s.write('<tr><td><code>%s</code></td><td>%s</td><td align="right">%s</td><td align="right">%i</td><td align="right" class="downloading">%i</td><td align="right">%i</td><td align="right">%s</td></tr>\n' % (b2a_hex(hash),
                             linkname,
                             size_format(sz),
                             c,
                             d,
                             n,
                             size_format(szt)))
                    else:
                        s.write('<tr><td><code>%s</code></td><td align="right"><code>%i</code></td><td align="right"><code>%i</code></td><td align="right"><code>%i</code></td></tr>\n' % (b2a_hex(hash),
                         c,
                         d,
                         n))

                ttn = 0
                for i in self.completed.values():
                    ttn = ttn + i

                if self.config['tracker_allowed_dir'] and self.show_names:
                    s.write('<tr><td align="right" colspan="2">%i files</td><td align="right">%s</td><td align="right">%i</td><td align="right">%i</td><td align="right">%i/%i</td><td align="right">%s</td></tr>\n' % (nf,
                     size_format(ts),
                     tc,
                     td,
                     tn,
                     ttn,
                     size_format(tt)))
                else:
                    s.write('<tr><td align="right">%i files</td><td align="right">%i</td><td align="right">%i</td><td align="right">%i/%i</td></tr>\n' % (nf,
                     tc,
                     td,
                     tn,
                     ttn))
                s.write('</table>\n<ul>\n<li><em>info hash:</em> SHA1 hash of the "info" section of the metainfo (*.torrent)</li>\n<li><em>complete:</em> number of connected clients with the complete file</li>\n<li><em>downloading:</em> number of connected clients still downloading</li>\n<li><em>downloaded:</em> reported complete downloads (total: current/all)</li>\n<li><em>transferred:</em> torrent size * total downloaded (does not include partial transfers)</li>\n</ul>\n<hr>\n<address>%s (%s)</address>\n' % (version_short, isotime()))
            s.write('</body>\n</html>\n')
            return (200,
             'OK',
             {'Content-Type': 'text/html; charset=iso-8859-1'},
             s.getvalue())
        except:
            print_exc()
            return (500,
             'Internal Server Error',
             {'Content-Type': 'text/html; charset=iso-8859-1'},
             'Server Error')

    def scrapedata(self, hash, return_name = True):
        l = self.downloads[hash]
        n = self.completed.get(hash, 0)
        c = self.seedcount[hash]
        d = len(l) - c
        f = {'complete': c,
         'incomplete': d,
         'downloaded': n}
        if return_name and self.show_names and self.config['tracker_allowed_dir']:
            f['name'] = self.allowed[hash]['name']
        return f

    def get_scrape(self, paramslist):
        fs = {}
        if paramslist.has_key('info_hash'):
            if self.config['tracker_scrape_allowed'] not in [ITRACKSCRAPE_ALLOW_SPECIFIC, ITRACKSCRAPE_ALLOW_FULL]:
                return (400,
                 'Not Authorized',
                 {'Content-Type': 'text/plain',
                  'Pragma': 'no-cache'},
                 bencode({'failure reason': 'specific scrape function is not available with this tracker.'}))
            for hash in paramslist['info_hash']:
                if self.allowed is not None:
                    if self.allowed.has_key(hash):
                        fs[hash] = self.scrapedata(hash)
                elif self.downloads.has_key(hash):
                    fs[hash] = self.scrapedata(hash)

        else:
            if self.config['tracker_scrape_allowed'] != ITRACKSCRAPE_ALLOW_FULL:
                return (400,
                 'Not Authorized',
                 {'Content-Type': 'text/plain',
                  'Pragma': 'no-cache'},
                 bencode({'failure reason': 'full scrape function is not available with this tracker.'}))
            if self.allowed is not None:
                keys = self.allowed.keys()
            else:
                keys = self.downloads.keys()
            for hash in keys:
                fs[hash] = self.scrapedata(hash)

        return (200,
         'OK',
         {'Content-Type': 'text/plain'},
         bencode({'files': fs}))

    def get_peers(self, infohash):
        data = ''
        if infohash not in self.downloads:
            data = 'no peers'
        else:
            data = str(self.downloads[infohash])
        return (200,
         'OK',
         {'Content-Type': 'text/plain'},
         data)

    def get_file_by_name(self, name):
        for hash, rec in self.allowed.iteritems():
            if 'name' in rec and rec['name'] == name:
                return self.get_file(hash)

        return (404,
         'Not Found',
         {'Content-Type': 'text/plain',
          'Pragma': 'no-cache'},
         alas)

    def get_file(self, hash):
        if not self.allow_get:
            return (400,
             'Not Authorized',
             {'Content-Type': 'text/plain',
              'Pragma': 'no-cache'},
             'get function is not available with this tracker.')
        if not self.allowed.has_key(hash):
            return (404,
             'Not Found',
             {'Content-Type': 'text/plain',
              'Pragma': 'no-cache'},
             alas)
        fname = self.allowed[hash]['file']
        fpath = self.allowed[hash]['path']
        return (200,
         'OK',
         {'Content-Type': 'application/x-bittorrent',
          'Content-Disposition': 'attachment; filename=' + fname},
         open(fpath, 'rb').read())

    def get_tstream_from_httpseed(self, httpseedurl):
        if not self.allow_get:
            return (400,
             'Not Authorized',
             {'Content-Type': 'text/plain',
              'Pragma': 'no-cache'},
             'get function is not available with this tracker.')
        wanturlhash = sha(httpseedurl).digest()
        found = False
        for infohash, a in self.allowed.iteritems():
            for goturlhash in a['url-hash-list']:
                if goturlhash == wanturlhash:
                    found = True
                    break

            if found:
                break

        if not found or not self.allowed.has_key(infohash):
            return (404,
             'Not Found',
             {'Content-Type': 'text/plain',
              'Pragma': 'no-cache'},
             alas)
        fname = self.allowed[infohash]['file']
        fpath = self.allowed[infohash]['path']
        print >> sys.stderr, 'tracker: get_stream: Sending', fname
        return (200,
         'OK',
         {'Content-Type': 'application/x-bittorrent',
          'Content-Disposition': 'attachment; filename=' + fname},
         open(fpath, 'rb').read())

    def check_allowed(self, infohash, paramslist):
        if self.aggregator_key is not None and not (paramslist.has_key('password') and paramslist['password'][0] == self.aggregator_key):
            return (200,
             'Not Authorized',
             {'Content-Type': 'text/plain',
              'Pragma': 'no-cache'},
             bencode({'failure reason': 'Requested download is not authorized for use with this tracker.'}))
        if self.allowed is not None:
            if not self.allowed.has_key(infohash):
                return (200,
                 'Not Authorized',
                 {'Content-Type': 'text/plain',
                  'Pragma': 'no-cache'},
                 bencode({'failure reason': 'Requested download is not authorized for use with this tracker.'}))
            if self.config['tracker_allowed_controls']:
                if self.allowed[infohash].has_key('failure reason'):
                    return (200,
                     'Not Authorized',
                     {'Content-Type': 'text/plain',
                      'Pragma': 'no-cache'},
                     bencode({'failure reason': self.allowed[infohash]['failure reason']}))
        if paramslist.has_key('tracker'):
            if self.config['tracker_multitracker_allowed'] == ITRACKMULTI_ALLOW_NONE or paramslist['peer_id'][0] == self.trackerid:
                return (200,
                 'Not Authorized',
                 {'Content-Type': 'text/plain',
                  'Pragma': 'no-cache'},
                 bencode({'failure reason': 'disallowed'}))
            if self.config['tracker_multitracker_allowed'] == ITRACKMULTI_ALLOW_AUTODETECT and not self.allowed[infohash].has_key('announce-list'):
                return (200,
                 'Not Authorized',
                 {'Content-Type': 'text/plain',
                  'Pragma': 'no-cache'},
                 bencode({'failure reason': 'Requested download is not authorized for multitracker use.'}))

    def add_data(self, infohash, event, ip, paramslist):
        peers = self.downloads.setdefault(infohash, {})
        ts = self.times.setdefault(infohash, {})
        self.completed.setdefault(infohash, 0)
        self.seedcount.setdefault(infohash, 0)

        def params(key, default = None, l = paramslist):
            if l.has_key(key):
                return l[key][0]
            return default

        myid = params('peer_id', '')
        if len(myid) != 20:
            raise ValueError, 'id not of length 20'
        if event not in ('started', 'completed', 'stopped', 'snooped', None):
            raise ValueError, 'invalid event'
        port = long(params('port', ''))
        if port < 0 or port > 65535:
            raise ValueError, 'invalid port'
        left = long(params('left', ''))
        if left < 0:
            raise ValueError, 'invalid amount left'
        uploaded = long(params('uploaded', ''))
        downloaded = long(params('downloaded', ''))
        peer = peers.get(myid)
        islocal = local_IPs.includes(ip)
        mykey = params('key')
        if peer:
            auth = peer.get('key', -1) == mykey or peer.get('ip') == ip
        else:
            auth = None
        gip = params('ip')
        if is_valid_ip(gip) and (islocal or not self.only_local_override_ip):
            ip1 = gip
        else:
            ip1 = ip
        if params('numwant') is not None:
            rsize = min(int(params('numwant')), self.response_size)
        else:
            rsize = self.response_size
        if DEBUG:
            log('itracker::add_data: infohash', infohash, 'event', event, 'ip', ip, 'gip', gip, 'port', port, 'myid', myid, 'mykey', mykey, 'auth', auth)
        if event == 'stopped':
            if peer:
                if auth:
                    if DEBUG:
                        log('itracker::add_data: delete peer: infohash', infohash, 'myid', myid)
                    self.delete_peer(infohash, myid)
        elif not peer:
            ts[myid] = clock()
            peer = {'ip': ip,
             'port': port,
             'left': left}
            if mykey:
                peer['key'] = mykey
            if gip:
                peer['given ip'] = gip
            if port:
                if not self.natcheck or islocal:
                    peer['nat'] = 0
                    self.natcheckOK(infohash, myid, ip1, port, left)
                else:
                    NatCheck(self.connectback_result, infohash, myid, ip1, port, self.rawserver)
            else:
                peer['nat'] = 1073741824
            if event == 'completed':
                self.completed[infohash] += 1
            if not left:
                self.seedcount[infohash] += 1
            if DEBUG:
                log('itracker::add_data: add new peer: myid', myid, 'peer', peer)
            peers[myid] = peer
        else:
            if not auth:
                return rsize
            ts[myid] = clock()
            if not left and peer['left']:
                self.completed[infohash] += 1
                self.seedcount[infohash] += 1
                if not peer.get('nat', -1):
                    for bc in self.becache[infohash]:
                        bc[1][myid] = bc[0][myid]
                        del bc[0][myid]

            if peer['left']:
                peer['left'] = left
            if port:
                recheck = False
                if ip != peer['ip']:
                    peer['ip'] = ip
                    recheck = True
                if gip != peer.get('given ip'):
                    if gip:
                        peer['given ip'] = gip
                    elif peer.has_key('given ip'):
                        del peer['given ip']
                    recheck = True
                natted = peer.get('nat', -1)
                if recheck:
                    if natted == 0:
                        l = self.becache[infohash]
                        y = not peer['left']
                        for x in l:
                            try:
                                del x[y][myid]
                            except KeyError:
                                pass

                        if not self.natcheck or islocal:
                            del peer['nat']
                if natted and natted < self.natcheck:
                    recheck = True
                if recheck:
                    if not self.natcheck or islocal:
                        peer['nat'] = 0
                        self.natcheckOK(infohash, myid, ip1, port, left)
                    else:
                        NatCheck(self.connectback_result, infohash, myid, ip1, port, self.rawserver)
        return rsize

    def peerlist(self, infohash, stopped, tracker, is_seed, return_type, rsize):
        data = {}
        seeds = self.seedcount[infohash]
        data['complete'] = seeds
        data['incomplete'] = len(self.downloads[infohash]) - seeds
        if self.config['tracker_allowed_controls'] and self.allowed[infohash].has_key('warning message'):
            data['warning message'] = self.allowed[infohash]['warning message']
        if tracker:
            data['interval'] = self.config['tracker_multitracker_reannounce_interval']
            if not rsize:
                return data
            cache = self.cached_t.setdefault(infohash, None)
            if not cache or len(cache[1]) < rsize or cache[0] + self.config['tracker_min_time_between_cache_refreshes'] < clock():
                bc = self.becache.setdefault(infohash, [[{}, {}], [{}, {}], [{}, {}]])
                cache = [clock(), bc[0][0].values() + bc[0][1].values()]
                self.cached_t[infohash] = cache
                shuffle(cache[1])
                cache = cache[1]
            data['peers'] = cache[-rsize:]
            del cache[-rsize:]
            return data
        data['interval'] = self.reannounce_interval
        if stopped or not rsize:
            data['peers'] = []
            return data
        bc = self.becache.setdefault(infohash, [[{}, {}], [{}, {}], [{}, {}]])
        len_l = len(bc[0][0])
        len_s = len(bc[0][1])
        if not len_l + len_s:
            data['peers'] = []
            return data
        l_get_size = int(float(rsize) * len_l / (len_l + len_s))
        cache = self.cached.setdefault(infohash, [None, None, None])[return_type]
        if cache and (not cache[1] or is_seed and len(cache[1]) < rsize or len(cache[1]) < l_get_size or cache[0] + self.config['tracker_min_time_between_cache_refreshes'] < self.cachetime):
            cache = None
        if not cache:
            peers = self.downloads[infohash]
            vv = [[], [], []]
            for key, ip, port in self.t2tlist.harvest(infohash):
                if not peers.has_key(key):
                    vv[0].append({'ip': ip,
                     'port': port,
                     'peer id': key})
                    vv[1].append({'ip': ip,
                     'port': port})
                    vv[2].append(compact_peer_info(ip, port))

            cache = [self.cachetime, bc[return_type][0].values() + vv[return_type], bc[return_type][1].values()]
            shuffle(cache[1])
            shuffle(cache[2])
            self.cached[infohash][return_type] = cache
            for rr in xrange(len(self.cached[infohash])):
                if rr != return_type:
                    try:
                        self.cached[infohash][rr][1].extend(vv[rr])
                    except:
                        pass

        if len(cache[1]) < l_get_size:
            peerdata = cache[1]
            if not is_seed:
                peerdata.extend(cache[2])
            cache[1] = []
            cache[2] = []
        else:
            if not is_seed:
                peerdata = cache[2][l_get_size - rsize:]
                del cache[2][l_get_size - rsize:]
                rsize -= len(peerdata)
            else:
                peerdata = []
            if rsize:
                peerdata.extend(cache[1][-rsize:])
                del cache[1][-rsize:]
        if return_type == 2:
            peerdata = ''.join(peerdata)
        data['peers'] = peerdata
        return data

    def get(self, connection, path, headers):
        real_ip = connection.get_ip()
        ip = real_ip
        if is_ipv4(ip):
            ipv4 = True
        else:
            try:
                ip = ipv6_to_ipv4(ip)
                ipv4 = True
            except ValueError:
                ipv4 = False

        if self.config['tracker_logfile']:
            self.getlog(ip, path, headers)
        if self.allowed_IPs and not self.allowed_IPs.includes(ip) or self.banned_IPs and self.banned_IPs.includes(ip):
            return (400,
             'Not Authorized',
             {'Content-Type': 'text/plain',
              'Pragma': 'no-cache'},
             bencode({'failure reason': 'your IP is not allowed on this tracker'}))
        nip = get_forwarded_ip(headers)
        if nip and not self.only_local_override_ip:
            ip = nip
            try:
                ip = to_ipv4(ip)
                ipv4 = True
            except ValueError:
                ipv4 = False

        paramslist = {}

        def params(key, default = None, l = paramslist):
            if l.has_key(key):
                return l[key][0]
            return default

        try:
            scheme, netloc, path, pars, query, fragment = urlparse(path)
            if self.uq_broken == 1:
                path = path.replace('+', ' ')
                query = query.replace('+', ' ')
            path = unquote(path)[1:]
            for s in query.split('&'):
                if s:
                    i = s.find('=')
                    if i == -1:
                        break
                    kw = unquote(s[:i])
                    paramslist.setdefault(kw, [])
                    paramslist[kw] += [unquote(s[i + 1:])]

            if DEBUG:
                _t_start_request = time()
                print >> sys.stderr, 'tracker: Got request /' + path + '?' + query
            if path == '' or path == 'index.html':
                return self.get_infopage()
            if path == 'file':
                if paramslist.has_key('name'):
                    return self.get_file_by_name(params('name'))
                else:
                    return self.get_file(params('info_hash'))
            if path == 'tlookup':
                return self.get_tstream_from_httpseed(unquote(query))
            if path == 'favicon.ico' and self.favicon is not None:
                return (200,
                 'OK',
                 {'Content-Type': 'image/x-icon'},
                 self.favicon)
            if path == 'scrape':
                return self.get_scrape(paramslist)
            if path == 'peers':
                infohash = params('infohash')
                if infohash is None:
                    raise ValueError, 'no infohash'
                return self.get_peers(infohash)
            if path != 'announce':
                return (404,
                 'Not Found',
                 {'Content-Type': 'text/plain',
                  'Pragma': 'no-cache'},
                 alas)
            filtered = self.Filter.check(real_ip, paramslist, headers)
            if filtered:
                return (400,
                 'Not Authorized',
                 {'Content-Type': 'text/plain',
                  'Pragma': 'no-cache'},
                 bencode({'failure reason': filtered}))
            infohash = params('info_hash')
            if not infohash:
                raise ValueError, 'no info hash'
            notallowed = self.check_allowed(infohash, paramslist)
            if notallowed:
                return notallowed
            event = params('event')
            rsize = self.add_data(infohash, event, ip, paramslist)
        except ValueError as e:
            if DEBUG:
                print_exc()
            return (400,
             'Bad Request',
             {'Content-Type': 'text/plain'},
             'you sent me garbage - ' + str(e))

        if self.aggregate_forward and not paramslist.has_key('tracker'):
            self.aggregate_senddata(query)
        if self.is_aggregator:
            return (200,
             'OK',
             {'Content-Type': 'text/plain',
              'Pragma': 'no-cache'},
             bencode({'response': 'OK'}))
        if params('compact') and ipv4:
            return_type = 2
        elif params('no_peer_id'):
            return_type = 1
        else:
            return_type = 0
        data = self.peerlist(infohash, event == 'stopped', params('tracker'), not params('left'), return_type, rsize)
        if paramslist.has_key('scrape'):
            data['scrape'] = self.scrapedata(infohash, False)
        if DEBUG:
            print >> sys.stderr, 'Tracker: request time:', time() - _t_start_request
        return (200,
         'OK',
         {'Content-Type': 'text/plain',
          'Pragma': 'no-cache'},
         bencode(data))

    def natcheckOK(self, infohash, peerid, ip, port, not_seed):
        if DEBUG:
            print >> sys.stderr, 'tracker: natcheck: Recorded succes'
        bc = self.becache.setdefault(infohash, [[{}, {}], [{}, {}], [{}, {}]])
        bc[0][not not_seed][peerid] = Bencached(bencode({'ip': ip,
         'port': port,
         'peer id': peerid}))
        bc[1][not not_seed][peerid] = Bencached(bencode({'ip': ip,
         'port': port}))
        bc[2][not not_seed][peerid] = compact_peer_info(ip, port)

    def natchecklog(self, peerid, ip, port, result):
        year, month, day, hour, minute, second, a, b, c = localtime(time())
        print '%s - %s [%02d/%3s/%04d:%02d:%02d:%02d] "!natcheck-%s:%i" %i 0 - -' % (ip,
         quote(peerid),
         day,
         months[month],
         year,
         hour,
         minute,
         second,
         ip,
         port,
         result)

    def getlog(self, ip, path, headers):
        year, month, day, hour, minute, second, a, b, c = localtime(time())
        print '%s - %s [%02d/%3s/%04d:%02d:%02d:%02d] "GET %s HTTP/1.1" 100 0 - -' % (ip,
         ip,
         day,
         months[month],
         year,
         hour,
         minute,
         second,
         path)

    def connectback_result(self, result, downloadid, peerid, ip, port):
        record = self.downloads.get(downloadid, {}).get(peerid)
        if record is None or record['ip'] != ip and record.get('given ip') != ip or record['port'] != port:
            if self.config['tracker_log_nat_checks']:
                self.natchecklog(peerid, ip, port, 404)
            if DEBUG:
                print >> sys.stderr, 'tracker: natcheck: No record found for tested peer'
            return
        if self.config['tracker_log_nat_checks']:
            if result:
                x = 200
            else:
                x = 503
            self.natchecklog(peerid, ip, port, x)
        if not record.has_key('nat'):
            record['nat'] = int(not result)
            if result:
                self.natcheckOK(downloadid, peerid, ip, port, record['left'])
        elif result and record['nat']:
            record['nat'] = 0
            self.natcheckOK(downloadid, peerid, ip, port, record['left'])
        elif not result:
            record['nat'] += 1
            if DEBUG:
                print >> sys.stderr, 'tracker: natcheck: Recorded failed attempt'

    def remove_from_state(self, *l):
        for s in l:
            try:
                del self.state[s]
            except:
                pass

    def save_state(self):
        self.rawserver.add_task(self.save_state, self.save_dfile_interval)
        h = None
        try:
            h = open(self.dfile, 'wb')
            if self.config['tracker_dfile_format'] == ITRACKDBFORMAT_BENCODE:
                h.write(bencode(self.state))
            else:
                pickle.dump(self.state, h, -1)
            h.close()
        except:
            if DEBUG:
                print_exc()
        finally:
            if h is not None:
                h.close()

    def parse_allowed(self, source = None):
        if DEBUG:
            print >> sys.stderr, 'tracker: parse_allowed: Source is', source, 'alloweddir', self.config['tracker_allowed_dir']
        if source is None:
            self.rawserver.add_task(self.parse_allowed, self.parse_dir_interval)
        if self.config['tracker_allowed_dir']:
            r = parsedir(self.config['tracker_allowed_dir'], self.allowed, self.allowed_dir_files, self.allowed_dir_blocked, ['.torrent', TRIBLER_TORRENT_EXT])
            self.allowed, self.allowed_dir_files, self.allowed_dir_blocked, added, garbage2 = r
            if DEBUG:
                print >> sys.stderr, 'tracker: parse_allowed: Found new', `added`
            self.state['allowed'] = self.allowed
            self.state['allowed_dir_files'] = self.allowed_dir_files
            self.t2tlist.parse(self.allowed)
        else:
            f = self.config['tracker_allowed_list']
            if self.allowed_list_mtime == os.path.getmtime(f):
                return
            try:
                r = parsetorrentlist(f, self.allowed)
                self.allowed, added, garbage2 = r
                self.state['allowed_list'] = self.allowed
            except (IOError, OSError):
                print '**warning** unable to read allowed torrent list'
                return

            self.allowed_list_mtime = os.path.getmtime(f)
        for infohash in added.keys():
            self.downloads.setdefault(infohash, {})
            self.completed.setdefault(infohash, 0)
            self.seedcount.setdefault(infohash, 0)

    def read_ip_lists(self):
        self.rawserver.add_task(self.read_ip_lists, self.parse_dir_interval)
        f = self.config['tracker_allowed_ips']
        if f and self.allowed_ip_mtime != os.path.getmtime(f):
            self.allowed_IPs = IP_List()
            try:
                self.allowed_IPs.read_fieldlist(f)
                self.allowed_ip_mtime = os.path.getmtime(f)
            except (IOError, OSError):
                print '**warning** unable to read allowed_IP list'

        f = self.config['tracker_banned_ips']
        if f and self.banned_ip_mtime != os.path.getmtime(f):
            self.banned_IPs = IP_Range_List()
            try:
                self.banned_IPs.read_rangelist(f)
                self.banned_ip_mtime = os.path.getmtime(f)
            except (IOError, OSError):
                print '**warning** unable to read banned_IP list'

    def delete_peer(self, infohash, peerid):
        try:
            dls = self.downloads[infohash]
            peer = dls[peerid]
            if not peer['left']:
                self.seedcount[infohash] -= 1
            if not peer.get('nat', -1):
                l = self.becache[infohash]
                y = not peer['left']
                for x in l:
                    del x[y][peerid]

            del self.times[infohash][peerid]
            del dls[peerid]
        except:
            if DEBUG:
                print_exc()

    def expire_downloaders(self):
        for x in self.times.keys():
            for myid, t in self.times[x].items():
                if t < self.prevtime:
                    self.delete_peer(x, myid)

        self.prevtime = clock()
        if self.keep_dead != 1:
            for key, value in self.downloads.items():
                if len(value) == 0 and (self.allowed is None or not self.allowed.has_key(key)):
                    del self.times[key]
                    del self.downloads[key]
                    del self.seedcount[key]

        self.rawserver.add_task(self.expire_downloaders, self.timeout_downloaders_interval)


def track(args):
    if not args:
        print formatDefinitions(defaults, 80)
        return
    try:
        config, files = parseargs(args, defaults, 0, 0)
    except ValueError as e:
        print 'error: ' + str(e)
        print 'run with no arguments for parameter explanations'
        return

    r = RawServer(Event(), config['tracker_timeout_check_interval'], config['tracker_socket_timeout'], ipv6_enable=config['ipv6_enabled'])
    t = Tracker(config, r)
    r.bind(config['minport'], config['bind'], reuse=True, ipv6_socket_style=config['ipv6_binds_v4'])
    r.listen_forever(HTTPHandler(t.get, config['min_time_between_log_flushes']))
    t.save_state()
    print '# Shutting down: ' + isotime()


def size_format(s):
    if s < 1024:
        r = str(s) + 'B'
    elif s < 1048576:
        r = str(int(s / 1024)) + 'KiB'
    elif s < 1073741824L:
        r = str(int(s / 1048576)) + 'MiB'
    elif s < 1099511627776L:
        r = str(int(s / 1073741824.0 * 100.0) / 100.0) + 'GiB'
    else:
        r = str(int(s / 1099511627776.0 * 100.0) / 100.0) + 'TiB'
    return r
