import io
import os
import unittest
from decimal import Decimal
from uuid import uuid4
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.engine import make_url
from sqlalchemy.schema import CreateSchema, DropSchema

from backend.app import create_app
from backend.app.models import SentimentResult, User


@unittest.skipUnless(os.environ.get("TEST_DATABASE_URL"), "Set TEST_DATABASE_URL")
class AccountAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = make_url(os.environ["TEST_DATABASE_URL"])
        schema = f"test_accounts_{uuid4().hex}"
        engine = create_engine(url)
        cls.addClassCleanup(engine.dispose)
        with engine.begin() as connection:
            connection.execute(CreateSchema(schema))

        def drop_schema():
            with engine.begin() as connection:
                connection.execute(DropSchema(schema, cascade=True))

        cls.addClassCleanup(drop_schema)
        test_url = url.update_query_dict({"options": f"-csearch_path={schema}"})
        cls.app = create_app(test_url, secret_key="test-only-session-key")
        cls.app.config.update(TESTING=True)
        cls.sessions = cls.app.extensions["db_sessions"]
        cls.addClassCleanup(cls.sessions.kw["bind"].dispose)
        result = cls.app.test_cli_runner().invoke(args=["init-db"])
        if result.exception:
            raise result.exception

    def register_and_login(self, client, email):
        credentials = {"email": email, "password": "example-test-password"}
        registered = client.post("/api/auth/register", json=credentials)
        self.assertEqual(registered.status_code, 201, registered.json)
        login = client.post("/api/auth/login", json=credentials)
        self.assertEqual(login.status_code, 200, login.json)
        return login.json

    def test_account_lifecycle(self):
        client = self.app.test_client(use_cookies=True)
        self.assertEqual(client.get("/api/me").status_code, 401)
        self.assertEqual(client.get("/api/datasets").status_code, 401)
        self.assertEqual(client.post("/api/auth/register", json={
            "email": "lifecycle@example.test", "password": "short",
        }).status_code, 400)

        profile = self.register_and_login(client, " Lifecycle@Example.test ")
        self.assertEqual(profile, {"id": profile["id"], "email": "lifecycle@example.test"})
        self.assertEqual(client.get("/api/me").json, profile)
        with self.sessions() as session:
            user = session.get(User, profile["id"])
            self.assertTrue(user.password_hash.startswith("scrypt:"))

        self.assertEqual(client.post("/api/auth/register", json={
            "email": "LIFECYCLE@example.test", "password": "different-test-password",
        }).status_code, 409)
        self.assertEqual(client.delete("/api/auth/session").status_code, 204)
        self.assertEqual(client.get("/api/me").status_code, 401)
        self.assertEqual(client.post("/api/auth/login", json={
            "email": profile["email"], "password": "incorrect-password",
        }).status_code, 401)
        self.assertEqual(client.get("/api/me").status_code, 401)

    def test_dataset_and_nested_routes_are_private(self):
        owner = self.app.test_client()
        other = self.app.test_client()
        profile = self.register_and_login(owner, "owner@example.test")
        self.register_and_login(other, "other@example.test")
        created = owner.post("/api/datasets", json={"name": "Private"})
        self.assertEqual(created.status_code, 201, created.json)
        self.assertEqual(created.json["owner_id"], profile["id"])
        dataset_id = created.json["id"]
        path = f"/api/datasets/{dataset_id}"
        owner.post(f"{path}/samples", json={"samples": [{"text": "Delivery was quick"}]})
        sample_id = owner.get(f"{path}/samples").json["items"][0]["id"]
        topic_id = owner.post(f"{path}/topics", json={"name": "Delivery"}).json["id"]
        with self.sessions.begin() as session:
            result = SentimentResult(
                dataset_id=dataset_id, sample_id=sample_id, topic_id=topic_id,
                label="positive", confidence=Decimal("0.95"), model_name="test",
                evidence_text="Delivery was quick",
            )
            session.add(result)
            session.flush()
            result_id = result.id

        self.assertEqual(other.get("/api/datasets").json["items"], [])
        self.assertEqual(other.post("/api/datasets", json={
            "name": "Override", "owner_id": profile["id"],
        }).status_code, 400)
        for resource in (
            path, f"{path}/samples", f"{path}/samples/{sample_id}", f"{path}/topics",
            f"{path}/topics/{topic_id}", f"{path}/results", f"{path}/results/{result_id}",
        ):
            with self.subTest(resource=resource):
                self.assertEqual(owner.get(resource).status_code, 200)
                self.assertEqual(other.get(resource).status_code, 404)

        for method, resource, payload in (
            ("PATCH", path, {"name": "Override"}),
            ("POST", f"{path}/samples", {"samples": [{"text": "Override"}]}),
            ("PATCH", f"{path}/samples/{sample_id}", {"text": "Override"}),
            ("POST", f"{path}/topics", {"name": "Override"}),
            ("PATCH", f"{path}/topics/{topic_id}", {"name": "Override"}),
            ("DELETE", f"{path}/samples/{sample_id}", None),
            ("DELETE", f"{path}/topics/{topic_id}", None),
            ("DELETE", path, None),
        ):
            with self.subTest(method=method, resource=resource):
                response = other.open(resource, method=method, json=payload)
                self.assertEqual(response.status_code, 404)

        response = other.post(f"{path}/samples/import", data={
            "file": (io.BytesIO(b"text\nPrivate import\n"), "feedback.csv"),
        })
        self.assertEqual(response.status_code, 404)

        self.assertEqual(owner.get(f"{path}/results").json["total"], 1)
        self.assertEqual(owner.delete(path).status_code, 204)
        with self.sessions() as session:
            self.assertIsNone(session.scalar(select(SentimentResult).where(
                SentimentResult.id == result_id,
            )))

    def csv_dataset(self):
        client = self.app.test_client()
        self.register_and_login(client, f"{uuid4().hex}@example.test")
        dataset = client.post("/api/datasets", json={"name": "CSV import"}).json
        return client, f"/api/datasets/{dataset['id']}/samples"

    def test_csv_import_preserves_text_and_optional_dates(self):
        client, path = self.csv_dataset()
        content = '\ufefftext,source_date\r\n"Fast, friendly delivery",2026-11-22\r\n"Great\npackaging",\r\n'
        response = client.post(f"{path}/import", data={
            "file": (io.BytesIO(content.encode("utf-8")), "feedback.csv"),
        })
        self.assertEqual(response.status_code, 201, response.json)
        self.assertEqual(response.json["created_count"], 2)
        samples = client.get(path).json["items"]
        self.assertEqual(samples[0]["text"], "Fast, friendly delivery")
        self.assertEqual(samples[0]["source_date"], "2026-11-22")
        self.assertEqual(samples[1]["text"], "Great\npackaging")
        self.assertIsNone(samples[1]["source_date"])
        response = client.post(f"{path}/import", data={
            "file": (io.BytesIO(b"text\nOnly text\n"), "text.csv"),
        })
        self.assertEqual(response.status_code, 201, response.json)
        self.assertEqual(client.get(path).json["total"], 3)

    def test_csv_validation_is_atomic(self):
        client, path = self.csv_dataset()
        invalid_files = (
            b"",
            b"text\n",
            b"message\nWrong header\n",
            b"text,text\nDuplicate,header\n",
            b"text,source_date\nGood,2026-11-22\nBad,2026-02-30\n",
            b"text\nGood\n   \n",
            b'text\nGood\n"Unclosed quote\n',
            b"text\nToo,many,columns\n",
            b"text,source_date\nMissing column\n",
            b"text\n\xff\n",
            b"text\nContains\x00null\n",
        )
        for content in invalid_files:
            with self.subTest(content=content):
                response = client.post(f"{path}/import", data={
                    "file": (io.BytesIO(content), "invalid.csv"),
                })
                self.assertEqual(response.status_code, 400, response.json)
                self.assertEqual(client.get(path).json["total"], 0)
        response = client.post(f"{path}/import", data={
            "file": (io.BytesIO(b"text,source_date\nGood,2026-11-22\nBad,nope\n"), "invalid.csv"),
        })
        self.assertIn("CSV line 3", response.json["error"]["message"])

    def test_csv_upload_requires_authentication_and_a_file(self):
        client, path = self.csv_dataset()
        self.assertEqual(client.post(f"{path}/import").status_code, 400)
        anonymous = self.app.test_client()
        self.assertEqual(anonymous.post(f"{path}/import").status_code, 401)

    def test_csv_upload_limits(self):
        client, path = self.csv_dataset()
        response = client.post(f"{path}/import", data={
            "file": (io.BytesIO(b"x" * (5 * 1024 * 1024 + 1)), "large.csv"),
        })
        self.addCleanup(response.request.input_stream.close)
        self.assertEqual(response.status_code, 413, response.json)
        response = client.post(f"{path}/import", data={
            "file": (io.BytesIO(b"x" * (7 * 1024 * 1024)), "large.csv"),
        })
        self.addCleanup(response.request.input_stream.close)
        self.assertEqual(response.status_code, 413, response.json)
        with patch("backend.app.routes.CSV_MAX_ROWS", 1):
            response = client.post(f"{path}/import", data={
                "file": (io.BytesIO(b"text\nFirst\nSecond\n"), "rows.csv"),
            })
        self.assertEqual(response.status_code, 400, response.json)
        self.assertEqual(client.get(path).json["total"], 0)
