import os
import time

import pymupdf
import pytest

from mephistocr import engine
import helpers


def _run(target, backup, **job_kw):
    """Run a bulk job in-process; returns (events, counts, seconds)."""
    events = []
    worker = job_kw.pop("worker", None)
    job = engine.Job(target=str(target), backup=str(backup), langs="eng", **job_kw)
    t0 = time.time()
    engine.run_job(job, os.path.join(str(backup), "_ocr_log.tsv"),
                   lambda kind, data: events.append((kind, data)),
                   **({"worker": worker} if worker else {}))
    done = [d for k, d in events if k == "done"]
    return events, (done[0]["counts"] if done else None), time.time() - t0


def _read(path):
    with open(engine.long_path(str(path)), "rb") as f:
        return f.read()


class TestLongPath:
    def test_prefixes_absolute_windows_path(self):
        result = engine.long_path(r"C:\some\path\file.pdf")
        assert result == "\\\\?\\C:\\some\\path\\file.pdf"

    def test_does_not_double_prefix_already_prefixed_path(self):
        already = "\\\\?\\C:\\some\\path\\file.pdf"
        assert engine.long_path(already) == already


class TestOcrOneLongPath:
    def test_opens_pdf_beyond_windows_max_path(self, tmp_path):
        deep_dir = helpers.deep_dir(str(tmp_path), 240)
        pdf_path = os.path.join(deep_dir, "y" * 40 + "_some_manual.pdf")
        assert len(pdf_path) > 260, "test setup must actually exceed MAX_PATH"
        helpers.text_pdf(pdf_path)

        backup_root = os.path.join(str(tmp_path), "backup")
        cat, status, extra = engine.ocr_one(
            pdf_path, engine.output_path(pdf_path), str(tmp_path), backup_root, "eng", 200)

        assert (cat, status) == ("has_text", "OK_HAS_TEXT"), f"got {cat}/{status}: {extra}"


class TestCollectTiffs:
    def test_finds_tif_and_tiff_case_insensitively_recursively(self, tmp_path):
        (tmp_path / "sub").mkdir()
        (tmp_path / "a.tif").write_bytes(b"x")
        (tmp_path / "sub" / "b.TIFF").write_bytes(b"x")
        (tmp_path / "c.pdf").write_bytes(b"x")

        found = engine.collect_tiffs(str(tmp_path), recursive=True)

        assert sorted(os.path.basename(f) for f in found) == ["a.tif", "b.TIFF"]

    def test_non_recursive_skips_subfolders(self, tmp_path):
        (tmp_path / "sub").mkdir()
        (tmp_path / "a.tif").write_bytes(b"x")
        (tmp_path / "sub" / "b.tif").write_bytes(b"x")

        found = engine.collect_tiffs(str(tmp_path), recursive=False)

        assert [os.path.basename(f) for f in found] == ["a.tif"]


class TestTiffSource:
    def test_tiff_beyond_max_path_opens_with_all_pages(self, tmp_path):
        deep_dir = helpers.deep_dir(str(tmp_path), 240)
        tif_path = os.path.join(deep_dir, "y" * 40 + "_scan.tif")
        assert len(tif_path) > 260, "test setup must actually exceed MAX_PATH"
        helpers.tiff(tif_path, pages=(helpers.EN, helpers.HU, helpers.EN))

        doc = engine._open_source(tif_path)
        try:
            assert doc.page_count == 3
        finally:
            doc.close()


class TestRunJobTiffIntegration:
    def test_tiff_is_ocrd_without_intermediate_pdf_and_backed_up(self, tmp_path):
        target, backup = tmp_path / "target", tmp_path / "backup"
        helpers.tiff(str(target / "scan.tif"))

        events, counts, _ = _run(target, backup, workers=1)

        assert counts.ocr == 1, events
        assert sorted(os.listdir(target)) == ["scan_ocr.pdf"]
        assert sorted(p for p in os.listdir(backup) if not p.startswith("_")) == ["scan.tif"]


# --- P0 regressions ---------------------------------------------------------

class TestSameNameTiffAndPdf:
    def test_tiff_never_overwrites_same_name_pdf_and_run_finishes(self, tmp_path):
        # C-1: x.tif used to be converted *onto* x.pdf, and the doubly queued
        # path made the supervisor wait forever.
        target, backup = tmp_path / "target", tmp_path / "backup"
        pdf = helpers.text_pdf(str(target / "x.pdf"), ["ORIGINAL born-digital PDF"] * 3)
        helpers.tiff(str(target / "x.tif"))
        original = _read(pdf)

        events, counts, secs = _run(target, backup, workers=2)

        assert secs < 30 and counts is not None
        assert _read(pdf) == original
        assert (counts.has_text, counts.ocr, counts.error) == (1, 1, 0), events
        assert (target / "x_tif_ocr.pdf").exists()

    def test_plan_dedupes_sources_and_never_shares_an_output(self, tmp_path):
        a = str(tmp_path / "a.pdf")
        items, clashes = engine.plan_items(
            [a, a.upper(), str(tmp_path / "a.tif"), str(tmp_path / "a.tiff")])

        outs = [os.path.basename(o) for _, o in items]
        assert outs == ["a_ocr.pdf", "a_tif_ocr.pdf", "a_tiff_ocr.pdf"] and clashes == []

    def test_watchdog_ends_run_when_work_goes_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(engine, "WATCHDOG_S", 1)
        target, backup = tmp_path / "target", tmp_path / "backup"
        helpers.scanned_pdf(str(target / "a.pdf"))
        helpers.scanned_pdf(str(target / "b.pdf"))

        events, counts, secs = _run(target, backup, workers=1, worker=helpers.lossy_worker)

        assert secs < 30 and counts.error == 2
        assert (backup / "_ocr_log.tsv").read_text(encoding="utf-8").count("ERROR_LOST") == 2


class TestBackupMove:
    def test_repeated_moves_never_overwrite(self, tmp_path):
        # C-2: the old _<seconds> suffix collided within one second and the
        # copy fallback then overwrote the earlier backup.
        backup = tmp_path / "backup"
        for gen in range(3):
            src = tmp_path / "x.pdf"
            src.write_bytes(f"GENERATION-{gen}".encode())
            engine.move_to_backup(str(src), str(tmp_path), str(backup))

        contents = sorted(p.read_bytes() for p in backup.iterdir())
        assert contents == [b"GENERATION-0", b"GENERATION-1", b"GENERATION-2"]

    def test_locked_source_leaves_nothing_in_backup(self, tmp_path):
        backup = tmp_path / "backup"
        src = tmp_path / "locked.pdf"
        src.write_bytes(b"data")
        with open(src, "rb"):  # an open handle blocks rename/delete on Windows
            with pytest.raises(OSError):
                engine.move_to_backup(str(src), str(tmp_path), str(backup))
            with pytest.raises(OSError):  # the cross-volume path cleans up too
                engine._copy_across_volumes(str(src), str(backup / "copy.pdf"))

        assert src.exists() and list(backup.iterdir()) == []


class TestFolderValidation:
    def test_same_nested_or_case_variant_folders_are_rejected(self, tmp_path):
        target = tmp_path / "Target"
        target.mkdir()
        assert engine.check_folders(str(target), str(target))
        assert engine.check_folders(str(target), str(tmp_path / "TARGET"))
        assert engine.check_folders(str(target), str(target / "backup"))
        assert engine.check_folders(str(target), str(tmp_path))
        assert engine.check_folders(str(target), str(tmp_path / "Target2")) is None


# --- P1 regressions ---------------------------------------------------------

class TestLongPathWalk:
    def test_folder_deeper_than_max_path_is_collected_and_ocrd(self, tmp_path):
        # C-3: os.walk without the \?\ prefix silently skipped such folders.
        target, backup = tmp_path / "target", tmp_path / "backup"
        deep = helpers.deep_dir(str(target / "deepdir"), 270)
        helpers.scanned_pdf(os.path.join(deep, "in_very_deep_dir.pdf"))

        events, counts, _ = _run(target, backup, workers=1)

        assert counts.ocr == 1, events
        assert os.path.exists(engine.long_path(os.path.join(deep, "in_very_deep_dir_ocr.pdf")))

    def test_unreadable_folder_is_reported_not_skipped(self, tmp_path):
        errors = []
        engine.collect_inputs(str(tmp_path / "missing"), recursive=False, errors=errors)
        assert len(errors) == 1


class TestFaithfulOutput:
    def test_ocr_keeps_resolution_links_annotations_toc_and_metadata(self, tmp_path):
        # C-5: the output used to be a 200-dpi re-render without any of these.
        src = helpers.scanned_pdf(str(tmp_path / "scan.pdf"), dpi=300)
        doc = pymupdf.open(src)
        doc[0].insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(50, 50, 150, 70),
                            "uri": "https://example.org"})
        doc[0].add_text_annot((300, 300), "note")
        doc.set_toc([[1, "Chapter", 1]])
        doc.set_metadata({"title": "Title", "author": "Author"})
        doc.saveIncr()
        doc.close()
        out = engine.output_path(src)

        cat, status, _ = engine.ocr_one(src, out, str(tmp_path), "", "eng", 200)

        res = pymupdf.open(out)
        page = res[0]
        assert (cat, status) == ("ocr", "OCR_DONE")
        assert max(img[2] for img in page.get_images()) == 2479  # the 300-dpi source, not a re-render
        assert len(page.get_links()) == 1 and len(list(page.annots())) == 1
        assert res.get_toc() == [[1, "Chapter", 1]] and res.metadata["author"] == "Author"
        assert page.search_for("lubricant")
        res.close()

    def test_mixed_pdf_gets_ocr_only_on_its_scanned_page(self, tmp_path):
        # C-4: a born-digital first page used to exempt the whole file.
        src = str(tmp_path / "mixed.pdf")
        helpers.text_pdf(src)
        scan = pymupdf.open(helpers.scanned_pdf(str(tmp_path / "scan.pdf")))
        doc = pymupdf.open(src)
        doc.insert_pdf(scan)
        doc.saveIncr()
        text_page_before = doc[0].read_contents()
        doc.close()

        cat, status, extra = engine.ocr_one(src, engine.output_path(src), str(tmp_path), "", "eng", 200)

        res = pymupdf.open(engine.output_path(src))
        assert (cat, status, extra.split(",")[0]) == ("ocr", "OCR_DONE", "1 of 2 pages")
        assert res[0].read_contents() == text_page_before
        assert res[1].search_for("lubricant")
        res.close()

    def test_tiff_page_size_follows_its_dpi(self, tmp_path):
        # C-6: a 300-dpi A4 TIFF used to become a 875x1238 mm page.
        tif = helpers.tiff(str(tmp_path / "fax.tif"), dpi=300)

        cat, _, _ = engine.ocr_one(tif, engine.output_path(tif), str(tmp_path), "", "eng", 200)

        res = pymupdf.open(engine.output_path(tif))
        assert cat == "ocr" and abs(res[0].rect.width - 595) < 2 and abs(res[0].rect.height - 842) < 2
        res.close()

    def test_page_without_text_still_gets_an_output(self, tmp_path):
        # C-13: drawings without text failed and were retried on every run.
        src = helpers.scanned_pdf(str(tmp_path / "drawing.pdf"), pages=([],))

        cat, status, _ = engine.ocr_one(src, engine.output_path(src), str(tmp_path), "", "eng", 200)

        assert (cat, status) == ("ocr", "OCR_NO_TEXT") and os.path.exists(engine.output_path(src))


class TestSupervisor:
    def test_stuck_page_times_out_and_run_continues(self, tmp_path, monkeypatch):
        monkeypatch.setattr(engine, "PAGE_TIMEOUT_S", 1)
        target, backup = tmp_path / "target", tmp_path / "backup"
        helpers.scanned_pdf(str(target / "a.pdf"))
        helpers.scanned_pdf(str(target / "b.pdf"))

        _, counts, secs = _run(target, backup, workers=1, worker=helpers.sleepy_worker)

        assert counts.error == 2 and secs < 30
        assert (backup / "_ocr_log.tsv").read_text(encoding="utf-8").count("ERROR_TIMEOUT") == 2

    def test_crashing_worker_fails_only_its_file(self, tmp_path):
        target, backup = tmp_path / "target", tmp_path / "backup"
        helpers.scanned_pdf(str(target / "crash.pdf"))
        helpers.scanned_pdf(str(target / "fine.pdf"))

        _, counts, _ = _run(target, backup, workers=1, worker=helpers.crashy_worker)

        assert (counts.ocr, counts.error) == (1, 1)
        assert (target / "crash.pdf").exists() and (target / "fine_ocr.pdf").exists()
        assert "ERROR_CRASHED" in (backup / "_ocr_log.tsv").read_text(encoding="utf-8")


class TestLanguageData:
    def test_unknown_or_missing_languages_are_rejected(self):
        # C-11: "hun+xyz" silently ran as "hun".
        assert "xyz" in engine.check_langs("hun+xyz")
        assert engine.check_langs("")
        assert engine.check_langs("hun+eng") is None

    def test_ocr_needs_no_tesseract_install(self, tmp_path, monkeypatch):
        # C-10: tesseract.exe used to be mandatory although MuPDF has it built in.
        monkeypatch.setattr(engine, "TESSERACT_DIRS", [])
        monkeypatch.setenv("PATH", "")
        monkeypatch.delenv("TESSDATA_PREFIX", raising=False)
        assert engine.setup_tesseract() == (True, engine.BUNDLED_TESSDATA)
        src = helpers.scanned_pdf(str(tmp_path / "scan.pdf"))

        assert engine.ocr_one(src, engine.output_path(src), str(tmp_path), "", "eng", 200)[1] == "OCR_DONE"


def test_worker_protocol_logs_every_outcome(tmp_path):
    # C-12/C-14 + S-7: every file gets a log line, no deprecation noise on the
    # protocol channel, corrupt != empty, and the cfg file is not left behind.
    import json
    import subprocess
    import sys

    target, backup = tmp_path / "target", tmp_path / "backup"
    helpers.text_pdf(str(target / "text.pdf"))
    helpers.scanned_pdf(str(target / "scan.pdf"))
    (target / "empty.pdf").write_bytes(b"")
    (target / "corrupt.pdf").write_bytes(b"%PDF-1.7\n1 0 obj << /Type /Catalog >>\n garbage" * 20)
    enc = pymupdf.open()
    enc.new_page().insert_text((72, 72), "secret " * 10)
    enc.save(str(target / "locked.pdf"), encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="u", owner_pw="o")
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({"target": str(target), "backup": str(backup), "langs": "eng",
                               "workers": 2}), encoding="utf-8")

    p = subprocess.run([sys.executable, "-m", "mephistocr.engine", "--worker", str(cfg)],
                       cwd=os.path.dirname(os.path.dirname(engine.__file__)),
                       capture_output=True, timeout=120)
    lines = p.stdout.decode("utf-8").splitlines()
    logs = "\n".join(line for line in lines if line.startswith("LOG\t"))

    assert p.returncode == 0 and not cfg.exists()
    assert all(line.split("\t")[0] in ("START", "PROG", "LOG", "FILE", "DONE") for line in lines), lines
    assert lines[-1] == "DONE\t1\t1\t2\t1"
    for expected in ("Already searchable: text.pdf", "OCR'd (1 of 1 pages",
                     "Skipped (empty file): empty.pdf", "Skipped (password-protected): locked.pdf",
                     "Failed (ERROR_NO_PAGES"):
        assert expected in logs, logs


class TestFileMode:
    MAIN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py")

    def test_cli_ocrs_one_file_next_to_the_untouched_original(self, tmp_path):
        # 6.3/5: `python main.py one.pdf` exits 0 and prints the output path.
        import subprocess
        import sys
        src = helpers.scanned_pdf(str(tmp_path / "one.pdf"))
        before = _read(src)

        p = subprocess.run([sys.executable, self.MAIN, src, "--lang", "eng"],
                           capture_output=True, timeout=120)

        assert p.returncode == 0, p.stderr
        assert p.stdout.decode("utf-8").strip() == engine.output_path(src)
        assert _read(src) == before

    def test_cli_exit_codes_and_json(self, tmp_path, capsys):
        import json
        from mephistocr import cli
        bad = tmp_path / "bad.pdf"
        bad.write_bytes(b"%PDF-1.7 garbage" * 20)
        good = helpers.scanned_pdf(str(tmp_path / "good.pdf"))

        code = cli.main([str(bad), good, "--out", str(tmp_path / "out"), "--lang", "eng", "--json"])

        results = json.loads(capsys.readouterr().out)
        assert code == 1 and sorted(r["cat"] for r in results) == ["error", "ocr"]
        assert (tmp_path / "out" / "good_ocr.pdf").exists()
        assert cli.main([str(tmp_path / "missing.pdf")]) == 2
        with pytest.raises(SystemExit) as usage:
            cli.main(["--folder", str(tmp_path)])  # folder mode needs --backup
        assert usage.value.code == 2

    def test_replace_mode_moves_original_but_never_onto_itself(self, tmp_path):
        src = helpers.scanned_pdf(str(tmp_path / "in" / "a.pdf"))
        job = engine.Job(files=[src], backup=str(tmp_path / "in"), langs="eng", workers=1)
        assert job.problem()  # backup == the files' own folder
        job.backup = str(tmp_path / "originals")

        engine.run_job(job, str(tmp_path / "log.tsv"), lambda kind, data: None)

        assert (tmp_path / "originals" / "a.pdf").exists() and not os.path.exists(src)
        assert (tmp_path / "in" / "a_ocr.pdf").exists()
