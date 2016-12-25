import os
import pickle
import logging
import taglib
import requests
from api import NeteaseAPI


class Library:
    L = logging.getLogger('Library')

    DOWNLOAD_STRATEGY_MISSING = 0
    DOWNLOAD_STRATEGY_UPGRADE = 1
    DOWNLOAD_SOURCE_PLAY = 0
    DOWNLOAD_SOURCE_DOWNLOAD = 1

    def __init__(self, lib_path, api: NeteaseAPI):
        Library.L.debug('Initialization: lib_path = %s', lib_path)
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
            Library.L.debug('Creating empty database')
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
            Library.L.debug('Syncing playlist %s(%d)', meta['name'], pid)
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
                self.L.info("Deleted remote track: %d", tid)

    def _download_track(self, tid, file_info, meta):
        size, url, ext = file_info['size'], file_info['url'], file_info['type']
        if url is None:
            Library.L.warning('Download failed: %s: %s', tid, meta['name'])
            return False
        Library.L.info('Downloading %s: %s, size = %d', tid, meta['name'], size)

        tmp_path = self._TMP_DIR + str(tid) + '.' + ext
        Library.download_file(url, tmp_path)
        Library.L.debug('Tagging %s', tid)
        Library.tag(tmp_path, meta)

        if tid in self._db['local_tracks']:
            prev_path = self._TRACK_DIR + str(tid) + '.' + self._db['local_tracks'][tid]['ext']
            os.remove(prev_path)
        new_path = self._TRACK_DIR + str(tid) + '.' + ext
        os.rename(tmp_path, new_path)
        local_track = dict(size=os.path.getsize(new_path), ext=ext, bitrate=file_info['br'])
        self._db['local_tracks'][tid] = local_track
        return True

    def _get_download_info(self, tids, strategy, source):
        local_tracks = self._db['local_tracks']
        # Skip tracks already downloaded: don't fetch the song's detail
        if strategy == Library.DOWNLOAD_STRATEGY_MISSING:
            tids = list(set(tids) - set(local_tracks.keys()))
        details_api = self._api.get_track_detail(tids)
        details = {t['id']: dict(meta=t) for t in details_api['songs']}
        for priv in details_api['privileges']:
            details[priv['id']]['priv'] = priv

        list_download = list()
        list_play = list()
        for tid in tids:
            bitrate_local = 0
            if tid in local_tracks:
                if local_tracks[tid]['ext'] != 'mp3':
                    bitrate_local = 999000
                else:
                    bitrate_local = local_tracks[tid]['bitrate']

            meta, priv = details[tid]['meta'], details[tid]['priv']
            method = 0
            bitrate_fetch = bitrate_local
            # Check play bitrate first; prefer the play API when both APIs show the same bitrate
            if bitrate_fetch < priv['pl']:
                method = 1
                bitrate_fetch = priv['pl']
            # Check download bitrate after
            if source == Library.DOWNLOAD_SOURCE_DOWNLOAD and \
               bitrate_fetch < priv['dl']:
                method = 2
                bitrate_fetch = priv['dl']

            # Check for possible higher bitrates
            if bitrate_fetch < priv['maxbr']:
                Library.L.info("Better quality for %d: %s, local = %d, fetch = %d, max = %d",
                               tid, meta['name'], bitrate_local, bitrate_fetch, priv['maxbr'])
            if method != 0:
                Library.L.info("Upgrade(%s) quality for %d: %s, local = %d, fetch = %d, max = %d",
                               {1: "play", 2: "download"}[method],
                               tid, meta['name'], bitrate_local, bitrate_fetch, priv['maxbr'])
            if method == 1:
                list_play.append(tid)
            elif method == 2:
                list_download.append((tid, bitrate_fetch))
        return details, list_play, list_download

    def download_tracks(self, tids, strategy=None, source=None):
        strategy = Library.DOWNLOAD_STRATEGY_MISSING if strategy is None else strategy
        source = Library.DOWNLOAD_SOURCE_PLAY if strategy is None else source
        if not tids:
            return
        details, list_play, list_download = self._get_download_info(tids, strategy, source)

        num_total = len(list_play) + len(list_download)
        if num_total == 0:
            return

        num_processed = 1
        # Download play urls in batch
        for file_info in self._api.get_player_url(list_play)['data']:
            tid = file_info['id']
            Library.L.info("Download progress: %d/%d", num_processed, num_total)
            self._download_track(tid, file_info, details[tid]['meta'])
            num_processed += 1

        # Download API doesn't support batch mode, download one by one
        for tid, bitrate in list_download:
            file_info = self._api.get_download_url(tid, bitrate)['data']
            Library.L.info("Download progress: %d/%d", num_processed, num_total)
            self._download_track(tid, file_info, details[tid]['meta'])
            num_processed += 1

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
