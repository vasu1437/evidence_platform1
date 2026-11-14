import atexit
import hashlib
import io
import os
import sqlite3
import shutil
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
import wave

TEST_STORAGE = tempfile.mkdtemp(prefix="evidence-auth-tests-")
os.environ["PYTHON_DOTENV_DISABLED"] = "true"
os.environ["STORAGE_DIR"] = TEST_STORAGE
os.environ["AUTH_USERNAME"] = "legacy-owner"
os.environ["AUTH_SESSION_SECRET"] = "isolated-test-session-secret-with-enough-bytes"
os.environ["AUTH_COOKIE_SECURE"] = "false"

from app import auth
from app import audit, db, extract, index
from app.config import ORIGINALS, WORK

LEGACY_PASSWORD = "synthetic legacy password 123"
os.environ["AUTH_PASSWORD_HASH"] = auth.hash_password(LEGACY_PASSWORD)

from fastapi.testclient import TestClient

from app.main import app

atexit.register(lambda: shutil.rmtree(TEST_STORAGE, ignore_errors=True))

LEGACY_FILE_ID = db.run(
    "INSERT INTO files(user_id,name,type,path,sha256,size,uploaded_at) VALUES(NULL,?,?,?,?,?,?)",
    ("legacy.txt", "chat", "", "synthetic-hash", 12, "2026-01-01T00:00:00Z"),
)


class AuthTests(unittest.TestCase):
    def _create_ready_file(self, client, username):
        registration = client.post("/api/auth/register", json={
            "username": username,
            "password": "synthetic test password 123",
        })
        self.assertEqual(registration.status_code, 200)
        user_id = registration.json()["user_id"]
        original_path = Path(ORIGINALS) / str(user_id) / f"{username}.txt"
        original_path.parent.mkdir(parents=True, exist_ok=True)
        original_path.write_text("Synthetic file for deletion tests.", encoding="utf-8")
        file_id = db.run(
            "INSERT INTO files(user_id,name,type,path,sha256,size,uploaded_at,status) VALUES(?,?,?,?,?,?,?,?)",
            (user_id, original_path.name, "chat", str(original_path), "synthetic-hash", 32, "2026-01-01T00:00:00Z", "ready"),
        )
        return file_id, original_path

    def test_password_hash_verifies_only_the_original_password(self):
        encoded = auth.hash_password("correct horse battery staple")

        self.assertTrue(auth.verify_password("correct horse battery staple", encoded))
        self.assertFalse(auth.verify_password("different password", encoded))
        self.assertFalse(auth.verify_password("correct horse battery staple", "invalid"))

    def test_signed_session_rejects_tampering_and_expires(self):
        secret = b"test-session-secret-that-is-long-enough"
        with patch("app.auth.time.time", return_value=100):
            token = auth.create_session(7, "owner", secret)
            self.assertEqual(auth.session_user(token, secret), {"id": 7, "username": "owner"})
            self.assertIsNone(auth.session_user(token + "x", secret))
        with patch("app.auth.time.time", return_value=100 + auth.SESSION_SECONDS + 1):
            self.assertIsNone(auth.session_user(token, secret))

    def test_settings_require_valid_auth_configuration(self):
        with patch.dict(os.environ, {
            "AUTH_USERNAME": "owner",
            "AUTH_PASSWORD_HASH": auth.hash_password("a sufficiently long test password"),
            "AUTH_SESSION_SECRET": "test-session-secret-that-is-long-enough",
            "AUTH_COOKIE_SECURE": "true",
        }, clear=False):
            settings = auth.get_settings()

        self.assertEqual(settings.legacy_username, "owner")
        self.assertTrue(settings.cookie_secure)

        with patch.dict(os.environ, {
            "AUTH_USERNAME": "",
            "AUTH_PASSWORD_HASH": "",
            "AUTH_SESSION_SECRET": "",
        }, clear=False):
            generated = auth.get_settings()
            self.assertGreaterEqual(len(generated.session_secret), 32)
            self.assertEqual(auth.get_settings().session_secret, generated.session_secret)

    def test_accounts_cannot_read_each_others_case_data(self):
        with TestClient(app) as legacy_client, TestClient(app) as second_client, TestClient(app) as anonymous_client:
            self.assertEqual(anonymous_client.get("/api/files").status_code, 401)
            login = legacy_client.post("/api/auth/login", json={
                "username": "legacy-owner",
                "password": LEGACY_PASSWORD,
            })
            self.assertEqual(login.status_code, 200)
            legacy_files = legacy_client.get("/api/files").json()
            self.assertEqual([item["id"] for item in legacy_files], [LEGACY_FILE_ID])

            registered = second_client.post("/api/auth/register", json={
                "username": "second-owner",
                "password": "synthetic second password 456",
            })
            self.assertEqual(registered.status_code, 200)
            second_user_id = registered.json()["user_id"]
            self.assertEqual(second_client.get("/api/auth/session").json()["user_id"], second_user_id)
            self.assertEqual(second_client.post("/api/auth/register", json={
                "username": "second-owner",
                "password": "synthetic second password 456",
            }).status_code, 409)
            second_file_id = db.run(
                "INSERT INTO files(user_id,name,type,path,sha256,size,uploaded_at) VALUES(?,?,?,?,?,?,?)",
                (second_user_id, "private.txt", "chat", "", "second-hash", 10, "2026-01-01T00:00:00Z"),
            )
            db.run("INSERT INTO events(file_id,date,description,chunk_ids) VALUES(?,?,?,?)",
                   (LEGACY_FILE_ID, "2026-01-01", "legacy owner's event", "[]"))
            db.run("INSERT INTO events(file_id,date,description,chunk_ids) VALUES(?,?,?,?)",
                   (second_file_id, "2026-01-02", "second owner's event", "[]"))

            self.assertEqual([item["name"] for item in second_client.get("/api/files").json()], ["private.txt"])
            self.assertEqual(legacy_client.get(f"/api/files/{second_file_id}").status_code, 404)
            self.assertEqual(second_client.get(f"/api/files/{LEGACY_FILE_ID}").status_code, 404)
            self.assertEqual(second_client.get(f"/api/files/{LEGACY_FILE_ID}/original").status_code, 404)
            self.assertEqual([event["description"] for event in legacy_client.get("/api/timeline").json()["events"]],
                             ["legacy owner's event"])
            self.assertEqual([event["description"] for event in second_client.get("/api/timeline").json()["events"]],
                             ["second owner's event"])

            audit.log("private legacy event", user_id=login.json()["user_id"])
            self.assertEqual(second_client.get("/api/audit").json(), [])
            self.assertEqual(legacy_client.get("/api/audit").json()[0]["action"], "private legacy event")

    def test_chunk_corrections_preserve_machine_text_and_update_search(self):
        with TestClient(app) as client:
            file_id, _ = self._create_ready_file(client, "transcript-correction-owner")
            user_id = client.get("/api/auth/session").json()["user_id"]
            chunk_id = db.run(
                "INSERT INTO chunks(file_id,text,ref) VALUES(?,?,?)",
                (file_id, "Machine transcript text", "case.txt"),
            )

            with patch("app.main.index.upsert") as upsert:
                response = client.put(
                    f"/api/files/{file_id}/chunks/{chunk_id}",
                    json={"text": "Human verified transcript"},
                )

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["text"], "Machine transcript text")
            self.assertEqual(response.json()["corrected_text"], "Human verified transcript")
            self.assertEqual(response.json()["effective_text"], "Human verified transcript")
            upsert.assert_called_once_with(
                [{"id": chunk_id, "file_id": file_id, "text": "Human verified transcript"}],
                user_id,
            )
            stored = db.q("SELECT text,corrected_text FROM chunks WHERE id=?", (chunk_id,))[0]
            self.assertEqual(stored, {"text": "Machine transcript text", "corrected_text": "Human verified transcript"})
            self.assertEqual(client.get(f"/api/files/{file_id}/chunks").json()[0]["effective_text"],
                             "Human verified transcript")

            with patch("app.index.collection") as collection:
                collection.return_value.query.return_value = {
                    "ids": [[str(chunk_id)]],
                    "distances": [[0.1]],
                }
                top, _, _ = index.retrieve("verified transcript", user_id)
            self.assertEqual(top[0]["text"], "Human verified transcript")
            self.assertTrue(audit.verify(user_id)["ok"])

            with patch("app.main.index.upsert"):
                restored = client.put(f"/api/files/{file_id}/chunks/{chunk_id}", json={"text": None})
            self.assertEqual(restored.status_code, 200)
            self.assertEqual(restored.json()["effective_text"], "Machine transcript text")
            self.assertIsNone(db.q("SELECT corrected_text FROM chunks WHERE id=?", (chunk_id,))[0]["corrected_text"])

    def test_failed_file_can_be_retried_without_reusing_stale_extraction(self):
        with TestClient(app) as client:
            file_id, original_path = self._create_ready_file(client, "retry-processing-owner")
            user_id = client.get("/api/auth/session").json()["user_id"]
            digest = hashlib.sha256(original_path.read_bytes()).hexdigest()
            db.run("UPDATE files SET status='failed',error='Synthetic processing failure',sha256=? WHERE id=?",
                   (digest, file_id))
            chunk_id = db.run("INSERT INTO chunks(file_id,text,ref) VALUES(?,?,?)",
                              (file_id, "Stale machine text", original_path.name))
            db.run("INSERT INTO events(file_id,date,description,chunk_ids) VALUES(?,?,?,?)",
                   (file_id, "2026-01-01", "Stale event", f"[{chunk_id}]"))
            db.run("INSERT INTO entities(file_id,name,kind) VALUES(?,?,?)", (file_id, "Stale person", "person"))

            with patch("app.main.index.remove") as remove, patch("app.main.pipeline.process") as process:
                response = client.post(f"/api/files/{file_id}/retry")

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "queued")
            remove.assert_called_once_with([chunk_id], user_id)
            process.assert_called_once_with(file_id)
            self.assertEqual(db.q("SELECT id FROM chunks WHERE file_id=?", (file_id,)), [])
            self.assertEqual(db.q("SELECT id FROM events WHERE file_id=?", (file_id,)), [])
            self.assertEqual(db.q("SELECT id FROM entities WHERE file_id=?", (file_id,)), [])

    def test_delete_file_removes_original_and_case_record(self):
        with TestClient(app) as client:
            file_id, original_path = self._create_ready_file(client, "delete-success-owner")
            os.chmod(original_path, 0o444)

            response = client.delete(f"/api/files/{file_id}")

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["name"], original_path.name)
            self.assertFalse(original_path.exists())
            self.assertEqual(client.get("/api/files").json(), [])

    def test_delete_video_removes_readonly_original_and_processing_files(self):
        with TestClient(app) as client:
            file_id, original_path = self._create_ready_file(client, "delete-video-owner")
            user_id = client.get("/api/auth/session").json()["user_id"]
            db.run("UPDATE files SET type='video' WHERE id=?", (file_id,))
            os.chmod(original_path, 0o444)
            work_path = Path(WORK) / str(user_id) / f"video_{original_path.stem}"
            work_path.mkdir(parents=True)
            (work_path / "frame.jpg").write_bytes(b"synthetic frame")

            response = client.delete(f"/api/files/{file_id}")

            self.assertEqual(response.status_code, 200)
            self.assertFalse(original_path.exists())
            self.assertFalse(work_path.exists())

    def test_delete_reports_partial_failure_without_exposing_os_path(self):
        with TestClient(app) as client:
            file_id, original_path = self._create_ready_file(client, "delete-partial-owner")
            failure_detail = f"Access is denied: {original_path}"

            with self.assertLogs("app.main", level="ERROR") as captured:
                with patch("app.main.Path.unlink", side_effect=PermissionError(failure_detail)):
                    response = client.delete(f"/api/files/{file_id}")

            self.assertEqual(response.status_code, 500)
            detail = response.json()["detail"]
            self.assertIn("removed from this case", detail)
            self.assertIn("original file could not be deleted", detail)
            self.assertNotIn("Access is denied", detail)
            self.assertNotIn(str(original_path), detail)
            self.assertEqual(client.get("/api/files").json(), [])
            staged_paths = list(original_path.parent.glob(f".delete-{file_id}-*"))
            self.assertEqual(len(staged_paths), 1)
            Path.unlink(staged_paths[0])
            self.assertIn(failure_detail, "\n".join(captured.output))

    def test_replace_file_overwrites_original_and_reprocesses_record(self):
        with TestClient(app) as client:
            file_id, original_path = self._create_ready_file(client, "replace-owner")
            chunk_id = db.run(
                "INSERT INTO chunks(file_id,text,page,msg_no,ref) VALUES(?,?,?,?,?)",
                (file_id, "old extracted text", None, None, "replace-owner.txt"),
            )
            db.run("INSERT INTO events(file_id,date,description,chunk_ids) VALUES(?,?,?,?)",
                   (file_id, "2026-01-01", "old event", f"[{chunk_id}]"))
            db.run("INSERT INTO entities(file_id,name,kind) VALUES(?,?,?)", (file_id, "Old person", "person"))
            db.run("INSERT INTO flags(user_id,kind,note,sources) VALUES(?,?,?,?)",
                   (client.get("/api/auth/session").json()["user_id"], "duplicate", "old flag",
                    f'[{{"file_id": {file_id}}}]'))

            with patch("app.main.index.remove") as remove_index, patch("app.main.pipeline.process") as process:
                response = client.post(
                    f"/api/files/{file_id}/replace",
                    files={"file": ("replacement.txt", b"New replacement evidence.", "text/plain")},
                )

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["id"], file_id)
            self.assertEqual(response.json()["name"], "replacement.txt")
            self.assertEqual(response.json()["status"], "queued")
            remove_index.assert_called_once()
            process.assert_called_once_with(file_id)
            self.assertFalse(original_path.exists())
            replaced = db.q("SELECT * FROM files WHERE id=?", (file_id,))[0]
            self.assertEqual(Path(replaced["path"]).read_text(encoding="utf-8"), "New replacement evidence.")
            self.assertEqual(db.q("SELECT id FROM chunks WHERE file_id=?", (file_id,)), [])
            self.assertEqual(db.q("SELECT id FROM events WHERE file_id=?", (file_id,)), [])
            self.assertEqual(db.q("SELECT id FROM entities WHERE file_id=?", (file_id,)), [])
            self.assertEqual(db.q("SELECT id FROM flags WHERE note='old flag'"), [])

    def test_replace_rejects_duplicate_contents_without_changing_original(self):
        with TestClient(app) as client:
            file_id, original_path = self._create_ready_file(client, "replace-duplicate-owner")
            duplicate_contents = b"Already stored in another record."
            db.run(
                "INSERT INTO files(user_id,name,type,path,sha256,size,uploaded_at,status) VALUES(?,?,?,?,?,?,?,?)",
                (client.get("/api/auth/session").json()["user_id"], "existing.txt", "chat", "",
                 hashlib.sha256(duplicate_contents).hexdigest(), len(duplicate_contents),
                 "2026-01-01T00:00:00Z", "ready"),
            )

            response = client.post(
                f"/api/files/{file_id}/replace",
                files={"file": ("replacement.txt", duplicate_contents, "text/plain")},
            )

            self.assertEqual(response.status_code, 409)
            self.assertIn("already in this case", response.json()["detail"])
            self.assertEqual(original_path.read_text(encoding="utf-8"), "Synthetic file for deletion tests.")
            self.assertEqual(db.q("SELECT name FROM files WHERE id=?", (file_id,))[0]["name"], original_path.name)

    def test_audio_and_video_uploads_complete_processing_pipeline(self):
        with TestClient(app) as client:
            registration = client.post("/api/auth/register", json={
                "username": "media-processing-owner",
                "password": "synthetic media password 123",
            })
            self.assertEqual(registration.status_code, 200)

            def fake_ffmpeg(args, capture_output):
                output = Path(args[-1])
                if output.suffix == ".wav":
                    output.write_bytes(b"w" * 1200)
                else:
                    from PIL import Image
                    Image.new("RGB", (16, 16), "white").save(output.parent / "f_0001.jpg")
                return type("FFmpegResult", (), {"returncode": 0})()

            def fake_transcribe(path, name, label="Speech", progress_callback=None):
                if progress_callback:
                    progress_callback(45)
                return ([{
                    "text": "Synthetic spoken words",
                    "start_sec": 0.2,
                    "end_sec": 1.0,
                    "speaker": label,
                    "ref": name,
                }], "en")

            with patch("app.main.pipeline.extract.shutil.which", return_value="ffmpeg"), \
                 patch("app.main.pipeline.extract.subprocess.run", side_effect=fake_ffmpeg), \
                 patch("app.main.pipeline.extract.transcribe", side_effect=fake_transcribe), \
                 patch("app.main.pipeline.extract.ocr", return_value=("Synthetic sign text", 99)), \
                 patch("app.main.pipeline.llm.ask_json", return_value={"events": [], "entities": []}), \
                 patch("app.main.pipeline.index.add"), \
                 patch("app.main.pipeline.scan_contradictions"):
                response = client.post("/api/files", files=[
                    ("files", ("synthetic-audio.wav", b"RIFF-synthetic-audio", "audio/wav")),
                    ("files", ("synthetic-video.mp4", b"synthetic-video-data", "video/mp4")),
                ])

            self.assertEqual(response.status_code, 200)
            processed = {item["name"]: item for item in client.get("/api/files").json()}
            for name, file_type in (("synthetic-audio.wav", "audio"), ("synthetic-video.mp4", "video")):
                self.assertEqual(processed[name]["type"], file_type)
                self.assertEqual(processed[name]["status"], "ready")
                self.assertEqual(processed[name]["progress"], 100)
                original_path = Path(db.q("SELECT path FROM files WHERE id=?", (processed[name]["id"],))[0]["path"])
                self.assertTrue(original_path.exists())
                chunks = client.get(f"/api/files/{processed[name]['id']}/chunks").json()
                expected = ["Synthetic spoken words"]
                if file_type == "video":
                    expected.insert(0, "[on-screen text] Synthetic sign text")
                    video_work = Path(WORK) / str(registration.json()["user_id"]) / f"video_{original_path.stem}"
                    self.assertFalse(video_work.exists())
                self.assertEqual([chunk["text"] for chunk in chunks], expected)

    def test_media_upload_size_limits_are_enforced_by_backend(self):
        with TestClient(app) as client:
            registration = client.post("/api/auth/register", json={
                "username": "media-limit-owner",
                "password": "synthetic limit password 123",
            })
            self.assertEqual(registration.status_code, 200)
            oversized_content = b"x" * (1024 * 1024 + 1)

            for file_type, name, mime in (
                ("image", "large.png", "image/png"),
                ("audio", "large.wav", "audio/wav"),
                ("video", "large.mp4", "video/mp4"),
            ):
                with patch("app.main.MAX_UPLOAD_BYTES", {file_type: 1024 * 1024}):
                    response = client.post("/api/files", files={
                        "files": (name, oversized_content, mime),
                    })
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()[0]["error"], f"Maximum {file_type} file size is 1 MB.")

            self.assertEqual(client.get("/api/files").json(), [])

    def test_audio_and_video_extractors_process_content_and_clean_work_files(self):
        audio_path = Path(TEST_STORAGE) / "synthetic-audio.wav"
        audio_path.write_bytes(b"synthetic audio")
        speech = {"text": "Synthetic spoken words", "start_sec": 0.2, "end_sec": 1.0}
        with patch("app.extract.transcribe", return_value=([speech], "en")):
            audio_chunks, audio_meta = extract.audio(audio_path, audio_path.name)
        self.assertEqual(audio_chunks, [speech])
        self.assertEqual(audio_meta["language"], "en")
        self.assertEqual(audio_meta["note"], "0:01 · language: en")

        video_path = Path(TEST_STORAGE) / "synthetic-video.mp4"
        video_path.write_bytes(b"synthetic video")
        work_path = Path(WORK) / "media-extractor-test" / f"video_{video_path.stem}"

        def fake_ffmpeg(args, capture_output):
            output = Path(args[-1])
            if output.suffix == ".wav":
                output.write_bytes(b"w" * 1200)
            else:
                from PIL import Image
                Image.new("RGB", (16, 16), "white").save(output.parent / "f_0001.jpg")
            return type("FFmpegResult", (), {"returncode": 0})()

        with patch("app.extract.shutil.which", return_value="ffmpeg"), \
             patch("app.extract.subprocess.run", side_effect=fake_ffmpeg), \
             patch("app.extract.transcribe", return_value=([speech], "en")), \
             patch("app.extract.ocr", return_value=("Synthetic sign text", 99)):
            video_chunks, video_meta = extract.video(video_path, video_path.name, "media-extractor-test")

        self.assertEqual(len(video_chunks), 2)
        self.assertEqual(video_chunks[0]["text"], "[on-screen text] Synthetic sign text")
        self.assertEqual(video_chunks[1]["text"], "Synthetic spoken words")
        self.assertEqual(video_meta["language"], "en")
        self.assertEqual(video_meta["frames_read"], 1)
        self.assertFalse(work_path.exists())

        failed_work_path = Path(WORK) / "media-extractor-failure-test" / f"video_{video_path.stem}"
        with patch("app.extract.shutil.which", return_value="ffmpeg"), \
             patch("app.extract.subprocess.run", side_effect=fake_ffmpeg), \
             patch("app.extract.transcribe", side_effect=RuntimeError("Synthetic transcription failure")):
            with self.assertRaisesRegex(RuntimeError, "Synthetic transcription failure"):
                extract.video(video_path, video_path.name, "media-extractor-failure-test")
        self.assertFalse(failed_work_path.exists())

    def test_graph_paths_follow_person_place_coappearance_only(self):
        with TestClient(app) as client:
            file_id, _ = self._create_ready_file(client, "people-place-graph-owner")
            db.run("INSERT INTO chunks(file_id,text,ref) VALUES(?,?,?)",
                   (file_id, "Alice visited Paris while working with Acme.", "case.txt line 1"))
            for name, kind in (("Alice", "person"), ("Paris", "place"), ("Acme", "org")):
                db.run("INSERT INTO entities(file_id,name,kind) VALUES(?,?,?)", (file_id, name, kind))

            graph_data = client.get("/api/graph").json()
            entity_ids = {node["label"]: node["id"] for node in graph_data["nodes"] if node["node_type"] == "entity"}
            person_place = client.post("/api/graph/path", json={
                "source_id": entity_ids["Alice"], "target_id": entity_ids["Paris"],
            })
            self.assertEqual(person_place.status_code, 200)
            self.assertTrue(person_place.json()["connected"])
            self.assertEqual([node["kind"] for node in person_place.json()["nodes"]], ["person", "place"])
            self.assertEqual([link["type"] for link in person_place.json()["links"]], ["co_occurs"])
            self.assertNotIn("document", [node["node_type"] for node in person_place.json()["nodes"]])

            unsupported_kind = client.post("/api/graph/path", json={
                "source_id": entity_ids["Alice"], "target_id": entity_ids["Acme"],
            })
            self.assertEqual(unsupported_kind.status_code, 422)

    def test_faster_whisper_audio_decode_works_with_supported_pyav(self):
        from faster_whisper.audio import decode_audio

        source = io.BytesIO()
        with wave.open(source, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(b"\x00\x00" * 16000)

        decoded = decode_audio(io.BytesIO(source.getvalue()), sampling_rate=16000)

        self.assertEqual(len(decoded), 16000)

    def test_registration_validates_password_and_username(self):
        with TestClient(app) as client:
            response = client.post("/api/auth/register", json={
                "username": "invalid name",
                "password": "short",
            })
            self.assertEqual(response.status_code, 422)

    def test_registration_works_without_a_preconfigured_owner(self):
        registration_db = Path(TEST_STORAGE) / "registration-only.sqlite"
        with patch.object(db, "DB", registration_db), patch.dict(os.environ, {
            "AUTH_USERNAME": "",
            "AUTH_PASSWORD_HASH": "",
            "AUTH_SESSION_SECRET": "",
        }, clear=False):
            db.init()
            with TestClient(app) as client:
                self.assertFalse(client.get("/api/auth/session").json()["authenticated"])
                response = client.post("/api/auth/register", json={
                    "username": "first-registrant",
                    "password": "synthetic first password 789",
                })
                self.assertEqual(response.status_code, 200)
                self.assertEqual(client.get("/api/files").json(), [])
                self.assertEqual(db.q("SELECT key FROM app_settings"), [{"key": "session_secret"}])

    def test_existing_database_is_assigned_to_bootstrap_owner(self):
        legacy_db = Path(TEST_STORAGE) / "legacy.sqlite"
        with sqlite3.connect(legacy_db) as connection:
            connection.executescript("""
                CREATE TABLE files(id INTEGER PRIMARY KEY, name TEXT);
                INSERT INTO files(id,name) VALUES(41,'legacy evidence');
                CREATE TABLE flags(id INTEGER PRIMARY KEY, kind TEXT, note TEXT, sources TEXT);
                CREATE TABLE audit(id INTEGER PRIMARY KEY, actor TEXT, action TEXT, detail TEXT, prev_hash TEXT, hash TEXT, at TEXT);
                CREATE TABLE shares(token TEXT PRIMARY KEY, expires_at TEXT);
                CREATE TABLE entity_merges(id INTEGER PRIMARY KEY);
                CREATE TABLE llm_cache(key TEXT PRIMARY KEY, value TEXT);
                INSERT INTO llm_cache(key,value) VALUES('legacy-key','legacy-value');
            """)

        with patch.object(db, "DB", legacy_db):
            db.init()
            owner_id = auth.initialize_legacy_account(auth.get_settings())
            self.assertEqual(db.q("SELECT user_id FROM files WHERE id=41")[0]["user_id"], owner_id)
            self.assertEqual(db.q("SELECT user_id FROM llm_cache WHERE key='legacy-key'")[0]["user_id"], owner_id)


if __name__ == "__main__":
    unittest.main()
