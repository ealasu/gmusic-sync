#!/usr/bin/env python

import os
import shutil
from getpass import getpass
import keyring
from ConfigParser import SafeConfigParser
from pprint import pprint
import unicodedata

from gmusicapi.clients import Webclient, Musicmanager, Mobileclient
from mutagen.easyid3 import EasyID3
import requests


def find_dict(lst, criteria):
    for item in lst:
        for k in criteria:
            if k in item and item[k] == criteria[k]:
                return item
    return None

def escape_path(fname):
    for c in ':/\\?"\'<>|*': 
        fname = fname.replace(c, '_')
    return fname

def setup_id3_keys():
    def registerTimestamp(key, frameid):
        def getter(id3, key):
            return list(ts.text for ts in id3[frameid].text)
        def setter(id3, key, value):
            if isinstance(value, str) or isinstance(value,unicode): value = [value]
            try:
                frame = id3[frameid]
            except KeyError:
                id3.add(mutagen.id3.Frames[frameid](encoding=3, text=list(ID3TimeStamp(ts) for ts in value)))
            else:
                frame.encoding = 3
                frame.text = list(ID3TimeStamp(ts) for ts in value)
        def deleter(id3, key):
            del(id3[frameid])
        EasyID3.RegisterKey(key, getter, setter, deleter)
    
    registerTimestamp("year", "TDRC")
    registerTimestamp("releasedate", "TDRL")

setup_id3_keys()

def _copy_track_metadata(file_name, track):
    def do_copy():
        mp3 = EasyID3()
        for m_k, g_k in {
            'name': 'title',
            'album': 'album',
            'genre': 'genre',
            'artist': 'artist',
            'albumArtist': 'performer',
            'track': 'tracknumber',
            'disc': 'discnumber',
            'year': 'date',
        }.iteritems():
            if m_k in track:
                value = unicode(track[m_k])
                if m_k=='album_image':
                    value = self._get_album_image(value)
                if m_k=='track':
                    value = '{0}/{1}'.format(value, track.get('totalTracks', 1))
                if m_k=='disc':
                    value = '{0}/{1}'.format(value, track.get('totalDiscs', 1))
                mp3[g_k] = value
        mp3.save(file_name)
    do_copy()


# get playlist, compare with local, download new tracks, optionally delete local files not in the playlist
# structure: /<Album Artist>/<Album>/<Track #> <Track Name>.mp3

class PlaylistSync:

    def __init__(self, root, playlist_name):
        self.root = root
        self.playlist_name = playlist_name
 
    def _login_mc(self):
        APP_NAME = 'gmusic-sync-playlist'
        CONFIG_FILE = 'auth.cfg'

        config = SafeConfigParser({
            'username': '',
            'device_id': ''
        })
        config.read(CONFIG_FILE)
        if not config.has_section('auth'):
            config.add_section('auth')
    
        username = config.get('auth','username')
        password = None
        if username != '':
            password = keyring.get_password(APP_NAME, username)
    
        if password == None or not self.mc.login(username, password):
            while 1:
                username = raw_input("Username: ")
                password = getpass("Password: ")
                if self.mc.login(username, password):
                    break
                else:
                    print "Sign-on failed."
    
            config.set('auth', 'username', username)
            with open(CONFIG_FILE, 'wb') as f:
                config.write(f)
    
            keyring.set_password(APP_NAME, username, password)

        device_id = config.get('auth', 'device_id')

        if device_id == '':
            wc = Webclient()
            success = wc.login(username, password)
            if not success:
                raise Exception('could not log in via Webclient')
            devices = wc.get_registered_devices()
            mobile_devices = [d for d in devices if d[u'type'] in (u'PHONE', u'IOS')]
            if len(mobile_devices) < 1:
                raise Exception('could not find any registered mobile devices')
            device_id = mobile_devices[0][u'id']
            if device_id.startswith(u'0x'):
                device_id = device_id[2:]
            
            config.set('auth', 'device_id', device_id)
            with open(CONFIG_FILE, 'wb') as f:
                config.write(f)

        print('Device ID: {}'.format(device_id))
        self.mc.device_id = device_id


    def login(self):
        self.mc = Mobileclient()
        self._login_mc()
        self.mm = Musicmanager()
        #self.mm.perform_oauth()
        self.mm.login()

    def track_file_name(self, track):
        if 'albumArtist' in track:
            albumartist = track['albumArtist']
        else:
            albumartist = 'Various'
        if not albumartist:
            albumartist = 'Various'
        file_name = escape_path(u'{trackNumber:02d} {title}.mp3'.format(**track))
        if track.get('totalDiscCount', 1) > 1:
            file_name = u'{discNumber}-'.format(**track) + file_name
        return unicodedata.normalize('NFD', os.path.join(self.root, escape_path(albumartist), escape_path(track['album']), file_name))

    def get_local_tracks(self):
        # return (metadata, file_name) of all files in root
        tracks = []
        for root, dirs, files in os.walk(self.root):
            for f in files:
                if os.path.splitext(f)[1].lower() == '.mp3':
                    file_name = os.path.join(root, f)
                    #id3 = EasyID3(file_name)
                    track = {}
                    #track = {
                    #  'name': id3['title'],
                    #  'album': id3['album'],
                    #  'track': id3['tracknumber'],
                    #  'disc': id3['discnumber']
                    #}
                    yield unicodedata.normalize('NFD', file_name.decode('utf-8')), track

    def get_playlist_tracks(self):
        # return (metadata, local_file_name) for each track in playlist
        all_playlists = self.mc.get_all_playlists()
        try:
            playlist_id = next(p for p in all_playlists if p['name'] == self.playlist_name)['id']
        except StopIteration:
            raise Exception('playlist "{0}" not found'.format(self.playlist_name))
        all_songs = self.mc.get_all_songs()
        pprint(all_songs[0])
        for p in self.mc.get_all_user_playlist_contents():
            if p['name'] == self.playlist_name:
                for track in p['tracks']:
                    song = next(s for s in all_songs if s['id'] == track['trackId'])
                    print(u'{album} - {title}'.format(**song))
                    #pprint(song)
                    yield self.track_file_name(song), song

    def add_track(self, track, file_name):
        # download track from gmusic, write to file_name
        if not os.path.exists(os.path.dirname(file_name)):
            os.makedirs(os.path.dirname(file_name))
        if track['kind'] != u'sj#track':
            url = self.mc.get_stream_url(track['id'], self.mc.device_id)
            r = requests.get(url)
            with open(file_name, 'wb') as f:
                f.write(r.content)
            _copy_track_metadata(file_name, track)
        else:
            fn, audio = self.mm.download_song(track['id'])
            with open(file_name, 'wb') as f:
                f.write(audio)
        
    def remove_track(self, file_name):
        """Removes the track and walks up the tree deleting empty folders
        """
        os.remove(file_name)
        rel = os.path.relpath(file_name, self.root)
        dirs = os.path.split(rel)[0:-1]
        for i in xrange(1, len(dirs) + 1):
            dir_path = os.path.join(self.root, *dirs[0:i])
            if not os.listdir(dir_path):
                os.unlink(dir_path)


    def sync(self, confirm=True, remove=False):
        print 'Searching for local tracks ...'
        local = dict(self.get_local_tracks())
        print 'Getting playlist ...'
        playlist = dict(self.get_playlist_tracks())

        to_add = []
        to_remove = []
        to_rename = []

        for file_name, track in sorted(playlist.iteritems()):
            if file_name not in local and file_name.encode('ascii', 'replace').replace('?','_') not in local:
                to_add.append((track, file_name))
            elif file_name not in local and file_name.encode('ascii', 'replace').replace('?','_') in local:
                to_rename.append((file_name.encode('ascii', 'replace').replace('?','_'), file_name))

        if remove:
            for file_name, track in sorted(local.iteritems()):
                if file_name not in playlist:
                    to_remove.append((track, file_name))

        if to_remove:
            print 'Deleting tracks:'
            for track, file_name in to_remove:
                print '  ' + file_name
            print ''
        if to_add:
            print 'Adding tracks:'
            for track, file_name in to_add:
                print '  ' + file_name
            print ''
        if to_rename:
            print 'Renaming tracks:'
            for src, dst in to_rename:
                print '  {0} to {1}'.format(src, dst)
            print ''
        if not (to_add or to_remove):
            print 'Nothing to do.'
            print ''

        if confirm:
            raw_input('Press enter to proceed')

        for src, dst in to_rename:
            if not os.path.exists(os.path.dirname(dst)):
                os.makedirs(os.path.dirname(dst))
            shutil.move(src, dst)
        for track, file_name in to_remove:
            print 'removing track ' + file_name
            self.remove_track(file_name)
        for track, file_name in to_add:
            print u'adding track: {album} / \n  {title}'.format(**track).encode('utf-8', 'replace')
            self.add_track(track, file_name)

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('playlist', help='name of the playlist')
    parser.add_argument('destination', help="where to sync")
    parser.add_argument('--no-confirm', '-f', action='store_true', help="don't confirm")
    parser.add_argument('--remove', '-x', action='store_true', help="delete local tracks not in playlist")
    args = parser.parse_args()
    
    ps = PlaylistSync(args.destination, args.playlist)
    print 'Logging in ...'
    ps.login()
    ps.sync(confirm=not args.no_confirm, remove=args.remove)

    print 'Done.'

