import os
import hmac
import hashlib
import threading
from datetime import datetime, timezone

from flask import Flask, request, jsonify, Response
from openai import OpenAI


# ============================================================
# C Exam Relay PoC
#
# Current architecture:
#
#   GET /task
#
#   First request:
#       q + job + cb + token
#           ↓
#       OpenAI Responses API
#       background=True
#           ↓
#       save:
#       job -> response_id
#
#   Later requests:
#       same q + same job + new cb
#           ↓
#       responses.retrieve(response_id)
#
#   Image question:
#       q + img + job + cb + token
#
# img can be:
#   - public https:// URL
#   - public http:// URL
#   - data:image/...;base64,...
#
# IMPORTANT:
#   Same job MUST always use the same q and img.
#
# ============================================================


app = Flask(__name__)


# ============================================================
# Environment variables
# ============================================================

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()

OPENAI_MODEL = os.environ.get(
    "OPENAI_MODEL",
    "gpt-6-luna"
).strip()

OPENAI_REASONING_EFFORT = os.environ.get(
    "OPENAI_REASONING_EFFORT",
    "low"
).strip()

try:
    OPENAI_MAX_OUTPUT_TOKENS = int(
        os.environ.get(
            "OPENAI_MAX_OUTPUT_TOKENS",
            "1800"
        )
    )
except ValueError:
    OPENAI_MAX_OUTPUT_TOKENS = 1800


RELAY_TOKEN = os.environ.get(
    "RELAY_TOKEN",
    ""
).strip()


# ============================================================
# Constants
# ============================================================

VERIFICATION_CODE = "CEXAM-9527"

MAX_QUESTION_LENGTH = 5000

MAX_JOB_LENGTH = 200


# ============================================================
# OpenAI client
# ============================================================

client = OpenAI(
    api_key=OPENAI_API_KEY
)


# ============================================================
# In-memory job storage
#
# Example:
#
# JOBS["abc123"] = {
#     "response_id": "resp_xxx",
#     "fingerprint": "...",
#     "created_at": "...",
#     "has_image": True
# }
#
# NOTE:
# Render restart = this dictionary will be cleared.
# This is acceptable for the current PoC.
# ============================================================

JOBS = {}

JOBS_LOCK = threading.Lock()


# ============================================================
# Instructions for the model
# ============================================================

C_EXAM_INSTRUCTIONS = """
你是一個專門處理 C 語言程式設計考題的程式設計助教。

你的任務是仔細分析使用者提供的 C 語言考題。

題目可能包含：
- 純文字
- C 程式碼
- 程式執行結果
- 流程圖
- 表格
- 螢幕截圖
- 題目照片
- 文字與圖片的組合

如果有圖片，請把圖片視為題目的一部分，
仔細閱讀圖片中的程式碼、數字、表格、選項與題目條件。

回答時請特別注意：

1. 必須按照 C 語言規則分析，不要把 C++、Java、Python 的規則混進來。

2. 仔細處理：
   - 變數型態
   - 整數運算
   - 浮點數運算
   - 運算子優先順序
   - 邏輯運算
   - 條件判斷
   - for / while / do-while
   - 陣列
   - 字串
   - 指標
   - 函式
   - scanf
   - printf
   - 格式化輸出
   - 記憶體
   - 邊界條件
   - overflow
   - undefined behavior

3. 如果題目要求程式最後輸出的結果，
   請逐步模擬程式，不要只憑直覺猜答案。

4. 如果題目要求撰寫程式，
   請提供完整且可以編譯的 C 程式。

5. 如果題目有輸入輸出格式，
   必須嚴格遵守指定格式。

6. 如果是選擇題，
   請明確指出正確選項，並簡短解釋原因。

7. 如果是程式碼追蹤題，
   請追蹤重要變數在迴圈或條件中的變化。

8. 如果圖片中的某個字元、數字或程式碼真的無法辨識，
   不要自行捏造內容，應明確指出不確定的位置。

9. 回答應以正確解題為第一優先。
   如果題目簡單，可以直接回答；
   如果題目複雜，請提供必要的推理與步驟。

10. 使用繁體中文回答，除非題目要求其他語言。
""".strip()


# ============================================================
# Utility functions
# ============================================================

def utc_now():
    return datetime.now(timezone.utc).isoformat()


def make_json(data, status_code=200):
    """
    Return JSON with aggressive no-cache headers.
    cb is still used by the client as an additional cache buster.
    """

    response = jsonify(data)
    response.status_code = status_code

    response.headers["Cache-Control"] = (
        "no-store, no-cache, must-revalidate, "
        "max-age=0, private"
    )

    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"

    return response


def request_fingerprint(question, image_url):
    """
    Bind one job to exactly one question + image.

    If the same job is later reused with another q or img,
    the fingerprint will be different and the request is rejected.
    """

    raw = (
        question
        + "\n---CEXAM-IMAGE---\n"
        + image_url
    )

    return hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()


def valid_image_value(image_url):
    """
    OpenAI Responses image input accepts:
      - http://
      - https://
      - data:image/... base64 data URL

    For this GET-based relay, public HTTPS URLs are strongly preferred.
    """

    if not image_url:
        return True

    lowered = image_url.lower()

    return (
        lowered.startswith("https://")
        or lowered.startswith("http://")
        or lowered.startswith("data:image/")
    )


def get_response_error_text(response):
    """
    Safely extract an OpenAI error if one exists.
    """

    try:
        error = getattr(response, "error", None)

        if error is None:
            return None

        message = getattr(error, "message", None)

        if message:
            return str(message)

        return str(error)

    except Exception:
        return None


def format_openai_response(
    response,
    job_id,
    cb,
    has_image
):
    """
    Convert an OpenAI Response object into relay JSON.
    """

    status = getattr(
        response,
        "status",
        "unknown"
    )

    model = getattr(
        response,
        "model",
        OPENAI_MODEL
    )

    base = {
        "job": job_id,
        "cb": cb,
        "status": status,
        "model": model,
        "has_image": has_image,
        "verification_code": VERIFICATION_CODE,
        "answer_source": "OPENAI_BACKGROUND_API",
        "server_time": utc_now(),
    }


    # --------------------------------------------------------
    # Still running
    # --------------------------------------------------------

    if status in {
        "queued",
        "in_progress"
    }:

        base.update({
            "success": True,
            "ready": False,
            "stage": "processing",
        })

        return make_json(
            base,
            200
        )


    # --------------------------------------------------------
    # Successfully completed
    # --------------------------------------------------------

    if status == "completed":

        try:
            answer = response.output_text or ""
        except Exception:
            answer = ""

        base.update({
            "success": True,
            "ready": True,
            "stage": "completed",
            "answer": answer,
        })

        return make_json(
            base,
            200
        )


    # --------------------------------------------------------
    # Terminal error states
    # --------------------------------------------------------

    error_text = get_response_error_text(
        response
    )

    base.update({
        "success": False,

        # ready=True here means:
        # stop polling; this job reached a terminal state.
        "ready": True,

        "stage": "terminal_error",
        "error": (
            error_text
            or f"OpenAI response ended with status: {status}"
        ),
    })

    return make_json(
        base,
        502
    )


# ============================================================
# Root
# ============================================================

@app.route("/", methods=["GET"])
def index():

    return make_json({
        "success": True,
        "service": "C Exam Relay PoC",
        "verification_code": VERIFICATION_CODE,
        "endpoint": "/task",
        "model": OPENAI_MODEL,
        "reasoning_effort": OPENAI_REASONING_EFFORT,
        "max_output_tokens": OPENAI_MAX_OUTPUT_TOKENS,
        "image_support": True,
        "usage": (
            "/task?"
            "q=QUESTION&"
            "img=OPTIONAL_IMAGE_URL&"
            "job=JOB_ID&"
            "cb=CACHE_BUSTER&"
            "token=TOKEN"
        ),
        "server_time": utc_now(),
    })


# ============================================================
# robots.txt
# ============================================================

@app.route("/robots.txt", methods=["GET"])
def robots():

    text = (
        "User-agent: *\n"
        "Disallow: /\n"
    )

    response = Response(
        text,
        mimetype="text/plain"
    )

    response.headers["Cache-Control"] = "no-store"

    return response


# ============================================================
# Old endpoints
#
# We deliberately keep these disabled so an old client cannot
# accidentally start using the previous two-endpoint design.
# ============================================================

@app.route("/submit", methods=["GET", "POST"])
def deprecated_submit():

    return make_json({
        "success": False,
        "error": (
            "/submit is deprecated. "
            "Use /task instead."
        ),
        "verification_code": VERIFICATION_CODE,
        "server_time": utc_now(),
    }, 410)


@app.route("/result", methods=["GET", "POST"])
def deprecated_result():

    return make_json({
        "success": False,
        "error": (
            "/result is deprecated. "
            "Use /task instead."
        ),
        "verification_code": VERIFICATION_CODE,
        "server_time": utc_now(),
    }, 410)


# ============================================================
# Main endpoint
# ============================================================

@app.route("/task", methods=["GET"])
def task():

    # --------------------------------------------------------
    # Read query parameters
    # --------------------------------------------------------

    question = request.args.get(
        "q",
        ""
    ).strip()

    image_url = request.args.get(
        "img",
        ""
    ).strip()

    job_id = request.args.get(
        "job",
        ""
    ).strip()

    cb = request.args.get(
        "cb",
        ""
    ).strip()

    supplied_token = request.args.get(
        "token",
        ""
    )


    # --------------------------------------------------------
    # Authentication
    # --------------------------------------------------------

    if RELAY_TOKEN:

        if not supplied_token:

            return make_json({
                "success": False,
                "ready": True,
                "error": "Missing token.",
                "verification_code": VERIFICATION_CODE,
                "server_time": utc_now(),
            }, 401)


        if not hmac.compare_digest(
            supplied_token,
            RELAY_TOKEN
        ):

            return make_json({
                "success": False,
                "ready": True,
                "error": "Invalid token.",
                "verification_code": VERIFICATION_CODE,
                "server_time": utc_now(),
            }, 403)


    # --------------------------------------------------------
    # Basic configuration check
    # --------------------------------------------------------

    if not OPENAI_API_KEY:

        return make_json({
            "success": False,
            "ready": True,
            "error": (
                "OPENAI_API_KEY is not configured."
            ),
            "verification_code": VERIFICATION_CODE,
            "server_time": utc_now(),
        }, 500)


    # --------------------------------------------------------
    # Validate question
    # --------------------------------------------------------

    if not question:

        return make_json({
            "success": False,
            "ready": True,
            "error": "Missing q.",
            "verification_code": VERIFICATION_CODE,
            "server_time": utc_now(),
        }, 400)


    if len(question) > MAX_QUESTION_LENGTH:

        return make_json({
            "success": False,
            "ready": True,
            "error": (
                f"Question too long. "
                f"Maximum is {MAX_QUESTION_LENGTH} characters."
            ),
            "verification_code": VERIFICATION_CODE,
            "server_time": utc_now(),
        }, 400)


    # --------------------------------------------------------
    # Validate job
    # --------------------------------------------------------

    if not job_id:

        return make_json({
            "success": False,
            "ready": True,
            "error": "Missing job.",
            "verification_code": VERIFICATION_CODE,
            "server_time": utc_now(),
        }, 400)


    if len(job_id) > MAX_JOB_LENGTH:

        return make_json({
            "success": False,
            "ready": True,
            "error": (
                f"job is too long. "
                f"Maximum is {MAX_JOB_LENGTH} characters."
            ),
            "verification_code": VERIFICATION_CODE,
            "server_time": utc_now(),
        }, 400)


    # --------------------------------------------------------
    # Validate image
    # --------------------------------------------------------

    if not valid_image_value(
        image_url
    ):

        return make_json({
            "success": False,
            "ready": True,
            "error": (
                "Invalid img. "
                "Use an http:// URL, https:// URL, "
                "or data:image/... URL."
            ),
            "verification_code": VERIFICATION_CODE,
            "server_time": utc_now(),
        }, 400)


    has_image = bool(image_url)


    # --------------------------------------------------------
    # Fingerprint
    #
    # q + img are both included.
    #
    # Therefore:
    #
    # job=abc + q=A + img=X
    #
    # cannot later become:
    #
    # job=abc + q=A + img=Y
    #
    # or
    #
    # job=abc + q=B + img=X
    # --------------------------------------------------------

    fingerprint = request_fingerprint(
        question,
        image_url
    )


    # --------------------------------------------------------
    # Check whether this job already exists.
    #
    # We reserve the job before calling OpenAI to avoid two
    # simultaneous requests starting two OpenAI responses.
    # --------------------------------------------------------

    should_create = False

    with JOBS_LOCK:

        existing = JOBS.get(
            job_id
        )

        if existing is None:

            JOBS[job_id] = {
                "response_id": None,
                "fingerprint": fingerprint,
                "created_at": utc_now(),
                "has_image": has_image,
                "state": "creating",
            }

            should_create = True

        else:

            # ------------------------------------------------
            # Same job, but different question/image
            # ------------------------------------------------

            if (
                existing.get("fingerprint")
                != fingerprint
            ):

                return make_json({
                    "success": False,
                    "ready": True,
                    "job": job_id,
                    "cb": cb,
                    "error": (
                        "This job ID is already bound "
                        "to a different question or image. "
                        "Use a new job ID."
                    ),
                    "verification_code": VERIFICATION_CODE,
                    "server_time": utc_now(),
                }, 409)


            response_id = existing.get(
                "response_id"
            )

            existing_has_image = bool(
                existing.get(
                    "has_image",
                    False
                )
            )


    # ========================================================
    # EXISTING JOB
    # ========================================================

    if not should_create:

        # Another request may still be in the tiny window
        # between reserving the job and receiving response.id
        # from OpenAI.

        if not response_id:

            return make_json({
                "success": True,
                "ready": False,
                "job": job_id,
                "cb": cb,
                "stage": "creating",
                "status": "creating",
                "model": OPENAI_MODEL,
                "has_image": existing_has_image,
                "verification_code": VERIFICATION_CODE,
                "answer_source": "OPENAI_BACKGROUND_API",
                "server_time": utc_now(),
            }, 200)


        # ----------------------------------------------------
        # Retrieve the existing OpenAI background response.
        # ----------------------------------------------------

        try:

            response = client.responses.retrieve(
                response_id
            )

        except Exception as exc:

            return make_json({
                "success": False,

                # This can be a temporary network/API error.
                # Keep ready=False so the caller may retry.
                "ready": False,

                "job": job_id,
                "cb": cb,
                "stage": "retrieve_error",
                "error": str(exc),
                "verification_code": VERIFICATION_CODE,
                "server_time": utc_now(),
            }, 502)


        return format_openai_response(
            response=response,
            job_id=job_id,
            cb=cb,
            has_image=existing_has_image,
        )


    # ========================================================
    # NEW JOB
    # ========================================================

    # --------------------------------------------------------
    # Build multimodal input.
    #
    # Text-only:
    #
    #   content = [
    #       input_text
    #   ]
    #
    # Image question:
    #
    #   content = [
    #       input_text,
    #       input_image
    #   ]
    # --------------------------------------------------------

    content = [
        {
            "type": "input_text",
            "text": question,
        }
    ]


    if image_url:

        content.append({
            "type": "input_image",
            "image_url": image_url,

            # For exam screenshots/code/text,
            # use high detail.
            "detail": "high",
        })


    # --------------------------------------------------------
    # Create OpenAI background Response
    # --------------------------------------------------------

    try:

        response = client.responses.create(

            model=OPENAI_MODEL,

            instructions=C_EXAM_INSTRUCTIONS,

            input=[
                {
                    "role": "user",
                    "content": content,
                }
            ],

            reasoning={
                "effort": OPENAI_REASONING_EFFORT
            },

            max_output_tokens=(
                OPENAI_MAX_OUTPUT_TOKENS
            ),

            background=True,

            metadata={
                "relay_job": job_id,
                "relay_has_image": (
                    "1"
                    if has_image
                    else "0"
                ),
            },
        )


    except Exception as exc:

        # Remove reservation so the same job may be retried
        # after a temporary API error.

        with JOBS_LOCK:

            current = JOBS.get(
                job_id
            )

            if (
                current
                and current.get("fingerprint")
                == fingerprint
                and current.get("response_id")
                is None
            ):
                JOBS.pop(
                    job_id,
                    None
                )


        return make_json({
            "success": False,
            "ready": False,
            "job": job_id,
            "cb": cb,
            "stage": "create_error",
            "error": str(exc),
            "verification_code": VERIFICATION_CODE,
            "server_time": utc_now(),
        }, 502)


    # --------------------------------------------------------
    # Save job -> response_id
    # --------------------------------------------------------

    with JOBS_LOCK:

        JOBS[job_id] = {
            "response_id": response.id,
            "fingerprint": fingerprint,
            "created_at": JOBS.get(
                job_id,
                {}
            ).get(
                "created_at",
                utc_now()
            ),
            "has_image": has_image,
            "state": "submitted",
        }


    # --------------------------------------------------------
    # The background job may already be:
    #
    # queued
    # in_progress
    # completed
    #
    # so process the response immediately.
    # --------------------------------------------------------

    return format_openai_response(
        response=response,
        job_id=job_id,
        cb=cb,
        has_image=has_image,
    )


# ============================================================
# Local development
#
# Render normally launches the app using gunicorn,
# so this block is mainly for local testing.
# ============================================================

if __name__ == "__main__":

    port = int(
        os.environ.get(
            "PORT",
            "10000"
        )
    )

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False,
    )