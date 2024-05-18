import os
import pickle
import logging
import taglib
import requests
from api import NeteaseAPI


# Copyright: Fred Cirera
# URL: http://stackoverflow.com/questions/1094841/reusable-library-to-get-human-readable-version-of-file-size
def _size_format(num, suffix='B'):
    for unit in ('', 'Ki', 'Mi', 'Gi', 'Ti', 'Pi', 'Ei', 'Zi'):
        if abs(num) < 1024.0:
            return "%3.1f%s%s" % (num, unit, suffix)
        num /= 1024.0
    return "%.1f%s%s" % (num, 'Yi', suffix)


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
        cdn = '220.243.197.54'
        url = url.replace('m10.music.126.net', cdn + '/m10.music.126.net')
        # print(url)
        r = requests.get(url, stream=True)
        with open(path, 'wb') as f:
            for chunk in r.iter_content(chunk_size=1024*1024):
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
            else:
                local_playlist = local_playlists[pid]
                if local_playlist['raw']['updateTime'] != remote_meta['updateTime']:
                    Library.L.info('Syncing out-of-date playlist %s(%d)', remote_meta['name'], pid)
                    should_fetch = True
                elif local_playlist['name'] != remote_meta['name']:
                    Library.L.info('Renaming playlist %s -> %s (%d)',
                                   remote_meta['name'], local_playlist['name'], pid)
                    local_playlist['name'] = remote_meta['name']

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
                    self.L.debug("Changed local track: %d (%s → %s)",
                                 tid, _size_format(track['size']),
                                 _size_format(scan[tid]['size']))
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
        remote_tracks = set()
        for playlist in self._db['playlists'].values():
            remote_tracks.update(playlist['tids'])

        redundant_tracks = set(local_tracks.keys()).difference(remote_tracks)
        for tid in redundant_tracks:
            self.L.info("Deleted remote track: %d", tid)
        self._save_tids('!redundant', redundant_tracks)

        return changed_tracks, redundant_tracks


    def _download_track(self, tid, file_info, meta):
        # Parse info
        size, url, ext = file_info['size'], file_info['url'], file_info['type']
        if url is None:
            Library.L.warning('Download unavailable: %s: %s', tid, meta['name'])
            return False
        Library.L.info('Downloading %s: %s, %s', tid, meta['name'], _size_format(size))
        tmp_path = self._TMP_DIR + str(tid) + '.' + ext
        Library.download_file(url, tmp_path)

        # Check size and hash
        CHECK_SIZE_TOO_SMALL = 0
        CHECK_SIZE_MISS      = 1
        CHECK_SIZE_ALMOST    = 2
        CHECK_HASH_MISS      = 3
        CHECK_HASH_MATCH     = 4

        check_status = CHECK_SIZE_TOO_SMALL
        import hashlib
        file_size = os.path.getsize(tmp_path)
        ratio = file_size / file_info['size']
        if ratio < 0.9:
            Library.L.error('Size too small: %d: %s (%.0f%%: %s -> %s)',
                            tid, meta['name'],
                            ratio * 100, _size_format(file_size),
                            _size_format(file_info['size']))
            check_status = CHECK_SIZE_TOO_SMALL
        elif file_size != file_info['size']:
            Library.L.warning('Size mismatch: %d: %s', tid, meta['name'])
            check_status = CHECK_SIZE_ALMOST
        elif file_info['md5'] != hashlib.md5(open(tmp_path, 'rb').read()).hexdigest():
            Library.L.warning('Hash mismatch: %d: %s', tid, meta['name'])
            check_status = CHECK_HASH_MISS
        else:
            check_status = CHECK_HASH_MATCH

        if check_status == CHECK_SIZE_TOO_SMALL:
            # Fail only when size is too small
            os.remove(tmp_path)
            return False

        # Tag
        Library.L.debug('Tagging %s', tid)
        Library.tag(tmp_path, meta)

        # Remove old file
        if tid in self._db['local_tracks']:
            prev_path = self._TRACK_DIR + str(tid) + '.' + self._db['local_tracks'][tid]['ext']
            try:
                os.remove(prev_path)
            except FileNotFoundError:
                pass
        new_path = self._TRACK_DIR + str(tid) + '.' + ext
        os.rename(tmp_path, new_path)

        # Add to DB
        local_track = dict(size=os.path.getsize(new_path), ext=ext, bitrate=file_info['br'])
        self._db['local_tracks'][tid] = local_track
        return True

    def _get_download_info(self, tids, strategy, source):
        local_tracks = self._db['local_tracks']
        # Skip tracks already downloaded: don't fetch the song's detail
        if strategy == Library.DOWNLOAD_STRATEGY_MISSING:
            tids = set(tids) - set(local_tracks.keys())
        else:
            tids = set(tids)  # don't change the caller's data
        if not tids:
            return dict(), list(), list()

        details_api = self._api.get_track_detail(list(tids))
        details = {t['id']: dict(meta=t) for t in details_api['songs']}
        for priv in details_api['privileges']:
            if priv['id'] not in details:
                Library.L.warning('Unknown track %d, excluding from download list', priv['id'])
                tids.discard(priv['id'])
            if priv.get('st', 0) < 0:
                Library.L.warning('Disabled track %d (status = %d), excluding from download list', priv['id'], priv['st'])
                tids.discard(priv['id'])
            else:
                details[priv['id']]['priv'] = priv

        # Fix invalid tracks with no metadata. See 16611839
        for tid in tids.difference(details.keys()):
            Library.L.warning('Unknown track %d, excluding from download list', tid)
            tids.discard(tid)

        list_download = list()
        list_play = list()
        for tid in tids:
            bitrate_local = 0
            if tid in local_tracks:
                if local_tracks[tid]['ext'] != 'mp3':
                    bitrate_local = 999000
                else:
                    bitrate_local = local_tracks[tid].get('bitrate')
                    if bitrate_local is None:
                        Library.L.warning("unknown bitrate for track %d", tid)
                        bitrate_local = 0

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
        details, list_play, list_download = self._get_download_info(tids, strategy, source)

        num_total = len(list_play) + len(list_download)
        num_processed = 1

        while list_play:
            # Download play urls in batch
            for file_info in self._api.get_player_url(list_play)['data']:
                tid = file_info['id']
                Library.L.info("Download progress: %d/%d", num_processed, num_total)
                if self._download_track(tid, file_info, details[tid]['meta']):
                    num_processed += 1
                    list_play.remove(tid)
                else:
                    Library.L.warning("Retry: fetch the player URL again in case of timeout")
                    break

        # Download API doesn't support batch mode, download one by one
        for tid, bitrate in list_download:
            file_info = self._api.get_download_url(tid, bitrate)['data']
            Library.L.info("Download progress: %d/%d", num_processed, num_total)
            self._download_track(tid, file_info, details[tid]['meta'])
            num_processed += 1

    def pull_radio(self, num_pull=3, source=None):
        if source is None:
            source = Library.DOWNLOAD_SOURCE_PLAY

        tracks = list()
        Library.L.info('Radio: started fetching')
        while len(tracks) < num_pull:
            tracks.extend(self._api.get_radio()['data'])
            Library.L.debug('Radio: fetched %d/%d', len(tracks), num_pull)
        tracks = tracks[:num_pull + 1]
        tids = [track['id'] for track in tracks]

        self.download_tracks(tids, Library.DOWNLOAD_STRATEGY_MISSING, source)
        self._save_tids('Radio', tids)

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

    def _save_tids(self, title: str, tids: list):
        m3u_path = self._PLAYLIST_DIR + title.replace('/', '／') + '.m3u'
        m3u_file = open(m3u_path, 'w')
        local_tracks = self._db['local_tracks']
        for tid in tids:
            if tid in local_tracks:
                path = str(tid) + '.' + local_tracks[tid]['ext']
                m3u_file.write(path + '\n')
            else:
                Library.L.warning('Missing file for track %d', tid)

    def save_playlist(self, pid):
        playlist = self._db['playlists'][pid]
        self._save_tids(playlist['name'], playlist['tids'])


class LibraryCli(object):
    def __init__(self, db_path, cookies_path="cookies"):
        self._cookies_name = cookies_path
        self._api = NeteaseAPI()
        try:
            self._api.load_cookie(self._cookies_name)
        except FileNotFoundError:
            pass
        self._db_path = db_path
        self._lib = Library(db_path, self._api)
        self._playlists = self._lib._db['playlists']
        self._local_tracks = self._lib._db['local_tracks']

    def __del__(self):
        self._api.dump_cookie(self._cookies_name)
        self._lib.save()

    def sync(self, uid):
        self._lib.sync(uid)

    def cleanup(self):
        import os
        _, redundant = self._lib.scan_tracks()
        for tid in redundant:
            os.remove(self._db_path + '/tracks/' + str(tid) + '.' +
                      self._local_tracks[tid]['ext'])

    def scan(self):
        self._lib.scan_tracks()

    def radio_pull(self, num_pull=3, source=None):
        self._lib.pull_radio(num_pull, source)

    def pl_show(self, *pids):
        if pids:
            for pid in pids:
                for tid in self._playlists[pid]['tids']:
                    print(tid)
        else:
            for pid, playlist in self._playlists.items():
                print(pid, playlist['name'])

    def pl_down(self, *pids):
        pids = list(pids)
        if not pids:
            pids = self._playlists.keys()
        for pid in pids:
            playlist = self._playlists[pid]
            tracks = playlist['tids']

            start = 0
            while start < len(tracks):
                next_start = start + 200
                print(pid, playlist['name'], f'{start} / {len(tracks)}')
                self._lib.download_tracks(tracks[start:next_start],
                                          Library.DOWNLOAD_STRATEGY_UPGRADE,
                                          Library.DOWNLOAD_SOURCE_PLAY)
                # Save in case the download progress crashes
                self._lib.save()
                start = next_start


    def m3u(self):
        for pid, playlist in self._playlists.items():
            print(pid, playlist['name'])
            self._lib.save_playlist(pid)


def main():
    try:
        import fire
        logging.getLogger().setLevel(logging.INFO)
        logging.info('Logging started')

        fire.Fire(LibraryCli)
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
