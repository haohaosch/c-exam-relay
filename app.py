import os
import re
import hashlib
import secrets
from datetime import datetime

import psycopg
from flask import Flask, request, jsonify, Response
from openai import OpenAI


# =========================================================
# Flask
# =========================================================

app = Flask(__name__)
app.json.ensure_ascii = False


# =========================================================
# Environment Variables
# =========================================================

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
DATABASE_URL = os.environ.get("DATABASE_URL", "")
RELAY_TOKEN = os.environ.get("RELAY_TOKEN", "")

OPENAI_MODEL = os.environ.get(
    "OPENAI_MODEL",
    "gpt-6-luna"
)

OPENAI_REASONING_EFFORT = os.environ.get(
    "OPENAI_REASONING_EFFORT",
    "low"
)

try:
    OPENAI_MAX_OUTPUT_TOKENS = int(
        os.environ.get(
            "OPENAI_MAX_OUTPUT_TOKENS",
            "1800"
        )
    )
except ValueError:
    OPENAI_MAX_OUTPUT_TOKENS = 1800


# 短題 q：
# 10000 字元以內直接接受
try:
    MAX_DIRECT_QUESTION_CHARS = int(
        os.environ.get(
            "MAX_DIRECT_QUESTION_CHARS",
            "10000"
        )
    )
except ValueError:
    MAX_DIRECT_QUESTION_CHARS = 10000


# 每個 chunk 建議不要太大，
# 避免 URL 又變得太長。
try:
    MAX_CHUNK_CHARS = int(
        os.environ.get(
            "MAX_CHUNK_CHARS",
            "500"
        )
    )
except ValueError:
    MAX_CHUNK_CHARS = 500


# 防止有人一次宣告幾千個 chunks
try:
    MAX_TOTAL_CHUNKS = int(
        os.environ.get(
            "MAX_TOTAL_CHUNKS",
            "100"
        )
    )
except ValueError:
    MAX_TOTAL_CHUNKS = 100


# =========================================================
# OpenAI
# =========================================================

client = OpenAI(
    api_key=OPENAI_API_KEY
)


# =========================================================
# Database
# =========================================================

def get_db():

    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL 尚未設定"
        )

    return psycopg.connect(
        DATABASE_URL,
        connect_timeout=8
    )


def init_db():

    with get_db() as conn:

        with conn.cursor() as cur:

            # ---------------------------------------------
            # Job table
            # ---------------------------------------------

            cur.execute("""
                CREATE TABLE IF NOT EXISTS relay_jobs (

                    job_id TEXT PRIMARY KEY,

                    response_id TEXT UNIQUE NOT NULL,

                    question_hash TEXT NOT NULL,

                    question TEXT NOT NULL,

                    model TEXT NOT NULL,

                    reasoning_effort TEXT NOT NULL,

                    status TEXT NOT NULL,

                    answer TEXT,

                    created_at TIMESTAMPTZ
                        NOT NULL DEFAULT NOW(),

                    updated_at TIMESTAMPTZ
                        NOT NULL DEFAULT NOW(),

                    completed_at TIMESTAMPTZ
                )
            """)


            cur.execute("""
                CREATE INDEX IF NOT EXISTS
                relay_jobs_status_idx
                ON relay_jobs(status)
            """)


            # ---------------------------------------------
            # Chunk table
            # ---------------------------------------------

            cur.execute("""
                CREATE TABLE IF NOT EXISTS relay_chunks (

                    job_id TEXT NOT NULL,

                    part_number INTEGER NOT NULL,

                    total_parts INTEGER NOT NULL,

                    chunk_text TEXT NOT NULL,

                    chunk_hash TEXT NOT NULL,

                    created_at TIMESTAMPTZ
                        NOT NULL DEFAULT NOW(),

                    PRIMARY KEY (
                        job_id,
                        part_number
                    )
                )
            """)


            cur.execute("""
                CREATE INDEX IF NOT EXISTS
                relay_chunks_job_idx
                ON relay_chunks(job_id)
            """)

        conn.commit()


try:

    init_db()

    print(
        "Postgres initialization successful."
    )

except Exception as e:

    print(
        "DATABASE INIT ERROR:",
        type(e).__name__,
        str(e)
    )


# =========================================================
# Helpers
# =========================================================

def no_cache(response):

    response.headers["Cache-Control"] = (
        "no-store, no-cache, "
        "must-revalidate, max-age=0"
    )

    response.headers["Pragma"] = (
        "no-cache"
    )

    response.headers["Expires"] = (
        "0"
    )

    return response


def json_reply(data, status_code=200):

    response = jsonify(data)

    return (
        no_cache(response),
        status_code
    )


def check_token():

    # PoC：
    # 如果 RELAY_TOKEN 沒設定，
    # 暫時不驗證。
    if not RELAY_TOKEN:
        return True

    provided_token = request.args.get(
        "token",
        ""
    ).strip()

    return secrets.compare_digest(
        provided_token,
        RELAY_TOKEN
    )


def text_hash(text):

    return hashlib.sha256(
        text.encode("utf-8")
    ).hexdigest()


def valid_job_id(job_id):

    return bool(
        re.fullmatch(
            r"[A-Za-z0-9_-]{1,64}",
            job_id
        )
    )


def find_missing_parts(
    existing_parts,
    total_parts
):

    existing = set(
        existing_parts
    )

    return [
        i
        for i in range(
            1,
            total_parts + 1
        )
        if i not in existing
    ]


# =========================================================
# Home
# =========================================================

@app.get("/")
def home():

    return """
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>C Exam Relay</title>
    </head>

    <body>

        <h1>C Exam Relay</h1>

        <p>Server is running.</p>

        <pre>
SHORT QUESTION:

/task
?q=QUESTION
&job=C001
&cb=1


LONG QUESTION:

/chunk
  ↓
Postgres
  ↓
/task
  ↓
OpenAI Background API
        </pre>

    </body>

    </html>
    """


# =========================================================
# robots.txt
# =========================================================

@app.get("/robots.txt")
def robots():

    content = """
User-agent: *
Allow: /

User-agent: OAI-SearchBot
Allow: /
"""

    return Response(
        content,
        mimetype="text/plain"
    )


# =========================================================
# Health
# =========================================================

@app.get("/health")
def health():

    db_ok = False

    try:

        with get_db() as conn:

            with conn.cursor() as cur:

                cur.execute(
                    "SELECT 1"
                )

                cur.fetchone()

        db_ok = True

    except Exception as e:

        print(
            "HEALTH DB ERROR:",
            type(e).__name__,
            str(e)
        )


    return json_reply({

        "success": True,

        "service":
            "C Exam Relay",

        "database":
            "connected"
            if db_ok
            else "error",

        "model":
            OPENAI_MODEL,

        "reasoning_effort":
            OPENAI_REASONING_EFFORT,

        "max_output_tokens":
            OPENAI_MAX_OUTPUT_TOKENS,

        "max_direct_question_chars":
            MAX_DIRECT_QUESTION_CHARS,

        "max_chunk_chars":
            MAX_CHUNK_CHARS,

        "max_total_chunks":
            MAX_TOTAL_CHUNKS,

        "server_time":
            datetime.now().isoformat()
    })


# =========================================================
# CHUNK UPLOAD
# =========================================================

@app.get("/chunk")
def chunk():

    # -----------------------------------------------------
    # Authentication
    # -----------------------------------------------------

    if not check_token():

        return json_reply({
            "success": False,
            "error": "Unauthorized"
        }, 401)


    # -----------------------------------------------------
    # Parameters
    # -----------------------------------------------------

    job_id = request.args.get(
        "job",
        ""
    ).strip()

    part_raw = request.args.get(
        "part",
        ""
    ).strip()

    total_raw = request.args.get(
        "total",
        ""
    ).strip()

    chunk_text = request.args.get(
        "data",
        ""
    )

    cache_buster = request.args.get(
        "cb",
        ""
    ).strip()


    # -----------------------------------------------------
    # Validation
    # -----------------------------------------------------

    if not valid_job_id(job_id):

        return json_reply({
            "success": False,
            "error":
                "job 格式不正確"
        }, 400)


    try:

        part_number = int(
            part_raw
        )

        total_parts = int(
            total_raw
        )

    except ValueError:

        return json_reply({
            "success": False,
            "error":
                "part / total 必須是整數"
        }, 400)


    if (
        part_number < 1
        or
        total_parts < 1
        or
        part_number > total_parts
    ):

        return json_reply({
            "success": False,
            "error":
                "part / total 範圍不正確"
        }, 400)


    if total_parts > MAX_TOTAL_CHUNKS:

        return json_reply({
            "success": False,
            "error":
                "chunk 數量太多",
            "max_total_chunks":
                MAX_TOTAL_CHUNKS
        }, 400)


    if not chunk_text:

        return json_reply({
            "success": False,
            "error":
                "data 不可為空"
        }, 400)


    if len(chunk_text) > MAX_CHUNK_CHARS:

        return json_reply({

            "success": False,

            "error":
                "單一 chunk 太長",

            "chunk_length":
                len(chunk_text),

            "max_chunk_chars":
                MAX_CHUNK_CHARS

        }, 400)


    current_hash = text_hash(
        chunk_text
    )


    # =====================================================
    # Save Chunk
    # =====================================================

    try:

        with get_db() as conn:

            with conn.cursor() as cur:


                # -----------------------------------------
                # 同 job 上 transaction lock
                # -----------------------------------------

                cur.execute(
                    """
                    SELECT
                        pg_advisory_xact_lock(
                            hashtext(%s)
                        )
                    """,
                    (job_id,)
                )


                # -----------------------------------------
                # 如果 job 已經送到 OpenAI，
                # 就不再允許修改 chunks。
                # -----------------------------------------

                cur.execute("""
                    SELECT response_id

                    FROM relay_jobs

                    WHERE job_id = %s
                """, (
                    job_id,
                ))

                existing_job = (
                    cur.fetchone()
                )


                # -----------------------------------------
                # 查目前 part
                # -----------------------------------------

                cur.execute("""
                    SELECT
                        total_parts,
                        chunk_hash

                    FROM relay_chunks

                    WHERE
                        job_id = %s
                        AND part_number = %s
                """, (
                    job_id,
                    part_number
                ))

                existing_chunk = (
                    cur.fetchone()
                )


                # -----------------------------------------
                # Duplicate request
                # -----------------------------------------

                if existing_chunk:

                    (
                        old_total,
                        old_hash
                    ) = existing_chunk


                    if (
                        old_total != total_parts
                    ):

                        return json_reply({

                            "success": False,

                            "error":
                                "同一 job 的 total 不一致",

                            "job":
                                job_id

                        }, 409)


                    if old_hash != current_hash:

                        return json_reply({

                            "success": False,

                            "error":
                                "相同 job + part 已存在不同內容",

                            "job":
                                job_id,

                            "part":
                                part_number

                        }, 409)


                    # 完全一樣：
                    # 視為成功 duplicate
                    duplicate = True


                else:

                    duplicate = False


                    if existing_job:

                        return json_reply({

                            "success": False,

                            "error":
                                "此 job 已開始 AI 處理，不能再新增 chunk",

                            "job":
                                job_id

                        }, 409)


                    # -------------------------------------
                    # 確認其他 chunks 的 total 一致
                    # -------------------------------------

                    cur.execute("""
                        SELECT DISTINCT
                            total_parts

                        FROM relay_chunks

                        WHERE job_id = %s
                    """, (
                        job_id,
                    ))

                    totals = [
                        row[0]
                        for row
                        in cur.fetchall()
                    ]


                    if (
                        totals
                        and
                        (
                            len(totals) != 1
                            or
                            totals[0] != total_parts
                        )
                    ):

                        return json_reply({

                            "success": False,

                            "error":
                                "同一 job 的 total 不一致",

                            "job":
                                job_id

                        }, 409)


                    # -------------------------------------
                    # Insert
                    # -------------------------------------

                    cur.execute("""
                        INSERT INTO relay_chunks (
                            job_id,
                            part_number,
                            total_parts,
                            chunk_text,
                            chunk_hash
                        )

                        VALUES (
                            %s, %s, %s, %s, %s
                        )
                    """, (

                        job_id,
                        part_number,
                        total_parts,
                        chunk_text,
                        current_hash
                    ))


                # -----------------------------------------
                # 查目前已收到哪些 parts
                # -----------------------------------------

                cur.execute("""
                    SELECT
                        part_number

                    FROM relay_chunks

                    WHERE job_id = %s

                    ORDER BY part_number
                """, (
                    job_id,
                ))


                existing_parts = [
                    row[0]
                    for row
                    in cur.fetchall()
                ]


                missing_parts = (
                    find_missing_parts(
                        existing_parts,
                        total_parts
                    )
                )


                complete = (
                    len(missing_parts) == 0
                )


            conn.commit()


        return json_reply({

            "success": True,

            "job":
                job_id,

            "part":
                part_number,

            "total":
                total_parts,

            "cb":
                cache_buster,

            "stored":
                not duplicate,

            "duplicate":
                duplicate,

            "received_parts":
                len(existing_parts),

            "complete":
                complete,

            "missing_parts":
                missing_parts,

            "server_time":
                datetime.now().isoformat()
        })


    except Exception as e:

        print(
            "CHUNK ERROR:",
            type(e).__name__,
            str(e)
        )

        return json_reply({

            "success": False,

            "job":
                job_id,

            "error":
                "儲存 chunk 時發生錯誤",

            "error_type":
                type(e).__name__,

            "server_time":
                datetime.now().isoformat()

        }, 500)


# =========================================================
# MAIN TASK
# =========================================================

@app.get("/task")
def task():

    # -----------------------------------------------------
    # Authentication
    # -----------------------------------------------------

    if not check_token():

        return json_reply({
            "success": False,
            "error": "Unauthorized"
        }, 401)


    # -----------------------------------------------------
    # Parameters
    # -----------------------------------------------------

    direct_question = request.args.get(
        "q",
        ""
    )

    job_id = request.args.get(
        "job",
        ""
    ).strip()

    cache_buster = request.args.get(
        "cb",
        ""
    ).strip()


    # -----------------------------------------------------
    # Validation
    # -----------------------------------------------------

    if not valid_job_id(job_id):

        return json_reply({
            "success": False,
            "error":
                "job 格式不正確"
        }, 400)


    if not OPENAI_API_KEY:

        return json_reply({
            "success": False,
            "error":
                "OPENAI_API_KEY 未設定"
        }, 500)


    if not DATABASE_URL:

        return json_reply({
            "success": False,
            "error":
                "DATABASE_URL 未設定"
        }, 500)


    # =====================================================
    # Determine question source
    #
    # A. q exists → direct
    # B. no q     → assemble chunks
    # =====================================================

    question = None
    source = None


    # -----------------------------------------------------
    # Direct q
    # -----------------------------------------------------

    if direct_question:

        question = (
            direct_question.strip()
        )

        source = "direct"


        if len(question) > MAX_DIRECT_QUESTION_CHARS:

            return json_reply({

                "success": False,

                "error":
                    "direct q 題目過長，請改用 /chunk",

                "question_length":
                    len(question),

                "max_direct_question_chars":
                    MAX_DIRECT_QUESTION_CHARS

            }, 413)


    # -----------------------------------------------------
    # Chunked question
    # -----------------------------------------------------

    else:

        source = "chunks"


        try:

            with get_db() as conn:

                with conn.cursor() as cur:

                    cur.execute("""
                        SELECT
                            part_number,
                            total_parts,
                            chunk_text

                        FROM relay_chunks

                        WHERE job_id = %s

                        ORDER BY part_number
                    """, (
                        job_id,
                    ))


                    rows = (
                        cur.fetchall()
                    )


        except Exception as e:

            print(
                "CHUNK READ ERROR:",
                type(e).__name__,
                str(e)
            )

            return json_reply({

                "success": False,

                "error":
                    "讀取 chunks 失敗",

                "error_type":
                    type(e).__name__

            }, 500)


        if not rows:

            return json_reply({

                "success": False,

                "stage":
                    "upload",

                "ready":
                    False,

                "error":
                    "此 job 尚未上傳任何 chunks",

                "job":
                    job_id

            }, 400)


        totals = {
            row[1]
            for row
            in rows
        }


        if len(totals) != 1:

            return json_reply({

                "success": False,

                "stage":
                    "upload",

                "ready":
                    False,

                "error":
                    "chunk total 不一致",

                "job":
                    job_id

            }, 409)


        total_parts = next(
            iter(totals)
        )


        existing_parts = [
            row[0]
            for row
            in rows
        ]


        missing_parts = (
            find_missing_parts(
                existing_parts,
                total_parts
            )
        )


        if missing_parts:

            return json_reply({

                "success": False,

                "stage":
                    "upload",

                "ready":
                    False,

                "job":
                    job_id,

                "received_parts":
                    len(existing_parts),

                "total_parts":
                    total_parts,

                "missing_parts":
                    missing_parts,

                "error":
                    "chunks 尚未完整"

            }, 400)


        # ---------------------------------------------
        # Assemble
        # ---------------------------------------------

        question = "".join(
            row[2]
            for row
            in rows
        )


    # =====================================================
    # Question ready
    # =====================================================

    current_hash = text_hash(
        question
    )


    # =====================================================
    # Lock + find/create job
    # =====================================================

    try:

        with get_db() as conn:

            with conn.cursor() as cur:


                cur.execute(
                    """
                    SELECT
                        pg_advisory_xact_lock(
                            hashtext(%s)
                        )
                    """,
                    (
                        job_id,
                    )
                )


                cur.execute("""
                    SELECT
                        response_id,
                        question_hash,
                        question,
                        model,
                        reasoning_effort,
                        status,
                        answer,
                        created_at,
                        completed_at

                    FROM relay_jobs

                    WHERE job_id = %s
                """, (
                    job_id,
                ))


                row = (
                    cur.fetchone()
                )


                # =========================================
                # Existing job
                # =========================================

                if row:

                    (
                        response_id,
                        stored_hash,
                        stored_question,
                        stored_model,
                        stored_effort,
                        stored_status,
                        stored_answer,
                        created_at,
                        completed_at
                    ) = row


                    if stored_hash != current_hash:

                        return json_reply({

                            "success": False,

                            "error":
                                "此 job 已被另一個題目使用",

                            "job":
                                job_id

                        }, 409)


                    if (
                        stored_status == "completed"
                        and
                        stored_answer is not None
                    ):

                        return json_reply({

                            "success": True,

                            "job":
                                job_id,

                            "cb":
                                cache_buster,

                            "question_source":
                                source,

                            "question_length":
                                len(stored_question),

                            "stage":
                                "completed",

                            "status":
                                "completed",

                            "ready":
                                True,

                            "database_cached":
                                True,

                            "verification_code":
                                "CEXAM-9527",

                            "answer_source":
                                "POSTGRES_CACHE",

                            "model":
                                stored_model,

                            "reasoning_effort":
                                stored_effort,

                            "answer":
                                stored_answer,

                            "job_created_at":
                                created_at.isoformat(),

                            "completed_at":
                                (
                                    completed_at.isoformat()
                                    if completed_at
                                    else None
                                ),

                            "server_time":
                                datetime.now().isoformat()
                        })


                # =========================================
                # New job
                # =========================================

                else:

                    print(
                        "\n============================"
                    )

                    print("CREATE NEW JOB")
                    print("job:", job_id)
                    print(
                        "source:",
                        source
                    )
                    print(
                        "question length:",
                        len(question)
                    )
                    print(
                        "model:",
                        OPENAI_MODEL
                    )
                    print(
                        "reasoning:",
                        OPENAI_REASONING_EFFORT
                    )

                    print(
                        "============================\n"
                    )


                    ai_response = (
                        client.responses.create(

                            model=
                                OPENAI_MODEL,

                            reasoning={
                                "effort":
                                    OPENAI_REASONING_EFFORT
                            },

                            instructions="""
你是一位專門處理困難 C 語言程式設計題目的程式設計專家。

請仔細分析題目。

要求：

1. 必須使用 C 語言。

2. 完整理解輸入、輸出、限制條件與邊界情況。

3. 對複雜題目選擇正確且效率足夠的演算法。

4. 必須考慮時間限制與記憶體限制。

5. 特別檢查：
   - 陣列越界
   - off-by-one
   - 整數溢位
   - signed / unsigned
   - scanf / printf
   - 字串
   - 指標
   - 動態記憶體
   - 遞迴深度
   - self-loop
   - multigraph
   - 特殊輸入
   - 最小值
   - 最大值

6. 輸出格式必須完全符合題目。

7. 不可自行加入題目沒有提供的限制。

8. 最終只輸出完整、可以直接編譯執行的 C 程式碼。

9. 不要使用 Markdown code fence。

10. 不要加入額外解釋。
""",

                            input=
                                question,

                            max_output_tokens=
                                OPENAI_MAX_OUTPUT_TOKENS,

                            background=
                                True,

                            store=
                                True,

                            metadata={
                                "relay_job":
                                    job_id
                            }
                        )
                    )


                    initial_status = (
                        ai_response.status
                        or
                        "queued"
                    )


                    initial_answer = None
                    completed_now = False


                    if (
                        initial_status
                        == "completed"
                    ):

                        initial_answer = (
                            ai_response.output_text
                        )

                        completed_now = True


                    cur.execute("""
                        INSERT INTO relay_jobs (

                            job_id,

                            response_id,

                            question_hash,

                            question,

                            model,

                            reasoning_effort,

                            status,

                            answer,

                            completed_at

                        )

                        VALUES (

                            %s, %s, %s, %s,
                            %s, %s, %s, %s,

                            CASE
                                WHEN %s
                                THEN NOW()
                                ELSE NULL
                            END

                        )
                    """, (

                        job_id,

                        ai_response.id,

                        current_hash,

                        question,

                        OPENAI_MODEL,

                        OPENAI_REASONING_EFFORT,

                        initial_status,

                        initial_answer,

                        completed_now
                    ))


                    if completed_now:

                        return json_reply({

                            "success":
                                True,

                            "job":
                                job_id,

                            "cb":
                                cache_buster,

                            "question_source":
                                source,

                            "question_length":
                                len(question),

                            "stage":
                                "completed",

                            "status":
                                "completed",

                            "ready":
                                True,

                            "database_cached":
                                False,

                            "verification_code":
                                "CEXAM-9527",

                            "answer_source":
                                "OPENAI_BACKGROUND_API",

                            "model":
                                ai_response.model,

                            "reasoning_effort":
                                OPENAI_REASONING_EFFORT,

                            "answer":
                                initial_answer,

                            "server_time":
                                datetime.now().isoformat()
                        })


                    return json_reply({

                        "success":
                            True,

                        "job":
                            job_id,

                        "cb":
                            cache_buster,

                        "question_source":
                            source,

                        "question_length":
                            len(question),

                        "stage":
                            "submitted",

                        "status":
                            initial_status,

                        "ready":
                            False,

                        "database_cached":
                            False,

                        "answer_source":
                            "OPENAI_BACKGROUND_API",

                        "model":
                            OPENAI_MODEL,

                        "reasoning_effort":
                            OPENAI_REASONING_EFFORT,

                        "verification_code":
                            "CEXAM-9527",

                        "message":
                            "AI 正在處理；保持 job 與題目不變，只修改 cb。",

                        "server_time":
                            datetime.now().isoformat()
                    })


        # =================================================
        # Existing unfinished job
        # =================================================

        ai_response = (
            client.responses.retrieve(
                response_id
            )
        )

        status = ai_response.status


        # -------------------------------------------------
        # Processing
        # -------------------------------------------------

        if status in {
            "queued",
            "in_progress"
        }:

            with get_db() as conn:

                with conn.cursor() as cur:

                    cur.execute("""
                        UPDATE relay_jobs

                        SET
                            status = %s,
                            updated_at = NOW()

                        WHERE job_id = %s
                    """, (
                        status,
                        job_id
                    ))

                conn.commit()


            return json_reply({

                "success":
                    True,

                "job":
                    job_id,

                "cb":
                    cache_buster,

                "question_source":
                    source,

                "stage":
                    "processing",

                "status":
                    status,

                "ready":
                    False,

                "message":
                    "答案仍在產生；保持 job 不變，只換 cb。",

                "server_time":
                    datetime.now().isoformat()
            })


        # -------------------------------------------------
        # Completed
        # -------------------------------------------------

        if status == "completed":

            answer = (
                ai_response.output_text
            )


            with get_db() as conn:

                with conn.cursor() as cur:

                    cur.execute("""
                        UPDATE relay_jobs

                        SET
                            status = 'completed',
                            answer = %s,
                            model = %s,
                            updated_at = NOW(),
                            completed_at = NOW()

                        WHERE job_id = %s
                    """, (

                        answer,

                        ai_response.model,

                        job_id
                    ))

                conn.commit()


            return json_reply({

                "success":
                    True,

                "job":
                    job_id,

                "cb":
                    cache_buster,

                "question_source":
                    source,

                "stage":
                    "completed",

                "status":
                    "completed",

                "ready":
                    True,

                "database_cached":
                    False,

                "verification_code":
                    "CEXAM-9527",

                "answer_source":
                    "OPENAI_BACKGROUND_API",

                "model":
                    ai_response.model,

                "reasoning_effort":
                    OPENAI_REASONING_EFFORT,

                "answer":
                    answer,

                "server_time":
                    datetime.now().isoformat()
            })


        # -------------------------------------------------
        # Other terminal state
        # -------------------------------------------------

        with get_db() as conn:

            with conn.cursor() as cur:

                cur.execute("""
                    UPDATE relay_jobs

                    SET
                        status = %s,
                        updated_at = NOW()

                    WHERE job_id = %s
                """, (
                    status,
                    job_id
                ))

            conn.commit()


        return json_reply({

            "success":
                False,

            "job":
                job_id,

            "cb":
                cache_buster,

            "stage":
                "failed",

            "status":
                status,

            "ready":
                False,

            "error":
                "OpenAI response 未正常完成",

            "server_time":
                datetime.now().isoformat()

        }, 500)


    except Exception as e:

        print(
            "TASK ERROR:",
            type(e).__name__,
            str(e)
        )

        return json_reply({

            "success":
                False,

            "job":
                job_id,

            "cb":
                cache_buster,

            "error":
                "處理 task 時發生錯誤",

            "error_type":
                type(e).__name__,

            "server_time":
                datetime.now().isoformat()

        }, 500)


# =========================================================
# Old endpoints
# =========================================================

@app.get("/solve")
def deprecated_solve():

    return json_reply({
        "success": False,
        "deprecated": True,
        "message": "請使用 /task 或 /chunk"
    }, 410)


@app.get("/submit")
def deprecated_submit():

    return json_reply({
        "success": False,
        "deprecated": True,
        "message": "請使用 /task"
    }, 410)


@app.get("/result")
def deprecated_result():

    return json_reply({
        "success": False,
        "deprecated": True,
        "message": "請使用 /task"
    }, 410)


# =========================================================
# Local
# =========================================================

if __name__ == "__main__":

    port = int(
        os.environ.get(
            "PORT",
            5000
        )
    )

    app.run(
        host="0.0.0.0",
        port=port
    )