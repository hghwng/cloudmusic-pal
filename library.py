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
        local_playlists = self._db['playlists']

        remote_pids = set()
        remote_playlists = self._api.get_user_playlist(uid)['playlist']
        for remote_meta in remote_playlists:
            pid = remote_meta['id']
            remote_pids.add(pid)

            should_fetch = False
            if pid not in local_playlists:
                Library.L.info('Syncing new playlist %s(%d)', remote_meta['name'], pid)
                should_fetch = True
            elif local_playlists[pid]['raw']['updateTime'] != remote_meta['updateTime']:
                Library.L.info('Syncing out-of-date playlist %s(%d)', remote_meta['name'], pid)
                should_fetch = True

            if should_fetch:
                detail = self._api.get_playlist_detail(pid)['playlist']
                playlist = {'name': remote_meta['name'], 'raw': detail}
                playlist['tids'] = [t['id'] for t in detail['trackIds']]
                local_playlists[pid] = playlist

        for pid in set(local_playlists.keys()) - remote_pids:
            Library.L.info('Removing redundant playlist %s(%d)',
                           local_playlists[pid]['name'], pid)
            del local_playlists[pid]


    def scan_tracks(self):
        # Scan current local tracks
        scan = dict()
        for filename in os.listdir(self._TRACK_DIR):
            tid, ext = tuple(os.path.splitext(filename))
            ext = ext[1:]
            size = os.path.getsize(self._TRACK_DIR + filename)
            try:
                tid = int(tid)
            except ValueError:
                self.L.error("Invalid local track name: %s", tid)
                continue
            scan[tid] = dict(size=size, ext=ext)

        # Maintain local tracks db
        # Remove files not in file system
        changed_tracks = set()
        local_tracks = self._db['local_tracks']
        for tid, track in local_tracks.items():
            if tid in scan:
                if track['size'] != scan[tid]['size']:
                    self.L.debug("Changed local track: %d (%d -> %d)",
                                tid, track['size'], scan[tid]['size'])
                    changed_tracks.add(tid)
            else:
                self.L.debug("Deleted local track: %d", tid)
                changed_tracks.add(tid)
        for tid in changed_tracks:
            del local_tracks[tid]

        # Add files not in db
        for tid, info in scan.items():
            if tid not in local_tracks:
                path = self._TRACK_DIR + str(tid) + '.' + info['ext']
                local_tracks[tid] = info
                local_tracks[tid]['bitrate'] = taglib.File(path).bitrate * 1000
                self.L.debug("Manually added local track: %d, bitrate = %d",
                             tid, local_tracks[tid]['bitrate'])

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
        local_tracks = self._db['local_tracks']
        for tid in playlist['tids']:
            if tid in local_tracks:
                path = self._TRACK_DIR + str(tid) + '.' + local_tracks[tid]['ext']
                m3u_file.write(path + '\n')
            else:
                Library.L.warning('Missing file for track %d', tid)


def main():
    from sys import argv
    logging.getLogger().setLevel(logging.INFO)
    logging.info('Logging started')

    api = NeteaseAPI()
    api.load_cookie('cookies')
    lib = Library(argv[1], api)
    playlists = lib._db['playlists']

    command = argv[2]
    if command == 'sync':
        lib.sync(int(argv[3]))
    elif command == 'scan':
        lib.scan_tracks()
    elif command == 'pl_show':
        for pid, playlist in playlists.items():
            print(pid, playlist['name'])
    elif command == 'pl_down':
        pids = [int(i) for i in argv[3:]]
        if not pids:
            pids = playlists.keys()
        for pid in pids:
            playlist = playlists[pid]
            print(pid, playlist['name'])
            lib.download_tracks(playlist['tids'],
                                Library.DOWNLOAD_STRATEGY_MISSING,
                                Library.DOWNLOAD_SOURCE_DOWNLOAD)
            # Save in case the download progress crashes
            lib.save()
    elif command == 'm3u':
        for pid, playlist in playlists.items():
            print(pid, playlist['name'])
            lib.create_playlist(pid)
    else:
        print('Usage: DB_PATH COMMAND')
        print('COMMAND: sync UID | pl_show | PL_DOWN_COMMAND | m3u')
        print('PL_DOWN_COMMAND: pl_down | pl_down PIDS')
        print('PIDS: PID PIDS | PID')
    lib.save()


if __name__ == '__main__':
    main()
