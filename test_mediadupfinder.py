import sys
import os
import re
from functools import lru_cache
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mediadupfinder as mdf


class TestNormalizeName:
    def test_basic_stem(self):
        assert mdf.normalize_name("MyVideo.mp4") == "myvideo"

    def test_copy_suffix_1(self):
        assert mdf.normalize_name("movie (1).mkv") == "movie"

    def test_copy_suffix_2(self):
        assert mdf.normalize_name("movie - copy.mkv") == "movie"

    def test_copy_suffix_chinese(self):
        assert mdf.normalize_name("电影 副本.mkv") == "电影"

    def test_resolution_markers_stripped(self):
        assert mdf.normalize_name("movie 1080p.mkv", strip_res=True) == "movie"

    def test_resolution_markers_kept(self):
        assert "1080p" in mdf.normalize_name("movie 1080p.mkv", strip_res=False)

    def test_separator_normalization(self):
        assert mdf.normalize_name("my_-_great video.mkv") == "my great video"

    def test_uc_suffix_stripped(self):
        assert mdf.normalize_name("movie U.mkv") == "movie"
        assert mdf.normalize_name("movie UC.mkv") == "movie"


class TestNameSimilarity:
    def test_identical(self):
        assert mdf.name_similarity("hello", "hello") == 1.0

    def test_empty_both(self):
        assert mdf.name_similarity("", "") == 1.0

    def test_security_pruning(self):
        a = "a" * 50
        b = "b" * 1
        assert mdf.name_similarity(a, b, threshold=0.8) == 0.0

    def test_similar(self):
        sim = mdf.name_similarity("episode01", "episode01v2")
        assert sim > 0.7

    def test_dissimilar(self):
        sim = mdf.name_similarity("completely", "different")
        assert sim < 0.5


class TestSuggestKeep:
    def test_higher_resolution_wins(self):
        files = [
            {"path": "/a", "width": 1920, "height": 1080,
             "video_bitrate": 5000000, "audio_bitrate": 192000,
             "size": 100, "duration": 100},
            {"path": "/b", "width": 1280, "height": 720,
             "video_bitrate": 8000000, "audio_bitrate": 192000,
             "size": 200, "duration": 100},
        ]
        assert mdf.suggest_keep(files) == "/a"

    def test_higher_bitrate_wins_when_same_res(self):
        files = [
            {"path": "/a", "width": 1920, "height": 1080,
             "video_bitrate": 3000000, "audio_bitrate": 192000,
             "size": 100, "duration": 100},
            {"path": "/b", "width": 1920, "height": 1080,
             "video_bitrate": 6000000, "audio_bitrate": 192000,
             "size": 100, "duration": 100},
        ]
        assert mdf.suggest_keep(files) == "/b"


class TestMedianSize:
    def test_odd_count(self):
        files = [{"size": 100}, {"size": 200}, {"size": 300}]
        assert mdf._median_size(files) == 200

    def test_even_count(self):
        files = [{"size": 100}, {"size": 200}, {"size": 300}, {"size": 400}]
        assert mdf._median_size(files) == 250

    def test_single(self):
        files = [{"size": 500}]
        assert mdf._median_size(files) == 500


def _fake_file(path, size, duration, resolution,
               video_bitrate=0, audio_bitrate=0, name=None):
    fname = name or os.path.basename(path)
    return {
        "path": path,
        "name": fname,
        "size": size,
        "duration": duration,
        "width": 0,
        "height": 0,
        "resolution": resolution,
        "video_bitrate": video_bitrate,
        "audio_bitrate": audio_bitrate,
        "format": "",
        "_norm_strip": mdf.normalize_name(fname, strip_res=True),
    }


class TestFindStrongCandidates:
    def test_exact_match(self):
        files = [
            _fake_file("/a.mkv", 1000, 30.0, "1920x1080"),
            _fake_file("/b.mkv", 1000, 30.0, "1920x1080"),
            _fake_file("/c.mkv", 2000, 30.0, "1920x1080"),
        ]
        groups = mdf.find_strong_candidates(files, tol_sec=1.0)
        assert len(groups) == 1
        paths = sorted(f["path"] for f in groups[0]["files"])
        assert paths == ["/a.mkv", "/b.mkv"]

    def test_tolerance(self):
        files = [
            _fake_file("/a.mkv", 1000, 30.0, "1920x1080"),
            _fake_file("/b.mkv", 1000, 30.9, "1920x1080"),
            _fake_file("/c.mkv", 1000, 32.0, "1920x1080"),
        ]
        groups = mdf.find_strong_candidates(files, tol_sec=1.0)
        assert len(groups) == 1
        paths = sorted(f["path"] for f in groups[0]["files"])
        assert paths == ["/a.mkv", "/b.mkv"]

    def test_transitive_closure(self):
        files = [
            _fake_file("/a.mkv", 1000, 0.0, "1920x1080"),
            _fake_file("/b.mkv", 1000, 0.9, "1920x1080"),
            _fake_file("/c.mkv", 1000, 1.8, "1920x1080"),
        ]
        groups = mdf.find_strong_candidates(files, tol_sec=1.0)
        assert len(groups) == 1
        assert len(groups[0]["files"]) == 3

    def test_no_match_different_sizes(self):
        files = [
            _fake_file("/a.mkv", 1000, 30.0, "1920x1080"),
            _fake_file("/b.mkv", 2000, 30.0, "1920x1080"),
        ]
        groups = mdf.find_strong_candidates(files, tol_sec=1.0)
        assert len(groups) == 0


class TestFindMidCandidates:
    def test_subcase_a_similar_name_same_res(self):
        f1 = _fake_file("/a/episode01.mkv", 1000, 30.0, "1920x1080",
                        video_bitrate=5000000, name="episode01.mkv")
        f2 = _fake_file("/b/episode01v2.mkv", 1200, 30.0, "1920x1080",
                        video_bitrate=5000000, name="episode01v2.mkv")
        groups = mdf.find_mid_candidates([f1, f2], sim_threshold=0.7)
        assert len(groups) >= 1

    def test_claimed_pairs_skipped(self):
        f1 = _fake_file("/a/ep.mkv", 1000, 30.0, "1920x1080",
                        video_bitrate=5000000, name="ep.mkv")
        f2 = _fake_file("/b/ep-copy.mkv", 1200, 30.0, "1920x1080",
                        video_bitrate=5000000, name="ep-copy.mkv")
        claimed = {mdf._pair_key(f1, f2)}
        groups = mdf.find_mid_candidates([f1, f2], sim_threshold=0.7,
                                         claimed_pairs=claimed)
        assert len(groups) == 0


class TestFindWeakCandidates:
    def test_duration_close_same_res(self):
        f1 = _fake_file("/a.mkv", 1000, 30.0, "1920x1080")
        f2 = _fake_file("/b.mkv", 1500, 31.0, "1920x1080")
        groups = mdf.find_weak_candidates([f1, f2], tol_sec=2.0)
        assert len(groups) == 1

    def test_duration_too_far(self):
        f1 = _fake_file("/a.mkv", 1000, 30.0, "1920x1080")
        f2 = _fake_file("/b.mkv", 1500, 40.0, "1920x1080")
        groups = mdf.find_weak_candidates([f1, f2], tol_sec=2.0)
        assert len(groups) == 0

    def test_different_resolution(self):
        f1 = _fake_file("/a.mkv", 1000, 30.0, "1920x1080")
        f2 = _fake_file("/b.mkv", 1500, 30.5, "1280x720")
        groups = mdf.find_weak_candidates([f1, f2], tol_sec=2.0)
        assert len(groups) == 0


class TestBuildResult:
    def test_wasted_bytes_calculation(self):
        roots = ["G:\\"]
        strong = [{
            "reason": "test",
            "files": [
                _fake_file("/a.mkv", 1000, 30.0, "1920x1080"),
                _fake_file("/b.mkv", 1000, 30.0, "1920x1080"),
                _fake_file("/c.mkv", 1000, 30.0, "1920x1080"),
            ]
        }]
        result = mdf.build_result(roots, strong, [], [], [], 3)
        g = result["strong_candidates"][0]
        assert g["file_count"] == 3
        assert g["median_size"] == 1000
        assert g["wasted_bytes"] == 2000
        assert "group_id" in g
        assert "suggested_keep" in g

    def test_sorted_by_wasted(self):
        roots = ["G:\\"]
        big = [{
            "reason": "big",
            "files": [
                _fake_file("/a.mkv", 5000, 30.0, "1920x1080"),
                _fake_file("/b.mkv", 5000, 30.0, "1920x1080"),
                _fake_file("/c.mkv", 5000, 30.0, "1920x1080"),
            ]
        }]
        small = [{
            "reason": "small",
            "files": [
                _fake_file("/d.mkv", 1000, 30.0, "1920x1080"),
                _fake_file("/e.mkv", 1000, 30.0, "1920x1080"),
            ]
        }]
        result = mdf.build_result(roots, big, small, [], [], 5)
        assert result["strong_candidates"][0]["wasted_bytes"] > \
               result["mid_candidates"][0]["wasted_bytes"]


class TestPairKey:
    def test_order_independent(self):
        a = {"path": "/a"}
        b = {"path": "/b"}
        assert mdf._pair_key(a, b) == mdf._pair_key(b, a)


# ---------- 0.4.0 新增能力 ----------

class TestNormalizeExtSet:
    def test_comma_string(self):
        assert mdf.normalize_ext_set("mp4,mkv") == {".mp4", ".mkv"}

    def test_dots_and_case(self):
        assert mdf.normalize_ext_set([".MKV", "MP4"]) == {".mkv", ".mp4"}

    def test_mixed_separators(self):
        assert mdf.normalize_ext_set("ts;rmvb avi") == {".ts", ".rmvb", ".avi"}

    def test_empty_returns_none(self):
        assert mdf.normalize_ext_set(None) is None
        assert mdf.normalize_ext_set("") is None
        assert mdf.normalize_ext_set(["", "  "]) is None


class TestFilterAllowedRoots:
    def test_no_allow_list_allows_all(self):
        allowed, denied = mdf.filter_allowed_roots(["/a", "/b"], [])
        assert allowed == ["/a", "/b"]
        assert denied == []

    def test_subpath_allowed(self, tmp_path):
        base = tmp_path / "media"
        sub = base / "sub"
        sub.mkdir(parents=True)
        allowed, denied = mdf.filter_allowed_roots([str(sub)], [str(base)])
        assert allowed == [str(sub)]
        assert denied == []

    def test_outside_denied(self, tmp_path):
        a = tmp_path / "a"
        b = tmp_path / "b"
        a.mkdir()
        b.mkdir()
        allowed, denied = mdf.filter_allowed_roots([str(b)], [str(a)])
        assert allowed == []
        assert denied == [str(b)]


class TestScanControl:
    def test_initial_state(self):
        c = mdf.ScanControl()
        assert not c.paused
        assert not c.cancelled

    def test_pause_and_resume(self):
        c = mdf.ScanControl()
        c.pause()
        assert c.paused
        c.resume()
        assert not c.paused

    def test_cancel_clears_pause(self):
        c = mdf.ScanControl()
        c.pause()
        c.cancel()
        assert c.cancelled
        assert not c.paused

    def test_wait_if_paused_blocks_until_resume(self):
        import threading
        import time
        c = mdf.ScanControl()
        c.pause()
        done = threading.Event()

        def worker():
            c.wait_if_paused()
            done.set()

        th = threading.Thread(target=worker, daemon=True)
        th.start()
        time.sleep(0.3)
        assert not done.is_set()
        c.resume()
        th.join(timeout=2)
        assert done.is_set()


class TestMetaCache:
    def test_roundtrip(self, tmp_path):
        p = tmp_path / "cache.json"
        data = {"a.mp4": {"size": 10, "mtime": 1.5}}
        mdf.save_meta_cache(p, data)
        assert mdf.load_meta_cache(p) == data

    def test_missing_file_returns_empty(self, tmp_path):
        assert mdf.load_meta_cache(tmp_path / "nope.json") == {}

    def test_corrupt_file_returns_empty(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("not json", encoding="utf-8")
        assert mdf.load_meta_cache(p) == {}


class TestEnumerateExtFilter:
    def _make_files(self, tmp_path):
        for name in ("a.mp4", "b.mkv", "c.txt"):
            (tmp_path / name).write_bytes(b"0" * 100)

    def test_include_only(self, tmp_path):
        self._make_files(tmp_path)
        files, roots, total = mdf._enumerate(
            [str(tmp_path)], include_exts={".mp4"})
        names = [os.path.basename(f["path"]) for f in files[str(tmp_path)]]
        assert names == ["a.mp4"]
        assert total == 1

    def test_include_can_add_non_media(self, tmp_path):
        self._make_files(tmp_path)
        files, _, total = mdf._enumerate(
            [str(tmp_path)], include_exts="txt")
        names = [os.path.basename(f["path"]) for f in files[str(tmp_path)]]
        assert names == ["c.txt"]
        assert total == 1

    def test_exclude_blacklist(self, tmp_path):
        self._make_files(tmp_path)
        files, _, total = mdf._enumerate(
            [str(tmp_path)], exclude_exts={".mkv"})
        names = [os.path.basename(f["path"]) for f in files[str(tmp_path)]]
        assert names == ["a.mp4"]
        assert total == 1

    def test_min_size_filter(self, tmp_path):
        (tmp_path / "big.mp4").write_bytes(b"0" * 5000)
        (tmp_path / "small.mp4").write_bytes(b"0" * 10)
        files, _, total = mdf._enumerate([str(tmp_path)], min_size_bytes=1000)
        assert total == 1
        assert files[str(tmp_path)][0]["size"] == 5000

    def test_candidate_fields(self, tmp_path):
        (tmp_path / "a.mp4").write_bytes(b"0" * 42)
        files, roots, _ = mdf._enumerate([str(tmp_path)])
        item = files[str(tmp_path)][0]
        assert set(item) == {"path", "size", "mtime"}
        assert item["size"] == 42
        assert roots == [str(tmp_path)]

    def test_missing_root_skipped(self, tmp_path):
        files, roots, total = mdf._enumerate([str(tmp_path / "ghost")])
        assert roots == []
        assert total == 0


class TestPreviewScan:
    def test_empty_dir(self, tmp_path):
        info = mdf.preview_scan([str(tmp_path)])
        assert info["total_candidates"] == 0
        assert info["est_seconds"] == 0.0
        assert info["per_root"][str(tmp_path)] == 0

    def test_counts_candidates(self, tmp_path):
        for i in range(3):
            (tmp_path / f"v{i}.mp4").write_bytes(b"0" * 128)
        info = mdf.preview_scan([str(tmp_path)], sample_size=2)
        assert info["total_candidates"] == 3
        assert info["sample_size"] == 2
        assert info["avg_ms"] >= 0