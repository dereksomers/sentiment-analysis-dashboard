import csv
import io
import re
from datetime import date, datetime, timezone
from decimal import Decimal

from flask import Blueprint, abort, current_app, g, request, session as login_session
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.exceptions import BadRequest

from .models import Dataset, SentimentResult, TextSample, Topic, User

api = Blueprint("api", __name__, url_prefix="/api")
EMAIL_PATTERN = re.compile(r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+")
CSV_MAX_BYTES = 5 * 1024 * 1024
CSV_MAX_ROWS = 10_000


def sessions():
    return current_app.extensions["db_sessions"]


def serialize(row):
    data = {}
    for column in row.__table__.columns:
        value = getattr(row, column.key)
        if isinstance(value, datetime):
            value = value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        elif isinstance(value, date):
            value = value.isoformat()
        elif isinstance(value, Decimal):
            value = float(value)
        data[column.key] = value
    return data


def fields(data, allowed, required=()):
    if not isinstance(data, dict):
        abort(400, "Expected a JSON object.")
    if data.keys() - set(allowed):
        abort(400, "The request contains unsupported fields.")
    if set(required) - data.keys():
        abort(400, "The request is missing required fields.")
    return data


def nonblank(value, name, max_length=None):
    if not isinstance(value, str) or not value.strip():
        abort(400, f"{name} must be a nonblank string.")
    if max_length is not None and len(value.strip()) > max_length:
        abort(400, f"{name} must be at most {max_length} characters.")
    return value


def credentials():
    data = fields(request.get_json(), ("email", "password"), ("email", "password"))
    email = nonblank(data["email"], "email", 254).strip().lower()
    if not EMAIL_PATTERN.fullmatch(email):
        abort(400, "Enter a valid email address.")
    password = data["password"]
    if not isinstance(password, str) or not 12 <= len(password) <= 128:
        abort(400, "Password must be 12–128 characters.")
    try:
        password.encode("utf-8")
    except UnicodeEncodeError:
        abort(400, "Password must contain valid Unicode characters.")
    return email, password


def public_user(user):
    return {"id": user.id, "email": user.email}


@api.before_request
def load_user():
    if request.endpoint in {"api.register", "api.login"}:
        return
    user_id = login_session.get("user_id")
    if user_id is not None:
        with sessions().begin() as session:
            user = session.get(User, user_id)
            if user is not None:
                g.user = public_user(user)
                return
    login_session.clear()
    abort(401, "Sign in to continue.")


@api.after_request
def prevent_caching(response):
    response.headers["Cache-Control"] = "no-store"
    return response


@api.post("/auth/register")
def register():
    email, password = credentials()
    user = User(email=email, password_hash=generate_password_hash(password))
    with sessions().begin() as session:
        session.add(user)
        session.flush()
        return public_user(user), 201


@api.post("/auth/login")
def login():
    email, password = credentials()
    with sessions().begin() as session:
        user = session.scalar(select(User).where(User.email == email))
        if user is None or not check_password_hash(user.password_hash, password):
            abort(401, "Invalid email or password.")
        profile = public_user(user)
    login_session.clear()
    login_session["user_id"] = profile["id"]
    return profile


@api.delete("/auth/session")
def logout():
    login_session.clear()
    return "", 204


@api.get("/me")
def me():
    return g.user


def sample_fields(data, creating=False):
    data = fields(data, ("text", "source_date"), ("text",) if creating else ()).copy()
    if "text" in data:
        nonblank(data["text"], "text")
    if data.get("source_date") is not None:
        try:
            parsed = date.fromisoformat(data["source_date"])
            if parsed.isoformat() != data["source_date"]:
                raise ValueError
        except (TypeError, ValueError):
            abort(400, "source_date must be YYYY-MM-DD or null.")
        data["source_date"] = parsed
    return data


def csv_samples(upload):
    content = upload.read(CSV_MAX_BYTES + 1)
    if len(content) > CSV_MAX_BYTES:
        abort(413, "CSV file must be at most 5 MiB.")
    try:
        decoded = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        abort(400, "CSV file must use UTF-8 encoding.")
    if "\x00" in decoded:
        abort(400, "CSV file must not contain null characters.")

    reader = csv.DictReader(io.StringIO(decoded, newline=""), strict=True)
    rows = []
    try:
        headers = reader.fieldnames or []
        if (
            "text" not in headers
            or set(headers) - {"text", "source_date"}
            or len(headers) != len(set(headers))
        ):
            abort(400, "CSV header must contain text and optionally source_date, once each.")
        for row in reader:
            if len(rows) >= CSV_MAX_ROWS:
                abort(400, "CSV file must contain at most 10,000 data rows.")
            if None in row or None in row.values():
                abort(400, f"CSV line {reader.line_num} has a different column count from the header.")
            if row.get("source_date") == "":
                row["source_date"] = None
            try:
                rows.append(sample_fields(row, creating=True))
            except BadRequest as error:
                abort(400, f"CSV line {reader.line_num}: {error.description}")
    except csv.Error:
        abort(400, f"CSV parsing failed near line {reader.line_num}.")
    if not rows:
        abort(400, "CSV file must contain at least one data row.")
    return rows


def topic_fields(creating=False):
    data = fields(
        request.get_json(), ("name", "matching_phrases"), ("name",) if creating else ()
    ).copy()
    if "name" in data:
        data["name"] = nonblank(data["name"], "name", 200).strip()
    if "matching_phrases" in data:
        phrases = data["matching_phrases"]
        if not isinstance(phrases, list):
            abort(400, "matching_phrases must be a list of strings.")
        data["matching_phrases"] = [
            nonblank(phrase, "Each matching phrase").strip() for phrase in phrases
        ]
    return data


def find(session, model, row_id, dataset_id=None):
    row = session.get(model, row_id)
    if row is None or (dataset_id is not None and row.dataset_id != dataset_id):
        abort(404, "Resource not found.")
    return row


def owned_datasets():
    return select(Dataset).where(Dataset.owner_id == g.user["id"])


def find_owned_dataset(session, dataset_id):
    row = session.scalar(owned_datasets().where(Dataset.id == dataset_id))
    if row is None:
        abort(404, "Resource not found.")
    return row


def page(session, query, order_by):
    limit = request.args.get("limit", "50")
    offset = request.args.get("offset", "0")
    try:
        limit, offset = int(limit), int(offset)
    except ValueError:
        abort(400, "limit and offset must be integers.")
    if not 1 <= limit <= 100 or offset < 0:
        abort(400, "limit must be 1–100 and offset must be nonnegative.")
    total = session.scalar(select(func.count()).select_from(query.subquery()))
    rows = session.scalars(query.order_by(order_by).limit(limit).offset(offset))
    return {
        "items": [serialize(row) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@api.post("/datasets")
def create_dataset():
    data = fields(request.get_json(), ("name",), ("name",))
    name = nonblank(data["name"], "name", 200).strip()
    with sessions().begin() as session:
        row = Dataset(name=name, owner_id=g.user["id"])
        session.add(row)
        session.flush()
        return serialize(row), 201


@api.get("/datasets")
def list_datasets():
    with sessions().begin() as session:
        return page(session, owned_datasets(), Dataset.id.desc())


@api.route("/datasets/<int:dataset_id>", methods=["GET", "PATCH", "DELETE"])
def dataset_detail(dataset_id):
    with sessions().begin() as session:
        row = find_owned_dataset(session, dataset_id)
        if request.method == "DELETE":
            session.delete(row)
            return "", 204
        if request.method == "PATCH":
            data = fields(request.get_json(), ("name",), ("name",))
            row.name = nonblank(data["name"], "name", 200).strip()
        return serialize(row)


@api.route("/datasets/<int:dataset_id>/samples", methods=["GET", "POST"])
def samples(dataset_id):
    with sessions().begin() as session:
        find_owned_dataset(session, dataset_id)
        if request.method == "POST":
            data = fields(request.get_json(), ("samples",), ("samples",))
            batch = data["samples"]
            if not isinstance(batch, list) or not batch:
                abort(400, "samples must be a nonempty list.")
            rows = [
                TextSample(dataset_id=dataset_id, **sample_fields(item, creating=True))
                for item in batch
            ]
            session.add_all(rows)
            return {"dataset_id": dataset_id, "created_count": len(rows)}, 201

        query = select(TextSample).where(TextSample.dataset_id == dataset_id)
        return page(session, query, TextSample.id)


@api.post("/datasets/<int:dataset_id>/samples/import")
def import_samples(dataset_id):
    # Allow multipart framing in addition to the file's 5 MiB limit.
    request.max_content_length = CSV_MAX_BYTES + 1024 * 1024
    with sessions().begin() as session:
        find_owned_dataset(session, dataset_id)
        uploads = request.files.getlist("file")
        if len(uploads) != 1 or not uploads[0].filename:
            abort(400, "Upload one CSV file in the file form field.")
        rows = csv_samples(uploads[0])
        session.add_all(TextSample(dataset_id=dataset_id, **row) for row in rows)
        return {"dataset_id": dataset_id, "created_count": len(rows)}, 201


@api.route(
    "/datasets/<int:dataset_id>/samples/<int:sample_id>",
    methods=["GET", "PATCH", "DELETE"],
)
def sample_detail(dataset_id, sample_id):
    with sessions().begin() as session:
        find_owned_dataset(session, dataset_id)
        row = find(session, TextSample, sample_id, dataset_id)
        if request.method == "DELETE":
            session.delete(row)
            return "", 204
        if request.method == "PATCH":
            data = sample_fields(request.get_json())
            if "text" in data and data["text"] != row.text:
                session.execute(delete(SentimentResult).where(
                    SentimentResult.sample_id == sample_id
                ))
            for key, value in data.items():
                setattr(row, key, value)
        results = session.scalars(
            select(SentimentResult)
            .where(SentimentResult.sample_id == sample_id)
            .order_by(SentimentResult.topic_id)
        )
        return {**serialize(row), "sentiment_results": [serialize(r) for r in results]}


@api.route("/datasets/<int:dataset_id>/topics", methods=["GET", "POST"])
def topics(dataset_id):
    with sessions().begin() as session:
        find_owned_dataset(session, dataset_id)
        if request.method == "POST":
            row = Topic(dataset_id=dataset_id, **topic_fields(creating=True))
            session.add(row)
            session.flush()
            return serialize(row), 201
        query = select(Topic).where(Topic.dataset_id == dataset_id)
        return page(session, query, Topic.id)


@api.route(
    "/datasets/<int:dataset_id>/topics/<int:topic_id>",
    methods=["GET", "PATCH", "DELETE"],
)
def topic_detail(dataset_id, topic_id):
    with sessions().begin() as session:
        find_owned_dataset(session, dataset_id)
        row = find(session, Topic, topic_id, dataset_id)
        if request.method == "DELETE":
            session.delete(row)
            return "", 204
        if request.method == "PATCH":
            data = topic_fields()
            if any(getattr(row, key) != value for key, value in data.items()):
                session.execute(delete(SentimentResult).where(
                    SentimentResult.topic_id == topic_id
                ))
                for key, value in data.items():
                    setattr(row, key, value)
        return serialize(row)


@api.get("/datasets/<int:dataset_id>/results")
def list_results(dataset_id):
    with sessions().begin() as session:
        find_owned_dataset(session, dataset_id)
        query = select(SentimentResult).where(SentimentResult.dataset_id == dataset_id)
        if "topic_id" in request.args:
            topic_id = request.args.get("topic_id", type=int)
            if topic_id is None or topic_id < 1:
                abort(400, "topic_id must be a positive integer.")
            find(session, Topic, topic_id, dataset_id)
            query = query.where(SentimentResult.topic_id == topic_id)
        return page(session, query, SentimentResult.id)


@api.get("/datasets/<int:dataset_id>/results/<int:result_id>")
def result_detail(dataset_id, result_id):
    with sessions().begin() as session:
        find_owned_dataset(session, dataset_id)
        return serialize(find(session, SentimentResult, result_id, dataset_id))


@api.errorhandler(IntegrityError)
def integrity_error(error):
    constraint = getattr(getattr(error.orig, "diag", None), "constraint_name", None)
    if constraint == "uq_users_email":
        return {"error": {
            "code": "duplicate_email",
            "message": "An account already uses that email address.",
        }}, 409
    if constraint == "uq_topics_dataset_normalized_name":
        return {"error": {
            "code": "duplicate_topic",
            "message": "That topic name already exists in this dataset.",
        }}, 409
    current_app.logger.error("Database constraint rejected the request: %s", constraint)
    return {"error": {
        "code": "integrity_error",
        "message": "The requested change conflicts with a database constraint.",
    }}, 409
