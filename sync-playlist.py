
import os
from getpass import getpass

from gmusicapi.clients import Webclient
from mutagen.easyid3 import EasyID3
import requests


def find_dict(lst, criteria):
  for item in lst:
    for k in criteria:
      if k in item and item[k] == criteria[k]:
        return item
  return None

def escape_path(path):
  return path

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
      'year': 'date'
    }.iteritems():
      if m_k in track:
        value = str(track[m_k])
        if m_k=='album_image':
          value = self._get_album_image(value)
        if m_k=='track':
          value = '{0}/{1}'.format(value, track['totalTracks'])
        if m_k=='disc':
          value = '{0}/{1}'.format(value, track['totalDiscs'])
        mp3[g_k] = value
    mp3.save(file_name)
  do_copy()


# get playlist, compare with local, download new tracks, optionally delete local files not in the playlist
# structure: /<Album Artist>/<Album>/<Track #> <Track Name>.mp3

class PlaylistSync:

  def __init__(self, root, playlist_name):
    self.root = root
    self.playlist_name = playlist_name

  def login(self, username, password):
    self.wc = Webclient()
    if not self.wc.login(username, password):
      raise Exception('login failed')

  def track_file_name(self, track):
    albumartist = track['albumArtist']
    if not albumartist:
      albumartist = 'Various'
    file_name = escape_path(u'{disc}-{track:02d} {name}.mp3'.format(**track))
    return os.path.join(self.root, escape_path(albumartist), escape_path(track['album']), file_name)

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
          yield file_name, track

  def get_playlist_tracks(self):
    # return (metadata, local_file_name) for each track in playlist
    all_playlists = self.wc.get_all_playlist_ids()
    user_playlists = all_playlists['user']
    try:
      playlist_id, = user_playlists[self.playlist_name]
    except KeyError:
      raise Exception('playlist "{0}" not found'.format(self.playlist_name))
    print playlist_id
    for track in self.wc.get_playlist_songs(playlist_id):
      yield self.track_file_name(track), track

  def add_track(self, track, file_name):
    # download track from gmusic, write to file_name
    if not os.path.exists(os.path.dirname(file_name)):
      os.makedirs(os.path.dirname(file_name))
    url = self.wc.get_stream_url(track['id'])
    r = requests.get(url)
    with open(file_name, 'wb') as f:
      f.write(r.content)
    _copy_track_metadata(file_name, track)
    
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


  def sync(self, confirm=False, remove=True):
    local = dict(self.get_local_tracks())
    playlist = dict(self.get_playlist_tracks())

    to_add = []
    to_remove = []

    for file_name, track in playlist.iteritems():
      if file_name not in local:
        to_add.append((track, file_name))

    if remove:
      for file_name, track in local.iteritems():
        if file_name not in playlist:
          to_remove.append((track, file_name))

    if confirm and (to_add or to_remove):
      if remove:
        print 'Deleting tracks:'
        for track, file_name in to_remove:
          print '  ' + file_name
      if to_add:
        print 'Adding tracks:'
        for track, file_name in to_add:
          print '  ' + file_name
      raw_input('Proceed?')
    else:
      print 'Nothing to do.'

    for track, file_name in to_remove:
      print 'removing track ' + file_name
      self.remove_track(file_name)
    for track, file_name in to_add:
      print 'adding track ' + track['name']
      self.add_track(track, file_name)

if __name__ == '__main__':
  import argparse
  parser = argparse.ArgumentParser()
  parser.add_argument('playlist', help='name of the playlist')
  parser.add_argument('destination', help="where to sync")
  parser.add_argument('--no-confirm', '-f', action='store_true', help="don't confirm")
  parser.add_argument('--no-remove', '-s', action='store_true', help="dont't delete files")
  args = parser.parse_args()
  
  ps = PlaylistSync(args.destination, args.playlist)
  ps.login(username, password)
  ps.sync(confirm=not args.no_confirm, remove=not args.no_remove)

  print 'Done.'

