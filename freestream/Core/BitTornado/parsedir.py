﻿#Embedded file name: freestream\Core\BitTornado\parsedir.pyo
import os
import sys
from traceback import print_exc
from bencode import bencode, bdecode
from BT1.btformats import check_info
from freestream.Core.simpledefs import TRIBLER_TORRENT_EXT
from freestream.Core.TorrentDef import TorrentDef
try:
    True
except:
    True = 1
    False = 0

DEBUG = False

def _errfunc(x):
    print >> sys.stderr, 'tracker: parsedir: ' + x


def parsedir(directory, parsed, files, blocked, exts = ['.torrent', TRIBLER_TORRENT_EXT], return_metainfo = False, errfunc = _errfunc):
    if DEBUG:
        errfunc('checking dir')
    dirs_to_check = [directory]
    new_files = {}
    new_blocked = {}
    torrent_type = {}
    while dirs_to_check:
        directory = dirs_to_check.pop()
        newtorrents = False
        for f in os.listdir(directory):
            newtorrent = None
            for ext in exts:
                if f.endswith(ext):
                    newtorrent = ext[1:]
                    break

            if newtorrent:
                newtorrents = True
                p = os.path.join(directory, f)
                new_files[p] = [(int(os.path.getmtime(p)), os.path.getsize(p)), 0]
                torrent_type[p] = newtorrent

        if not newtorrents:
            for f in os.listdir(directory):
                p = os.path.join(directory, f)
                if os.path.isdir(p):
                    dirs_to_check.append(p)

    new_parsed = {}
    to_add = []
    added = {}
    removed = {}
    for p, v in new_files.items():
        oldval = files.get(p)
        if not oldval:
            to_add.append(p)
            continue
        h = oldval[1]
        if oldval[0] == v[0]:
            if h:
                if blocked.has_key(p):
                    to_add.append(p)
                else:
                    new_parsed[h] = parsed[h]
                new_files[p] = oldval
            else:
                new_blocked[p] = 1
            continue
        if parsed.has_key(h) and not blocked.has_key(p):
            if DEBUG:
                errfunc('removing ' + p + ' (will re-add)')
            removed[h] = parsed[h]
        to_add.append(p)

    to_add.sort()
    for p in to_add:
        new_file = new_files[p]
        v, h = new_file
        if new_parsed.has_key(h):
            if not blocked.has_key(p) or files[p][0] != v:
                errfunc('**warning** ' + p + ' is a duplicate torrent for ' + new_parsed[h]['path'])
            new_blocked[p] = 1
            continue
        if DEBUG:
            errfunc('adding ' + p)
        try:
            tdef = TorrentDef.load(p)
            h = tdef.get_infohash()
            d = tdef.get_metainfo()
            new_file[1] = h
            if new_parsed.has_key(h):
                errfunc('**warning** ' + p + ' is a duplicate torrent for ' + new_parsed[h]['path'])
                new_blocked[p] = 1
                continue
            a = {}
            a['path'] = p
            f = os.path.basename(p)
            a['file'] = f
            a['type'] = torrent_type[p]
            if tdef.get_url_compat():
                a['url'] = tdef.get_url()
            i = d['info']
            l = 0
            nf = 0
            if i.has_key('length'):
                l = i.get('length', 0)
                nf = 1
            elif i.has_key('files'):
                for li in i['files']:
                    nf += 1
                    if li.has_key('length'):
                        l += li['length']

            a['numfiles'] = nf
            a['length'] = l
            a['name'] = i.get('name', f)

            def setkey(k, d = d, a = a):
                if d.has_key(k):
                    a[k] = d[k]

            setkey('failure reason')
            setkey('warning message')
            setkey('announce-list')
            if tdef.get_urllist() is not None:
                httpseedhashes = []
                for url in tdef.get_urllist():
                    urlhash = sha(url).digest()
                    httpseedhashes.append(urlhash)

                a['url-hash-list'] = httpseedhashes
            if return_metainfo:
                a['metainfo'] = d
        except:
            print_exc()
            errfunc('**warning** ' + p + ' has errors')
            new_blocked[p] = 1
            continue

        if DEBUG:
            errfunc('... successful')
        new_parsed[h] = a
        added[h] = a

    for p, v in files.items():
        if not new_files.has_key(p) and not blocked.has_key(p):
            if DEBUG:
                errfunc('removing ' + p)
            removed[v[1]] = parsed[v[1]]

    if DEBUG:
        errfunc('done checking')
    return (new_parsed,
     new_files,
     new_blocked,
     added,
     removed)
