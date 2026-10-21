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
        raise RuntimeError("DATABASE_URL 尚未設定")

    return psycopg.connect(
        DATABASE_URL,
        connect_timeout=8
    )


def init_db():

    with get_db() as conn:
        with conn.cursor() as cur:

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

        conn.commit()


try:
    init_db()
    print("Postgres initialization successful.")

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
        "no-store, no-cache, must-revalidate, max-age=0"
    )

    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"

    return response


def json_reply(data, status_code=200):

    response = jsonify(data)

    return no_cache(response), status_code


def check_token():

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


def question_hash(question):

    return hashlib.sha256(
        question.encode("utf-8")
    ).hexdigest()


def valid_job_id(job_id):

    return bool(
        re.fullmatch(
            r"[A-Za-z0-9_-]{1,64}",
            job_id
        )
    )


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
/task
  ↓
Postgres job lookup
  ↓
OpenAI Background API
  ↓
Postgres persistence
  ↓
answer
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
                cur.execute("SELECT 1")
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

        "service": "C Exam Relay",

        "database": (
            "connected"
            if db_ok
            else "error"
        ),

        "model": OPENAI_MODEL,

        "reasoning_effort":
            OPENAI_REASONING_EFFORT,

        "max_output_tokens":
            OPENAI_MAX_OUTPUT_TOKENS,

        "server_time":
            datetime.now().isoformat()
    })


# =========================================================
# Main /task endpoint
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
    # Input
    # -----------------------------------------------------

    question = request.args.get(
        "q",
        ""
    ).strip()

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

    if not question:

        return json_reply({
            "success": False,
            "error": "沒有收到 q"
        }, 400)


    if not job_id:

        return json_reply({
            "success": False,
            "error": "沒有收到 job"
        }, 400)


    if not valid_job_id(job_id):

        return json_reply({
            "success": False,
            "error": (
                "job 只能使用英文字母、數字、"
                "_、-，長度最多 64"
            )
        }, 400)


    if len(question) > 5000:

        return json_reply({
            "success": False,
            "error": "題目過長",
            "question_length": len(question)
        }, 400)


    if not OPENAI_API_KEY:

        return json_reply({
            "success": False,
            "error": "OPENAI_API_KEY 未設定"
        }, 500)


    if not DATABASE_URL:

        return json_reply({
            "success": False,
            "error": "DATABASE_URL 未設定"
        }, 500)


    current_hash = question_hash(
        question
    )


    # =====================================================
    # Step 1:
    # Lock job + find/create OpenAI response
    # =====================================================

    try:

        with get_db() as conn:

            with conn.cursor() as cur:

                # -------------------------------------------------
                # PostgreSQL transaction-level advisory lock
                #
                # 防止 ChatGPT / crawler 同時重複打同一 job
                # 而建立多筆付費 OpenAI request
                # -------------------------------------------------

                cur.execute(
                    """
                    SELECT pg_advisory_xact_lock(
                        hashtext(%s)
                    )
                    """,
                    (job_id,)
                )


                # -------------------------------------------------
                # 查詢 job
                # -------------------------------------------------

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
                """, (job_id,))


                row = cur.fetchone()


                # =================================================
                # Existing Job
                # =================================================

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


                    # 同一個 job 不能換題目
                    if stored_hash != current_hash:

                        return json_reply({

                            "success": False,

                            "error":
                                "此 job 已經被另一個題目使用",

                            "job": job_id
                        }, 409)


                    # ---------------------------------------------
                    # 已完成，而且答案已經存在 Postgres
                    #
                    # 不必再呼叫 OpenAI
                    # ---------------------------------------------

                    if (
                        stored_status == "completed"
                        and stored_answer is not None
                    ):

                        return json_reply({

                            "success": True,

                            "job": job_id,

                            "cb": cache_buster,

                            "stage": "completed",

                            "status": "completed",

                            "ready": True,

                            "database_cached": True,

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


                # =================================================
                # New Job
                # =================================================

                else:

                    print(
                        "\n=============================="
                    )

                    print("CREATE NEW JOB")
                    print("job:", job_id)
                    print("cb:", cache_buster)
                    print("model:", OPENAI_MODEL)
                    print(
                        "reasoning:",
                        OPENAI_REASONING_EFFORT
                    )

                    print("question:")
                    print(question)

                    print(
                        "==============================\n"
                    )


                    # ---------------------------------------------
                    # OpenAI Background Response
                    # ---------------------------------------------

                    ai_response = client.responses.create(

                        model=OPENAI_MODEL,

                        reasoning={
                            "effort":
                                OPENAI_REASONING_EFFORT
                        },

                        instructions="""
你是一位專門處理困難 C 語言程式設計題目的程式設計專家。

請仔細分析題目後作答。

要求：

1. 必須使用 C 語言。

2. 完整理解：
   - 輸入格式
   - 輸出格式
   - 題目限制
   - 邊界條件

3. 對複雜題目先選擇正確演算法，
   並確認時間複雜度與記憶體需求合理。

4. 特別檢查：
   - 陣列越界
   - off-by-one
   - 整數溢位
   - signed / unsigned
   - scanf / printf 格式
   - 字串結尾
   - 指標
   - 動態記憶體
   - 遞迴深度
   - 迴圈終止條件
   - 特殊輸入
   - 空輸入
   - 最小值與最大值

5. 輸出格式必須完全符合題目要求，
   包括空格、換行、大小寫與符號。

6. 不可以自行加入題目沒有提供的限制。

7. 最終答案必須是完整、
   可以直接編譯執行的 C 程式。

8. 最終輸出只提供 C 程式碼。
   不要使用 Markdown code fence。
   不要加入額外解釋。
""",

                        input=question,

                        max_output_tokens=
                            OPENAI_MAX_OUTPUT_TOKENS,

                        background=True,

                        # 明確保存 response，
                        # 方便之後透過 ID retrieve
                        store=True,

                        metadata={
                            "relay_job": job_id
                        }
                    )


                    initial_status = (
                        ai_response.status
                        or "queued"
                    )


                    initial_answer = None
                    completed_now = False


                    if initial_status == "completed":

                        initial_answer = (
                            ai_response.output_text
                        )

                        completed_now = True


                    # ---------------------------------------------
                    # 儲存 job
                    # ---------------------------------------------

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


                    # ---------------------------------------------
                    # 如果建立後立即完成
                    # ---------------------------------------------

                    if completed_now:

                        return json_reply({

                            "success": True,

                            "job": job_id,

                            "cb": cache_buster,

                            "stage": "completed",

                            "status": "completed",

                            "ready": True,

                            "database_cached": False,

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


                    # ---------------------------------------------
                    # Background job 已建立
                    # ---------------------------------------------

                    return json_reply({

                        "success": True,

                        "job": job_id,

                        "cb": cache_buster,

                        "stage": "submitted",

                        "status": initial_status,

                        "ready": False,

                        "database_cached": False,

                        "answer_source":
                            "OPENAI_BACKGROUND_API",

                        "model":
                            OPENAI_MODEL,

                        "reasoning_effort":
                            OPENAI_REASONING_EFFORT,

                        "verification_code":
                            "CEXAM-9527",

                        "message":
                            "AI 正在處理；保持 q 與 job 不變，只修改 cb 後再次查詢。",

                        "server_time":
                            datetime.now().isoformat()
                    })


        # =================================================
        # Existing but unfinished job
        #
        # 到這裡一定有 response_id
        # =================================================

        ai_response = client.responses.retrieve(
            response_id
        )

        status = ai_response.status


        print(
            "\n=============================="
        )

        print("CHECK EXISTING JOB")
        print("job:", job_id)
        print("cb:", cache_buster)
        print("status:", status)

        print(
            "==============================\n"
        )


        # =================================================
        # Still processing
        # =================================================

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

                "success": True,

                "job": job_id,

                "cb": cache_buster,

                "stage": "processing",

                "status": status,

                "ready": False,

                "database_cached": False,

                "message":
                    "答案仍在產生中；請保持 q 與 job 不變，只換新的 cb。",

                "server_time":
                    datetime.now().isoformat()
            })


        # =================================================
        # Completed
        # =================================================

        if status == "completed":

            answer = ai_response.output_text


            # -------------------------------------------------
            # 把最終答案永久存進 Postgres
            # -------------------------------------------------

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

                "success": True,

                "job": job_id,

                "cb": cache_buster,

                "stage": "completed",

                "status": "completed",

                "ready": True,

                "database_cached": False,

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


        # =================================================
        # Terminal error state
        # =================================================

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

            "success": False,

            "job": job_id,

            "cb": cache_buster,

            "stage": "failed",

            "status": status,

            "ready": False,

            "error":
                "OpenAI response 未正常完成",

            "server_time":
                datetime.now().isoformat()

        }, 500)


    # =====================================================
    # Error
    # =====================================================

    except Exception as e:

        print(
            "TASK ERROR:",
            type(e).__name__,
            str(e)
        )


        return json_reply({

            "success": False,

            "job": job_id,

            "cb": cache_buster,

            "error":
                "處理 task 時發生錯誤",

            "error_type":
                type(e).__name__,

            "server_time":
                datetime.now().isoformat()

        }, 500)


# =========================================================
# Old URLs
# =========================================================

@app.get("/solve")
def deprecated_solve():

    return json_reply({
        "success": False,
        "deprecated": True,
        "message": "請使用 /task"
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