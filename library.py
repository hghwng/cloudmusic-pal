import os
import json
import pickle
import logging
import taglib
import requests
from api import NeteaseAPI


class Library:
    L = logging.getLogger('Library')

    def __init__(self, lib_path, api: NeteaseAPI):
        Library.L.info('Initialization: lib_path = %s', lib_path)
        self._path = os.path.abspath(lib_path)
        self._TRACK_DIR = self._path + '/tracks/'
        self._TMP_DIR = self._path + '/tmp/'
        self._PLAYLIST_DIR = self._path + '/playlists/'
        self._DB_PATH = self._path + '/db.pickle'
        for path in (self._TRACK_DIR, self._TMP_DIR, self._PLAYLIST_DIR):
            if not os.path.exists(path):
                os.mkdir(path)

        self._api = api
        if os.path.isfile(self._DB_PATH):
            self._db = pickle.load(open(self._DB_PATH, 'rb'))
        else:
            Library.L.info('Creating empty database')
            self._db = {'playlists': {}, 'local_tracks': {}}

    def save(self):
        pickle.dump(self._db, open(self._DB_PATH, 'wb'))

    @staticmethod
    def download_file(url, path):
        r = requests.get(url, stream=True)
        with open(path, 'wb') as f:
            for chunk in r.iter_content(chunk_size=1024):
                if chunk: # filter out keep-alive new chunks
                    f.write(chunk)

    def sync(self, uid):
        Library.L.info('Syncing for user %d', uid)
        result = dict()
        self._db['playlists'] = result

        api_playlists = self._api.get_user_playlist(uid)['playlist']
        for meta in api_playlists:
            # Fetch the playlist
            pid = int(meta['id'])
            Library.L.info('Syncing playlist %s(%d)', meta['name'], pid)
            detail = self._api.get_playlist_detail(pid)['playlist']
            tids = [t['id'] for t in detail['trackIds']]
            result[pid] = dict()
            result[pid]['name'] = meta['name']
            result[pid]['tids'] = tids
            result[pid]['raw'] = detail

    def scan_tracks(self):
        # Scan current local tracks
        scan = dict()
        for filename in os.listdir(self._TRACK_DIR):
            tid, ext = tuple(os.path.splitext(filename))
            size = os.path.getsize(self._TRACK_DIR + filename)
            try:
                tid = int(tid)
            except ValueError:
                self.L.error("Invalid local track name: %s", tid)
                continue
            scan[tid] = dict(size=size, ext=ext)

        # Maintain local tracks db
        # Remove files not in file system
        deleted_tracks = set()
        local_tracks = self._db['local_tracks']
        for tid, track in local_tracks.items():
            if tid not in scan:
                deleted_tracks.add(tid)
            else:
                track['size'] = scan[tid]
        for tid in deleted_tracks:
            self.L.info("Deleted local track: %d", tid)
            del local_tracks[tid]

        # Add files not in db
        for tid, size in scan.items():
            if tid not in local_tracks:
                self.L.info("Manually added local track: %d", tid)
                local_tracks[tid] = dict(size=size)

        # Show redundant files
        remote_tids = list()
        for playlist in self._db['playlists'].values():
            remote_tids.extend(playlist['tids'])

        for tid, size in local_tracks.items():
            if tid not in remote_tids:
                self.L.warning("Deleted remote track: %d", tid)

    def download_tracks(self, tids, skipDownloaded=True):
        if skipDownloaded:
            tids = list(set(tids) - set(self._db['local_tracks'].keys()))
        urls = self._api.get_player_url(tids)['data']
        urls = {t['id']: t for t in urls}
        details = self._api.get_track_detail(tids)['songs']
        details = {t['id']: t for t in details}
        print(tids)

        for idx, tid in enumerate(tids):
            detail = details[tid]
            size, url, ext = urls[tid]['size'], urls[tid]['url'], urls[tid]['type']
            if url is None:
                Library.L.critical('Download failed: %s(%s)', detail['name'], tid)
                continue
            Library.L.info('Downloading %d/%d, %s: %s, size = %d', idx + 1, len(tids),
                           tid, detail['name'], size)
            tmp_path = self._TMP_DIR + str(tid) + '.' + ext
            Library.download_file(url, tmp_path)
            Library.L.debug('Tagging %s', tid)
            Library.tag(tmp_path, detail)

            path = self._TRACK_DIR + str(tid) + '.' + ext
            os.rename(tmp_path, path)
            local_track = dict(size=os.path.getsize(path), ext=ext)
            self._db['local_tracks'][tid] = local_track

    @staticmethod
    def tag(path, detail):
        tagfile = taglib.File(path)
        if 'COMMENT' in tagfile.tags:
            del tagfile.tags['COMMENT']

        tagfile.tags['TITLE'] = detail['name']
        tagfile.tags['ALBUM'] = detail['al']['name']
        tagfile.tags['ARTIST'] = [t['name'] for t in detail['ar']]
        tagfile.tags['TRACKNUMBER'] = str(detail['no'])
        tagfile.save()

    def create_playlist(self, pid):
        playlist = self._db['playlists'][pid]
        m3u_path = self._PLAYLIST_DIR + playlist['name'] + '.m3u'
        m3u_file = open(m3u_path, 'w')
        for tid in playlist['tids']:
            m3u_file.write(self._PLAYLIST_DIR + str(tid))


def main():
    pass


if __name__ == '__main__':
    main()
