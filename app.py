import os
import hashlib
import secrets
import threading
from datetime import datetime

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

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

OPENAI_MODEL = os.environ.get(
    "OPENAI_MODEL",
    "gpt-6-luna"
)

OPENAI_REASONING_EFFORT = os.environ.get(
    "OPENAI_REASONING_EFFORT",
    "low"
)

RELAY_TOKEN = os.environ.get(
    "RELAY_TOKEN",
    ""
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
# Temporary Job Storage
# =========================================================

# 測試版：
#
# job ID
#   ↓
# response_id
#
# 例如：
#
# ABC001 → resp_xxxxx
#
# 注意：
# Render 重啟後這些資料會消失。
#
JOBS = {}

JOBS_LOCK = threading.Lock()


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


def check_token():

    # 如果沒有設定 RELAY_TOKEN
    # 暫時不驗證
    if not RELAY_TOKEN:
        return True

    token = request.args.get(
        "token",
        ""
    ).strip()

    return secrets.compare_digest(
        token,
        RELAY_TOKEN
    )


def question_hash(question):

    return hashlib.sha256(
        question.encode("utf-8")
    ).hexdigest()


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

First request:
Create background AI job

Later request:
Check same job

Completed:
Return C answer
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

    response = jsonify({

        "success": True,

        "service": "C Exam Relay",

        "architecture": "single-task-endpoint",

        "model": OPENAI_MODEL,

        "reasoning_effort": OPENAI_REASONING_EFFORT,

        "max_output_tokens": OPENAI_MAX_OUTPUT_TOKENS,

        "jobs_in_memory": len(JOBS),

        "server_time": datetime.now().isoformat()
    })

    return no_cache(response)


# =========================================================
# TASK
# =========================================================

@app.get("/task")
def task():

    # -----------------------------------------------------
    # Authentication
    # -----------------------------------------------------

    if not check_token():

        response = jsonify({
            "success": False,
            "error": "Unauthorized"
        })

        return no_cache(response), 401


    # -----------------------------------------------------
    # Parameters
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

        response = jsonify({
            "success": False,
            "error": "沒有收到 q 參數"
        })

        return no_cache(response), 400


    if not job_id:

        response = jsonify({
            "success": False,
            "error": "沒有收到 job 參數"
        })

        return no_cache(response), 400


    if len(question) > 5000:

        response = jsonify({

            "success": False,

            "error": "題目過長",

            "question_length": len(question)
        })

        return no_cache(response), 400


    if not OPENAI_API_KEY:

        response = jsonify({

            "success": False,

            "error": "OPENAI_API_KEY 未設定"
        })

        return no_cache(response), 500


    current_hash = question_hash(
        question
    )


    # =====================================================
    # Create or find existing job
    # =====================================================

    try:

        with JOBS_LOCK:

            existing_job = JOBS.get(
                job_id
            )


            # -------------------------------------------------
            # NEW JOB
            # -------------------------------------------------

            if existing_job is None:

                print("\n====================================")
                print("CREATE NEW JOB")
                print("job:", job_id)
                print("cb:", cache_buster)
                print("model:", OPENAI_MODEL)
                print("question:", question)
                print("====================================\n")


                ai_response = client.responses.create(

                    model=OPENAI_MODEL,

                    reasoning={
                        "effort": OPENAI_REASONING_EFFORT
                    },

                    instructions="""
你是一個專門處理 C 語言程式設計考題的程式設計助教。

請仔細分析題目。

要求：

1. 必須使用 C 語言。

2. 正確理解：
   - 輸入格式
   - 輸出格式
   - 限制條件
   - 邊界情況

3. 若題目需要演算法，
   選擇正確且效率合理的方法。

4. 特別注意：
   - 陣列邊界
   - 整數溢位
   - 迴圈
   - 字串
   - 指標
   - scanf
   - printf
   - 空格
   - 換行
   - 大小寫
   - 特殊符號

5. 題目要求特殊輸出時，
   必須嚴格符合格式。

6. 不可以自行加入題目沒有提供的條件。

7. 最後提供完整、
   可以直接編譯執行的 C 程式碼。

8. 不要加入無關的長篇說明。
""",

                    input=question,

                    max_output_tokens=OPENAI_MAX_OUTPUT_TOKENS,

                    background=True,

                    metadata={
                        "relay_job": job_id
                    }
                )


                JOBS[job_id] = {

                    "response_id": ai_response.id,

                    "question_hash": current_hash,

                    "question": question,

                    "created_at": datetime.now().isoformat()
                }


                response = jsonify({

                    "success": True,

                    "job": job_id,

                    "cb": cache_buster,

                    "stage": "submitted",

                    "status": ai_response.status,

                    "ready": (
                        ai_response.status == "completed"
                    ),

                    "answer_source":
                        "OPENAI_BACKGROUND_API",

                    "model": OPENAI_MODEL,

                    "reasoning_effort":
                        OPENAI_REASONING_EFFORT,

                    "verification_code":
                        "CEXAM-9527",

                    "message":
                        "AI 已開始處理。若 ready=false，請稍後用相同 job 與題目、但新的 cb 再查詢。",

                    "server_time":
                        datetime.now().isoformat()
                })


                # 如果簡單題竟然馬上完成
                if ai_response.status == "completed":

                    response = jsonify({

                        "success": True,

                        "job": job_id,

                        "cb": cache_buster,

                        "stage": "completed",

                        "status": "completed",

                        "ready": True,

                        "verification_code":
                            "CEXAM-9527",

                        "answer_source":
                            "OPENAI_BACKGROUND_API",

                        "model": ai_response.model,

                        "answer":
                            ai_response.output_text,

                        "server_time":
                            datetime.now().isoformat()
                    })


                return no_cache(response)


            # -------------------------------------------------
            # EXISTING JOB
            # -------------------------------------------------

            if (
                existing_job["question_hash"]
                != current_hash
            ):

                response = jsonify({

                    "success": False,

                    "error":
                        "這個 job 已經被另一個題目使用",

                    "job": job_id
                })

                return no_cache(response), 409


            response_id = existing_job[
                "response_id"
            ]


        # =================================================
        # Retrieve outside lock
        # =================================================

        ai_response = client.responses.retrieve(
            response_id
        )

        status = ai_response.status


        print("\n====================================")
        print("CHECK JOB")
        print("job:", job_id)
        print("cb:", cache_buster)
        print("status:", status)
        print("====================================\n")


        # -------------------------------------------------
        # Processing
        # -------------------------------------------------

        if status in {
            "queued",
            "in_progress"
        }:

            response = jsonify({

                "success": True,

                "job": job_id,

                "cb": cache_buster,

                "stage": "processing",

                "status": status,

                "ready": False,

                "answer_source":
                    "OPENAI_BACKGROUND_API",

                "model": OPENAI_MODEL,

                "message":
                    "答案仍在產生中。請使用相同 job 和 q，將 cb 換成新值後再次查詢。",

                "server_time":
                    datetime.now().isoformat()
            })

            return no_cache(response)


        # -------------------------------------------------
        # Completed
        # -------------------------------------------------

        if status == "completed":

            response = jsonify({

                "success": True,

                "job": job_id,

                "cb": cache_buster,

                "stage": "completed",

                "status": status,

                "ready": True,

                "verification_code":
                    "CEXAM-9527",

                "answer_source":
                    "OPENAI_BACKGROUND_API",

                "model":
                    ai_response.model,

                "answer":
                    ai_response.output_text,

                "server_time":
                    datetime.now().isoformat()
            })

            return no_cache(response)


        # -------------------------------------------------
        # Terminal errors
        # -------------------------------------------------

        response = jsonify({

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
        })

        return no_cache(response), 500


    except Exception as e:

        print(
            "TASK ERROR:",
            type(e).__name__,
            str(e)
        )

        response = jsonify({

            "success": False,

            "job": job_id,

            "cb": cache_buster,

            "error":
                "處理 task 時發生錯誤",

            "error_type":
                type(e).__name__,

            "server_time":
                datetime.now().isoformat()
        })

        return no_cache(response), 500


# =========================================================
# Old endpoints
# =========================================================

@app.get("/submit")
def deprecated_submit():

    response = jsonify({

        "success": False,

        "deprecated": True,

        "message":
            "請改用 /task"
    })

    return no_cache(response), 410


@app.get("/result")
def deprecated_result():

    response = jsonify({

        "success": False,

        "deprecated": True,

        "message":
            "請改用 /task"
    })

    return no_cache(response), 410


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