"""End-to-end API tests: upload -> process (real OCR) -> review -> audit -> export."""
import csv
import io
import time
import zipfile

import pytest
from fastapi.testclient import TestClient

from app import main


@pytest.fixture(scope="module")
def client():
    main.init(start_workers=False)
    with TestClient(main.app) as c:
        yield c


def drain(job_id):
    """Process every queued image synchronously with the real worker code."""
    pool = main.state.pool
    while True:
        row = pool.claim()
        if row is None:
            break
        pool.process(row)


def upload(client, job_id, items):
    files = [("files", (name, data, "application/octet-stream")) for name, data in items]
    paths = [("paths", (None, name)) for name, _ in items]
    r = client.post(f"/api/jobs/{job_id}/upload", files=files + paths)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture(scope="module")
def job(client, dataset):
    d, _ = dataset
    j = client.post("/api/jobs", json={"name": "api test"}).json()
    items = [
        ("menus/p01_title_case.png", (d / "p01_title_case.png").read_bytes()),
        ("menus/p15_multiple.png", (d / "p15_multiple.png").read_bytes()),
        ("other/n02_other_menu.jpg", (d / "n02_other_menu.jpg").read_bytes()),
        ("copy/p01_title_case.png", (d / "p01_title_case.png").read_bytes()),          # exact duplicate
        ("other/p01_title_case.png", (d / "n01_mocktail.png").read_bytes()),          # same name, different file
        ("notes.txt", b"just text"),                                                   # unsupported
        ("broken.jpg", (d / "p02_uppercase.jpg").read_bytes()[:1500]),                 # corrupted
        ("empty.png", b""),                                                            # empty
    ]
    res = upload(client, j["id"], items)
    assert res["accepted"] == 5 and res["duplicates"] == 1 and res["rejected"] == 2
    drain(j["id"])
    return j


def images(client, job_id, **params):
    return client.get(f"/api/jobs/{job_id}/images", params={"limit": 1000, **params}).json()["items"]


def by_path(client, job_id):
    return {i["rel_path"]: i for i in images(client, job_id)}


def test_every_accepted_image_has_a_status(client, job):
    imgs = by_path(client, job["id"])
    assert len(imgs) == 6
    assert all(i["proc_status"] in ("done", "error", "duplicate") for i in imgs.values())
    assert imgs["menus/p01_title_case.png"]["final"] == "MATCH_CONFIRMED"
    assert imgs["menus/p15_multiple.png"]["final"] == "MATCH_CONFIRMED"
    assert imgs["other/p01_title_case.png"]["final"] != "MATCH_CONFIRMED"   # mocktail
    assert imgs["broken.jpg"]["proc_status"] == "error"
    assert imgs["broken.jpg"]["final"] == "REVIEW_REQUIRED"                 # failure is never negative
    dup = imgs["copy/p01_title_case.png"]
    assert dup["proc_status"] == "duplicate" and dup["duplicate_of"] == imgs["menus/p01_title_case.png"]["id"]


def test_rejected_uploads_are_recorded(client, job):
    rej = client.get(f"/api/jobs/{job['id']}/rejected").json()
    assert {r["original_name"] for r in rej} == {"notes.txt", "empty.png"}


def test_summary_counts(client, job):
    s = client.get(f"/api/jobs/{job['id']}").json()["summary"]
    assert s["total_unique"] == 5 and s["duplicates"] == 1 and s["rejected_uploads"] == 2
    assert s["failed"] == 1 and s["pending"] == 0 and s["processing_complete"]
    assert s["confirmed_matches"] == 2
    assert s["occurrences"]["total"] >= 2
    assert not s["audit_complete"] and not s["strict_fully_verified"]
    assert s["unresolved"] == 5


def test_detail_has_evidence(client, job):
    img = by_path(client, job["id"])["menus/p01_title_case.png"]
    d = client.get(f"/api/images/{img['id']}").json()
    hit = next(h for h in d["hits"] if h["kind"] == "exact")
    assert hit["raw"].lower() == "cocktail" and len(hit["poly"]) == 4
    assert d["passes"][0]["stage"] == "A" and "Cocktail" in d["passes"][0]["text"]
    assert d["duplicates"]
    assert client.get(f"/api/images/{img['id']}/thumb").status_code == 200
    assert client.get(f"/api/images/{img['id']}/view").status_code == 200
    orig = client.get(f"/api/images/{img['id']}/original")
    assert orig.status_code == 200 and orig.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_upload_check_for_resume(client, job):
    r = client.post(f"/api/jobs/{job['id']}/upload/check", json={"files": [
        {"path": "menus/p01_title_case.png", "size": by_path(client, job["id"])["menus/p01_title_case.png"]["size_bytes"]},
        {"path": "menus/new.png", "size": 10}]}).json()
    assert r["present"] == [True, False]


def test_filters_and_search(client, job):
    jid = job["id"]
    assert len(images(client, jid, filter="match")) == 2
    assert len(images(client, jid, filter="failed")) == 1
    assert len(images(client, jid, filter="duplicate")) == 1
    assert {i["rel_path"] for i in images(client, jid, q="Cocktail Menu")} == {"menus/p01_title_case.png", "copy/p01_title_case.png"}
    assert [i["rel_path"] for i in images(client, jid, q="n02_other")] == ["other/n02_other_menu.jpg"]
    srt = images(client, jid, sort="confidence", order="desc")
    assert srt[0]["best_confidence"] is not None
    assert client.get(f"/api/jobs/{jid}/images", params={"filter": "bogus"}).status_code == 400


def test_retry_and_interrupted_recovery(client, job):
    jid = job["id"]
    broken = by_path(client, jid)["broken.jpg"]
    r = client.post(f"/api/jobs/{jid}/retry", params={"scope": "failed"}).json()
    assert r["requeued"] == 1
    # simulate a crash mid-processing
    row = main.state.pool.claim()
    assert row["id"] == broken["id"]
    assert by_path(client, jid)["broken.jpg"]["proc_status"] == "processing"
    assert main.state.pool.recover() == 1
    assert by_path(client, jid)["broken.jpg"]["proc_status"] == "queued"
    drain(jid)
    assert by_path(client, jid)["broken.jpg"]["proc_status"] == "error"


def test_pause_blocks_claims(client, job):
    jid = job["id"]
    client.patch(f"/api/jobs/{jid}", json={"state": "paused"})
    client.post(f"/api/jobs/{jid}/retry", params={"scope": "failed"})
    assert main.state.pool.claim() is None
    client.patch(f"/api/jobs/{jid}", json={"state": "running"})
    drain(jid)


def test_review_and_audit_flow(client, job):
    jid = job["id"]
    imgs = by_path(client, jid)
    # Duplicate inherits the decision of its original.
    client.post(f"/api/images/{imgs['copy/p01_title_case.png']['id']}/review", json={"decision": "match"})
    imgs = by_path(client, jid)
    assert imgs["menus/p01_title_case.png"]["review_decision"] == "match"
    client.post(f"/api/images/{imgs['broken.jpg']['id']}/review", json={"decision": "unreadable"})
    s = client.get(f"/api/jobs/{jid}").json()["summary"]
    assert s["human_unreadable"] == 1 and not s["audit_complete"]
    ids = [imgs[p]["id"] for p in ("menus/p15_multiple.png", "other/n02_other_menu.jpg", "other/p01_title_case.png")]
    client.post(f"/api/jobs/{jid}/review-bulk", json={"ids": ids[:1], "decision": "match", "method": "full_view"})
    client.post(f"/api/jobs/{jid}/review-bulk", json={"ids": ids[1:], "decision": "no_match", "method": "contact_sheet"})
    s = client.get(f"/api/jobs/{jid}").json()["summary"]
    assert s["unresolved"] == 1 and not s["strict_fully_verified"]  # broken.jpg is still unreadable
    client.post(f"/api/images/{imgs['broken.jpg']['id']}/review", json={"decision": "no_match", "note": "file is corrupt; checked source"})
    s = client.get(f"/api/jobs/{jid}").json()["summary"]
    assert s["strict_fully_verified"] and s["audit_complete"] and s["unresolved"] == 0
    assert "fully human-verified" in s["audit_message"]
    # Clearing a decision re-opens the audit.
    client.post(f"/api/images/{imgs['broken.jpg']['id']}/review", json={"decision": None})
    assert not client.get(f"/api/jobs/{jid}").json()["summary"]["strict_fully_verified"]
    client.post(f"/api/images/{imgs['broken.jpg']['id']}/review", json={"decision": "no_match"})


def test_full_csv_contains_every_image(client, job):
    r = client.get(f"/api/jobs/{job['id']}/export/all.csv")
    assert r.status_code == 200
    rows = list(csv.DictReader(io.StringIO(r.content.decode("utf-8-sig"))))
    assert len(rows) == 6
    assert {"original_filename", "sha256", "final_classification", "detected_words", "raw_ocr_text",
            "ocr_engines_run", "best_confidence", "verification_status", "error"} <= set(rows[0])
    p01 = next(r for r in rows if r["relative_path"] == "menus/p01_title_case.png")
    assert p01["final_classification"] == "MATCH_CONFIRMED" and p01["detected_words"] == "cocktail"
    assert "Cocktail" in p01["raw_ocr_text"] and p01["bounding_boxes"].startswith("[{")
    m = list(csv.DictReader(io.StringIO(client.get(f"/api/jobs/{job['id']}/export/matches.csv").content.decode("utf-8-sig"))))
    assert {r["relative_path"] for r in m} == {"menus/p01_title_case.png", "menus/p15_multiple.png", "copy/p01_title_case.png"}


def test_matches_zip_keeps_names_and_avoids_collisions(client, job, dataset):
    jid = job["id"]
    d, _ = dataset
    # a second, different file with the same basename that also matches
    upload(client, jid, [("more/p15_multiple.png", (d / "p03_lowercase_italic.png").read_bytes())])
    drain(jid)
    z = zipfile.ZipFile(io.BytesIO(client.get(f"/api/jobs/{jid}/export/matches.zip").content))
    names = sorted(z.namelist())
    assert names == ["manifest.csv", "p01_title_case.png", "p15_multiple (2).png", "p15_multiple.png"]
    assert z.read("p01_title_case.png") == (d / "p01_title_case.png").read_bytes()  # unmodified original


def test_review_zip_and_reports(client, job):
    jid = job["id"]
    z = zipfile.ZipFile(io.BytesIO(client.get(f"/api/jobs/{jid}/export/review.zip").content))
    assert "manifest.csv" in z.namelist()
    rep = client.get(f"/api/jobs/{jid}/export/report.json").json()
    assert rep["summary"]["total_unique"] == 6 and len(rep["rejected_uploads"]) == 2
    html = client.get(f"/api/jobs/{jid}/export/report.html").text
    assert "verification report" in html.lower() and "notes.txt" in html


def test_csv_formula_injection_neutralised(client, dataset):
    d, _ = dataset
    j = client.post("/api/jobs", json={"name": "inj"}).json()
    upload(client, j["id"], [("=cmd()+.png", (d / "n03_blank.png").read_bytes())])
    rows = list(csv.DictReader(io.StringIO(client.get(f"/api/jobs/{j['id']}/export/all.csv").content.decode("utf-8-sig"))))
    assert rows[0]["original_filename"].startswith("'=")


def test_path_traversal_names_are_sanitised(client, dataset):
    d, _ = dataset
    j = client.post("/api/jobs", json={"name": "trav"}).json()
    res = upload(client, j["id"], [("../../etc/evil.png", (d / "n03_blank.png").read_bytes())])
    assert res["results"][0]["name"] == "etc/evil.png"
    client.delete(f"/api/jobs/{j['id']}")
    assert client.get(f"/api/jobs/{j['id']}").status_code == 404
